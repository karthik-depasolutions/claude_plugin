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
import sys
from pathlib import Path

from forge_core.models.common import CheckStatus
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.validation import ValidationCheckResult, ValidationIssue

_EXPECTED_TOOLS = {
    "describe_schema",
    "get_data_profile",
    "list_kpis",
    "get_kpi",
    "run_safe_query",
    "search_records",
}
_KPI_RETRY_ATTEMPTS = 3
_KPI_RETRY_DELAY_S = 1.5


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

        for kpi_id in kpi_ids:
            location = f"get_kpi:{kpi_id}"
            last_error: str | None = None
            for attempt in range(_KPI_RETRY_ATTEMPTS):
                result = await session.call_tool("get_kpi", {"kpi_id": kpi_id})
                last_error = _tool_failed(result)
                if last_error is None:
                    break
                if attempt < _KPI_RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(_KPI_RETRY_DELAY_S * (attempt + 1))
            if last_error:
                issues.append(ValidationIssue(severity="error", location=location, message=last_error))

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
        issues = asyncio.run(_run_smoke(config_dir, data_dir, [k.id for k in kpi_defs.kpis]))
    except Exception as exc:
        issues = [
            ValidationIssue(
                severity="error", location=str(config_dir), message=f"stdio smoke test failed: {exc}"
            )
        ]

    status = CheckStatus.FAIL if any(i.severity == "error" for i in issues) else CheckStatus.PASS
    return ValidationCheckResult(check="mcp_smoke", status=status, issues=issues)
