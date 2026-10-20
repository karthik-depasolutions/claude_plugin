"""Check 7 - MCP smoke test.

Spawns the generic `mis-mcp-runtime` server over stdio against one
customer's actual generated `config/*.json`, exactly as a Claude Code
session would, and calls every tool through a real MCP client. This is the
same mechanism M6's own stdio integration test exercises; here it runs as
the last gate before packaging, against real compiled KPI ids.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from forge_core.generation.constants import TOOL_NAMES
from forge_core.ingestion.credentials import resolve_env
from forge_core.models.common import CheckStatus
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.validation import ValidationCheckResult, ValidationIssue

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _plugin_connection_credentials(config_dir: Path) -> dict[str, str]:
    """The `${VAR}` credentials the packaged `data_source.json` needs to
    connect, resolved through this run's credential overlay
    (`set_credentials`) - the exact same source every in-process stage used.

    Without this the spawned `mis_mcp_runtime` only inherits `os.environ`, so
    for an API-started run (whose real credential lives in a ContextVar, not
    the process env) it falls back to whatever `FORGE_SOURCE_DB_URL` happens
    to sit in the server's own `.env` and smoke-tests the wrong database."""
    try:
        raw = json.loads((config_dir / "data_source.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    stmts = raw.get("connection", {}).get("duckdb_attach_sql", [])
    names = {name for stmt in stmts if isinstance(stmt, str) for name in _ENV_VAR_RE.findall(stmt)}
    return {name: value for name in names if (value := resolve_env(name)) is not None}

# Sourced from the generator's own TOOL_NAMES rather than a second hardcoded
# list, so a tool added to the runtime can never drift out of sync with what
# this check expects to find.
_EXPECTED_TOOLS = set(TOOL_NAMES)
_KPI_RETRY_ATTEMPTS = 3
_KPI_RETRY_DELAY_S = 1.5
# Hard ceiling on the whole stdio round-trip. The retries below are for
# transient hiccups; a genuinely unreachable warehouse would otherwise burn
# (tools + describe_schema + N KPIs) x (connect timeout + backoff) - minutes
# of a "running" pipeline for what is a fast, definite failure.
_SMOKE_TIMEOUT_S = 120.0
# Substrings that mean "the database itself is unreachable", not "this one
# query was briefly unlucky" - retrying these just feeds a circuit breaker,
# and every later KPI call will hit the identical wall.
_UNREACHABLE_MARKERS = (
    "unable to connect",
    "connection to server",
    "too many authentication failures",
    "circuitbreaker",
    "circuit breaker",
    "connection refused",
    "could not translate host name",
)


def _looks_unreachable(message: str | None) -> bool:
    if not message:
        return False
    low = message.lower()
    return any(marker in low for marker in _UNREACHABLE_MARKERS)


def _content_text(result: object) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _tool_failed(result: object) -> str | None:
    """Return an error message if the MCP tool call failed, else None."""
    if getattr(result, "is_error", False):
        return _content_text(result) or "tool call returned an error"
    first_block = (getattr(result, "content", None) or [None])[0]
    from mcp.types import TextContent

    if first_block is None or not isinstance(first_block, TextContent):
        return "tool returned non-text content"
    try:
        payload = json.loads(first_block.text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    return None


async def _run_smoke(config_dir: Path, data_dir: Path, kpi_ids: list[str]) -> list[ValidationIssue]:
    from mcp import StdioServerParameters, stdio_client
    from mcp.client.session import ClientSession

    issues: list[ValidationIssue] = []
    env = dict(os.environ)
    env.update(_plugin_connection_credentials(config_dir))
    env["MIS_MCP_CONFIG_DIR"] = str(config_dir)
    env["MIS_MCP_DATA_DIR"] = str(data_dir)
    params = StdioServerParameters(command=sys.executable, args=["-m", "mis_mcp_runtime.server"], env=env)

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools = await session.list_tools()
        tool_names = {t.name for t in tools.tools}
        missing = _EXPECTED_TOOLS - tool_names
        if missing:
            issues.append(
                ValidationIssue(
                    severity="error", location="tools", message=f"missing expected tools: {sorted(missing)}"
                )
            )

        schema_result = await session.call_tool("describe_schema", {})
        schema_error = _tool_failed(schema_result)
        if schema_error:
            issues.append(
                ValidationIssue(severity="error", location="describe_schema", message=schema_error)
            )
            if _looks_unreachable(schema_error):
                # Every get_kpi call would fail identically - don't hammer a
                # dead warehouse through the full retry matrix.
                return issues

        for kpi_id in kpi_ids:
            location = f"get_kpi:{kpi_id}"
            last_error: str | None = None
            for attempt in range(_KPI_RETRY_ATTEMPTS):
                result = await session.call_tool("get_kpi", {"kpi_id": kpi_id})
                last_error = _tool_failed(result)
                if last_error is None or _looks_unreachable(last_error):
                    break
                if attempt < _KPI_RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(_KPI_RETRY_DELAY_S * (attempt + 1))
            if last_error:
                issues.append(ValidationIssue(severity="error", location=location, message=last_error))
                if _looks_unreachable(last_error):
                    break

    return issues


def check_mcp_smoke(
    config_dir: Path | None, data_dir: Path | None, kpi_defs: KpiDefsFile | None
) -> ValidationCheckResult:
    if config_dir is None or data_dir is None or kpi_defs is None:
        return ValidationCheckResult(
            check="mcp_smoke",
            status=CheckStatus.SKIPPED,
            skipped_reason="no packaged config directory was provided (run after the packaging stage)",
        )

    try:
        issues = asyncio.run(
            asyncio.wait_for(
                _run_smoke(config_dir, data_dir, [k.id for k in kpi_defs.kpis]),
                timeout=_SMOKE_TIMEOUT_S,
            )
        )
    except TimeoutError:
        issues = [
            ValidationIssue(
                severity="error",
                location=str(config_dir),
                message=f"stdio smoke test exceeded {_SMOKE_TIMEOUT_S:.0f}s (warehouse unreachable?)",
            )
        ]
    except Exception as exc:
        issues = [
            ValidationIssue(
                severity="error", location=str(config_dir), message=f"stdio smoke test failed: {exc}"
            )
        ]

    status = CheckStatus.FAIL if any(i.severity == "error" for i in issues) else CheckStatus.PASS
    return ValidationCheckResult(check="mcp_smoke", status=status, issues=issues)
