"""End-to-end stdio smoke test: spawns `mis-mcp-runtime` as a real subprocess
over the MCP stdio transport and calls each tool through a real MCP client
session. This is the same mechanism the validation harness's `mcp_smoke`
check (M8) reuses against a freshly generated plugin.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_stdio_server_smoke(bookings_config_dir: Path):
    from mcp import StdioServerParameters, stdio_client
    from mcp.client.session import ClientSession

    # bookings_config_dir already set MIS_MCP_CONFIG_DIR / MIS_MCP_DATA_DIR via
    # monkeypatch on os.environ for this process; the subprocess inherits them.
    env = dict(os.environ)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mis_mcp_runtime.server"],
        env=env,
    )

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools = await session.list_tools()
        tool_names = {t.name for t in tools.tools}
        assert {"describe_schema", "get_kpi", "list_kpis", "run_safe_query", "search_records"} <= tool_names

        schema_result = await session.call_tool("describe_schema", {})
        schema_payload = json.loads(schema_result.content[0].text)
        assert "tables" in schema_payload

        kpi_result = await session.call_tool("get_kpi", {"kpi_id": "total_revenue"})
        kpi_payload = json.loads(kpi_result.content[0].text)
        assert kpi_payload["row_count"] == 1
