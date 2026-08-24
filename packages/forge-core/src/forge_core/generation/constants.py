"""Shared identifiers between the generation stage and the packager (M9) so
the MCP server name and tool references only need to change in one place."""

from __future__ import annotations

MCP_SERVER_NAME = "mis-mcp-runtime"

TOOL_NAMES = [
    "describe_schema",
    "get_data_profile",
    "list_kpis",
    "get_kpi",
    "list_metrics",
    "query_metric",
    "run_safe_query",
    "search_records",
    "render_chart",
]
"""P2-07: query_metric/list_metrics are additive, transitional tools -
get_kpi/list_kpis stay exactly as they are (kept "for one release" per the
plan) rather than being merged/removed in the same change that adds the
new capability. Reduces this back toward a fixed 7-tool surface once
get_kpi/list_kpis are actually retired; not done in this pass."""


def mcp_tool_ref(tool: str) -> str:
    """The `mcp__<server>__<tool>` reference Claude Code expects in
    `tools` / `allowed-tools` fields for a plugin-bundled MCP server."""
    return f"mcp__{MCP_SERVER_NAME}__{tool}"
