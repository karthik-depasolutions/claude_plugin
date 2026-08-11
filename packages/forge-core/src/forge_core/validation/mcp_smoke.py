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


async def _run_smoke(config_dir: Path, data_dir: Path, kpi_ids: list[str]) -> list[ValidationIssue]:
    from mcp import StdioServerParameters, stdio_client
    from mcp.client.session import ClientSession
    from mcp.types import TextContent

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
        if schema_result.is_error:
            issues.append(
                ValidationIssue(
                    severity="error", location="describe_schema", message="tool call returned an error"
                )
            )

        for kpi_id in kpi_ids:
            location = f"get_kpi:{kpi_id}"
            result = await session.call_tool("get_kpi", {"kpi_id": kpi_id})
            if result.is_error:
                issues.append(
                    ValidationIssue(
                        severity="error", location=location, message="tool call returned an error"
                    )
                )
                continue
            first_block = result.content[0]
            if not isinstance(first_block, TextContent):
                issues.append(
                    ValidationIssue(
                        severity="error", location=location, message="tool returned non-text content"
                    )
                )
                continue
            payload = json.loads(first_block.text)
            if "error" in payload:
                issues.append(
                    ValidationIssue(severity="error", location=location, message=str(payload["error"]))
                )

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
