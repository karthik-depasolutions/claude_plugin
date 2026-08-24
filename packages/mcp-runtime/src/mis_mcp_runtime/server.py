"""The one generic MCP server every generated plugin ships or points at.

Zero customer-specific code lives here — everything customer-specific comes
from config/*.json (see config.py). This is the architectural boundary the
whole platform is built around: the generator produces configuration, this
runtime interprets it, identically, for every customer and every industry.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Callable
from typing import Any

from mcp.server.apps import Apps, ResourceCsp, client_supports_apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from mis_mcp_runtime.config import ConfigError, RuntimeConfig, load_runtime_config
from mis_mcp_runtime.engine.duckdb_session import open_session
from mis_mcp_runtime.tools.describe_schema import describe_schema as _describe_schema
from mis_mcp_runtime.tools.get_data_profile import get_data_profile as _get_data_profile
from mis_mcp_runtime.tools.get_kpi import get_kpi as _get_kpi
from mis_mcp_runtime.tools.get_kpi import list_kpis as _list_kpis
from mis_mcp_runtime.tools.query_metric import list_metrics as _list_metrics
from mis_mcp_runtime.tools.query_metric import query_metric as _query_metric
from mis_mcp_runtime.tools.render_chart import chart_payload as _chart_payload
from mis_mcp_runtime.tools.render_chart import markdown_table as _markdown_table
from mis_mcp_runtime.tools.run_safe_query import run_safe_query as _run_safe_query
from mis_mcp_runtime.tools.search_records import search_records as _search_records

StateFn = Callable[[], tuple[RuntimeConfig, Any]]

_INSTRUCTIONS = (
    "Tools for querying this business's MIS data. Prefer get_kpi for standard metrics; "
    "use describe_schema first if you're unsure what's available; use run_safe_query only "
    "for questions no existing KPI answers, and always as a read-only SELECT."
)

_CHART_RESOURCE_URI = "ui://mis/chart.html"


def _read_ui(filename: str) -> str:
    return importlib.resources.files("mis_mcp_runtime.ui").joinpath(filename).read_text(encoding="utf-8")


def create_server(get_state: StateFn | None = None) -> MCPServer:
    """Build an MCP server. `get_state` lets a host (the Forge API) supply
    per-request config instead of the process-wide env dirs stdio uses.

    Registers the `Apps` extension (`mcp.server.apps`) so `render_chart`
    renders as an interactive chart in a sandboxed iframe on any host that
    negotiates MCP Apps (Claude, Claude Desktop) - both over stdio and over
    the hosted Streamable HTTP transport (`forge_api.hosted_mcp` builds its
    ASGI app from this same `create_server`), with zero divergence between
    the two. Clients that don't negotiate Apps get a markdown table instead
    (`client_supports_apps`, per SEP-2133's graceful-degradation rule)."""
    state: dict[str, Any] = {}

    def _default_state() -> tuple[RuntimeConfig, Any]:
        if "config" not in state:
            config = load_runtime_config()
            con = open_session(config.data_source, config.data_dir)
            state["config"] = config
            state["con"] = con
        return state["config"], state["con"]

    resolve = get_state or _default_state

    apps = Apps()

    @apps.tool(resource_uri=_CHART_RESOURCE_URI, visibility=["model"])
    def render_chart(kpi_id: str, ctx: Context) -> dict[str, Any]:
        """Render a KPI as an interactive chart instead of raw numbers - prefer
        this over get_kpi whenever the user wants to *see* the data, not just
        read it. Falls back to a markdown table on clients that can't render
        the chart, so it's always safe to call."""
        try:
            config, con = resolve()
            result = _get_kpi(config, con, kpi_id)
        except Exception as exc:  # noqa: BLE001 - never crash the MCP session over one KPI
            return {"error": str(exc)}
        if "error" in result:
            return result
        # `client_supports_apps` reflects negotiated capability, not a
        # rendering guarantee - real-world testing surfaced hosts that
        # negotiate the extension and fetch the ui:// resource but never
        # actually mount the iframe (see docs/ - a confirmed, currently-open
        # gap in at least one major client's stdio transport). Always
        # include the markdown fallback so the model has a clean, readable
        # answer regardless of whether the chart visually renders; the chart
        # payload rides alongside it as a bonus when rendering does work.
        payload = {**result, "rendered": _markdown_table(result)}
        if client_supports_apps(ctx):
            payload["chart"] = _chart_payload(result)
        return payload

    apps.add_html_resource(
        _CHART_RESOURCE_URI,
        _read_ui("chart.html"),
        title="KPI Chart",
        description="An interactive chart for one KPI's result.",
        csp=ResourceCsp(),
        prefers_border=True,
    )

    mcp = MCPServer(
        name="mis-mcp-runtime", version="0.1.0", instructions=_INSTRUCTIONS, extensions=[apps]
    )

    @mcp.tool()
    def describe_schema() -> dict[str, Any]:
        """Return the tables and columns available to query, with guessed roles.
        Never includes row-level data. Call this first if you're unsure what
        tables or columns exist."""
        config, _ = resolve()
        return _describe_schema(config)

    @mcp.tool()
    def get_data_profile(table: str) -> dict[str, Any]:
        """Return per-column data quality stats (null percentage, cardinality)
        for one table. Denied/PII columns are always excluded."""
        config, con = resolve()
        return _get_data_profile(config, con, table)

    @mcp.tool()
    def list_kpis() -> dict[str, Any]:
        """List every pre-computed KPI available via get_kpi, with a short
        description of what each one measures."""
        config, _ = resolve()
        return _list_kpis(config)

    @mcp.tool()
    def get_kpi(kpi_id: str) -> dict[str, Any]:
        """Compute a named, pre-validated business KPI (see list_kpis for the
        available ids). Always prefer this over run_safe_query when a KPI already
        covers the question being asked."""
        try:
            config, con = resolve()
            return _get_kpi(config, con, kpi_id)
        except Exception as exc:  # noqa: BLE001 - never crash the MCP session over one KPI
            return {"error": str(exc)}

    @mcp.tool()
    def list_metrics() -> dict[str, Any]:
        """List every parameterized metric available via query_metric, with
        the dimensions and time grains each one can be sliced by. Prefer
        this + query_metric over get_kpi when you need a metric broken down
        by a dimension or time period - get_kpi only ever returns one frozen
        total."""
        config, _ = resolve()
        return _list_metrics(config)

    @mcp.tool()
    def query_metric(
        metric_id: str,
        group_by: str | None = None,
        time_grain: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compute a named business metric (see list_metrics for available
        ids, valid dimensions, and valid time grains), optionally grouped by
        one dimension and/or bucketed by a time grain, with optional
        exact-match filters. Call list_metrics first to see what's valid for
        a given metric_id - an unknown dimension or grain is rejected with
        the valid options named."""
        try:
            config, con = resolve()
            return _query_metric(config, con, metric_id, group_by, time_grain, filters)
        except Exception as exc:  # noqa: BLE001 - never crash the MCP session over one metric
            return {"error": str(exc)}

    @mcp.tool()
    def run_safe_query(sql: str) -> dict[str, Any]:
        """Run a read-only SQL SELECT against the allowed table(s) for questions
        no existing KPI answers. Must be a single SELECT/WITH statement with
        explicit columns (no `SELECT *`); denied columns and non-allowed tables
        are rejected; a row limit and timeout are always enforced."""
        config, con = resolve()
        return _run_safe_query(config, con, sql)

    @mcp.tool()
    def search_records(table: str, filters: dict[str, Any] | None = None, limit: int = 20) -> dict[str, Any]:
        """Look up rows in one allowed table by exact-match column filters.
        Denied columns are never returned; results are always capped."""
        config, con = resolve()
        return _search_records(config, con, table, filters, limit)

    return mcp


mcp = create_server()


def main() -> None:
    try:
        load_runtime_config()
    except ConfigError as exc:
        raise SystemExit(f"mis-mcp-runtime failed to start: {exc}") from exc
    mcp.run()


if __name__ == "__main__":
    main()
