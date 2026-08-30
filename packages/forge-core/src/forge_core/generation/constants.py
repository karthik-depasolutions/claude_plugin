"""Shared identifiers between the generation stage and the packager (M9) so
the MCP server name and tool references only need to change in one place."""

from __future__ import annotations

MCP_SERVER_NAME = "mis-mcp-runtime"

TOOL_NAMES = [
    "describe_data",
    "list_business_concepts",
    "describe_schema",
    "get_data_profile",
    "get_value_set",
    "list_kpis",
    "get_kpi",
    "explain_metric",
    "compare_kpi",
    "rank_entities",
    "breakdown_metric",
    "query_metric",
    "search_records",
    "get_record",
    "get_related_records",
    "render_chart",
    "run_safe_query",
]


def mcp_tool_ref(tool: str) -> str:
    """The `mcp__<server>__<tool>` reference Claude Code expects in
    `tools` / `allowed-tools` fields for a plugin-bundled MCP server."""
    return f"mcp__{MCP_SERVER_NAME}__{tool}"
