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
from mis_mcp_runtime.tools.describe_data import (
    describe_data as _describe_data,
    list_business_concepts as _list_business_concepts,
)
from mis_mcp_runtime.tools.describe_schema import describe_schema as _describe_schema
from mis_mcp_runtime.tools.get_data_profile import get_data_profile as _get_data_profile
from mis_mcp_runtime.tools.get_kpi import get_kpi as _get_kpi
from mis_mcp_runtime.tools.get_kpi import list_kpis as _list_kpis
from mis_mcp_runtime.tools.get_value_set import get_value_set as _get_value_set
from mis_mcp_runtime.tools.metric_analytics import (
    breakdown_metric as _breakdown_metric,
    compare_kpi as _compare_kpi,
    explain_metric as _explain_metric,
    query_metric as _query_metric,
    rank_entities as _rank_entities,
)
from mis_mcp_runtime.tools.record_tools import (
    get_record as _get_record,
    get_related_records as _get_related_records,
)
from mis_mcp_runtime.tools.render_chart import chart_payload as _chart_payload
from mis_mcp_runtime.tools.render_chart import markdown_table as _markdown_table
from mis_mcp_runtime.tools.run_safe_query import run_safe_query as _run_safe_query
from mis_mcp_runtime.tools.search_records import search_records as _search_records

StateFn = Callable[[], tuple[RuntimeConfig, Any]]

_INSTRUCTIONS = (
    "Tools for querying this business's MIS data and Business Semantic Model. "
    "Use describe_data, list_business_concepts, and list_kpis first to discover available business concepts and metrics; "
    "use get_kpi, compare_kpi, breakdown_metric, and rank_entities for standard business analytics; "
    "use get_value_set to inspect distinct categories; "
    "use get_record and search_records for entity lookups; "
    "use run_safe_query ONLY as a last resort when no semantic tool can answer the question."
)

_CHART_RESOURCE_URI = "ui://mis/chart.html"


def _read_ui(filename: str) -> str:
    return importlib.resources.files("mis_mcp_runtime.ui").joinpath(filename).read_text(encoding="utf-8")


def create_server(get_state: StateFn | None = None) -> MCPServer:
    """Build an MCP server with the complete 4-Tier Business Analytics Surface.
    `get_state` lets a host (the Forge API) supply per-request config instead
    of the process-wide env dirs stdio uses.
    """
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
        name="mis-mcp-runtime", version="0.2.0", instructions=_INSTRUCTIONS, extensions=[apps]
    )

    # -------------------------------------------------------------------------
    # Tier 1: Semantic Discovery Tools
    # -------------------------------------------------------------------------

    @mcp.tool()
    def describe_data() -> dict[str, Any]:
        """Return the high-level Business Semantic Model: business domain, core
        entities, record grain, dimensions, measures, time fields, and available
        KPI counts. Call this FIRST to understand the scope of the business dataset."""
        config, _ = resolve()
        return _describe_data(config)

    @mcp.tool()
    def list_business_concepts() -> dict[str, Any]:
        """List all recognized business entities, dimensions, measures, and business
        events without requiring database schema knowledge."""
        config, _ = resolve()
        return _list_business_concepts(config)

    @mcp.tool()
    def describe_schema(table: str | None = None) -> dict[str, Any]:
        """Return table schemas, column data types, guessed roles, and denied column
        status. Pass table="name" for targeted single-table inspection."""
        config, _ = resolve()
        return _describe_schema(config, table=table)

    @mcp.tool()
    def get_data_profile(table: str) -> dict[str, Any]:
        """Return per-column data quality statistics (null percentages, cardinality,
        sample values) for one table. Denied/PII columns are always excluded."""
        config, con = resolve()
        return _get_data_profile(config, con, table)

    @mcp.tool()
    def get_value_set(field: str, table: str | None = None, limit: int = 50) -> dict[str, Any]:
        """Retrieve distinct values and percentage distributions for a categorical field.
        Always use this instead of writing exploratory SQL to see distinct values."""
        config, con = resolve()
        return _get_value_set(config, con, field, table=table, limit=limit)

    # -------------------------------------------------------------------------
    # Tier 2: Business Analytics & KPI Tools
    # -------------------------------------------------------------------------

    @mcp.tool()
    def list_kpis() -> dict[str, Any]:
        """List all verified business KPIs available in the catalog with descriptions,
        labels, and measurement units."""
        config, _ = resolve()
        return _list_kpis(config)

    @mcp.tool()
    def get_kpi(kpi_id: str) -> dict[str, Any]:
        """Execute a verified business KPI from the catalog (see list_kpis).
        Always prefer this over run_safe_query whenever a matching KPI exists."""
        try:
            config, con = resolve()
            return _get_kpi(config, con, kpi_id)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    @mcp.tool()
    def explain_metric(metric_id: str) -> dict[str, Any]:
        """Get the transparent formula definition, unit, description, and source
        fields of a business metric/KPI without executing a query."""
        config, _ = resolve()
        return _explain_metric(config, metric_id)

    @mcp.tool()
    def compare_kpi(
        kpi_id: str,
        period_a: dict[str, Any] | None = None,
        period_b: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compare a KPI across two time periods (e.g. period_a={'start_date': '...'})
        with automatic calculation of absolute delta and relative percentage change."""
        config, con = resolve()
        return _compare_kpi(config, con, kpi_id, period_a, period_b)

    @mcp.tool()
    def rank_entities(
        entity: str,
        metric: str | None = None,
        table: str | None = None,
        limit: int = 20,
        order: str = "desc",
    ) -> dict[str, Any]:
        """Rank business entities (e.g. top agents, highest performing categories,
        lead sources) by a metric. Avoids manual SQL ORDER BY queries."""
        config, con = resolve()
        return _rank_entities(config, con, entity_dimension=entity, metric=metric, table=table, limit=limit, order=order)

    @mcp.tool()
    def breakdown_metric(
        dimension: str,
        metric: str | None = None,
        table: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Break down a metric across categories of a dimension (e.g. revenue by region,
        bookings by status) with automated share-of-total percentage calculation."""
        config, con = resolve()
        return _breakdown_metric(config, con, dimension=dimension, metric_or_kpi_id=metric, table=table, limit=limit)

    @mcp.tool()
    def query_metric(
        metric_id: str,
        group_by: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Query a business metric with optional multi-dimensional grouping and date range filters."""
        config, con = resolve()
        return _query_metric(config, con, metric_id=metric_id, group_by=group_by, start_date=start_date, end_date=end_date, limit=limit)

    # -------------------------------------------------------------------------
    # Tier 3: Record & Entity Exploration Tools
    # -------------------------------------------------------------------------

    @mcp.tool()
    def search_records(
        table: str, filters: dict[str, Any] | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """Look up records in an allowed table matching exact filter criteria.
        Denied columns are never returned; result size is capped."""
        config, con = resolve()
        return _search_records(config, con, table, filters, limit)

    @mcp.tool()
    def get_record(
        table: str, id_value: str, id_column: str | None = None
    ) -> dict[str, Any]:
        """Retrieve a single entity record by its unique identifier."""
        config, con = resolve()
        return _get_record(config, con, table_or_entity=table, id_value=id_value, id_column=id_column)

    @mcp.tool()
    def get_related_records(
        source_table: str,
        source_id: str,
        target_table: str,
        foreign_key: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Traverse relational foreign keys between tables to find related entity records."""
        config, con = resolve()
        return _get_related_records(config, con, source_table=source_table, source_id=source_id, target_table=target_table, foreign_key=foreign_key, limit=limit)

    # -------------------------------------------------------------------------
    # Tier 5: Escape Hatch
    # -------------------------------------------------------------------------

    @mcp.tool()
    def run_safe_query(sql: str) -> dict[str, Any]:
        """Run a read-only SQL SELECT against allowed table(s) ONLY when no existing
        semantic or KPI tool can answer the question. Must be a single SELECT statement
        with explicit columns (no SELECT *); denied columns are rejected."""
        config, con = resolve()
        return _run_safe_query(config, con, sql)

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
