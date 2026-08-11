"""The one generic MCP server every generated plugin ships or points at.

Zero customer-specific code lives here — everything customer-specific comes
from config/*.json (see config.py). This is the architectural boundary the
whole platform is built around: the generator produces configuration, this
runtime interprets it, identically, for every customer and every industry.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from mis_mcp_runtime.config import ConfigError, RuntimeConfig, load_runtime_config
from mis_mcp_runtime.engine.duckdb_session import open_session
from mis_mcp_runtime.tools.describe_schema import describe_schema as _describe_schema
from mis_mcp_runtime.tools.get_data_profile import get_data_profile as _get_data_profile
from mis_mcp_runtime.tools.get_kpi import get_kpi as _get_kpi
from mis_mcp_runtime.tools.get_kpi import list_kpis as _list_kpis
from mis_mcp_runtime.tools.run_safe_query import run_safe_query as _run_safe_query
from mis_mcp_runtime.tools.search_records import search_records as _search_records

mcp: MCPServer = MCPServer(
    name="mis-mcp-runtime",
    version="0.1.0",
    instructions=(
        "Tools for querying this business's MIS data. Prefer get_kpi for standard metrics; "
        "use describe_schema first if you're unsure what's available; use run_safe_query only "
        "for questions no existing KPI answers, and always as a read-only SELECT."
    ),
)

_state: dict[str, Any] = {}


def _get_state() -> tuple[RuntimeConfig, Any]:
    if "config" not in _state:
        config = load_runtime_config()
        con = open_session(config.data_source, config.data_dir)
        _state["config"] = config
        _state["con"] = con
    return _state["config"], _state["con"]


@mcp.tool()
def describe_schema() -> dict[str, Any]:
    """Return the tables and columns available to query, with guessed roles.
    Never includes row-level data. Call this first if you're unsure what
    tables or columns exist."""
    config, _ = _get_state()
    return _describe_schema(config)


@mcp.tool()
def get_data_profile(table: str) -> dict[str, Any]:
    """Return per-column data quality stats (null percentage, cardinality)
    for one table. Denied/PII columns are always excluded."""
    config, con = _get_state()
    return _get_data_profile(config, con, table)


@mcp.tool()
def list_kpis() -> dict[str, Any]:
    """List every pre-computed KPI available via get_kpi, with a short
    description of what each one measures."""
    config, _ = _get_state()
    return _list_kpis(config)


@mcp.tool()
def get_kpi(kpi_id: str) -> dict[str, Any]:
    """Compute a named, pre-validated business KPI (see list_kpis for the
    available ids). Always prefer this over run_safe_query when a KPI already
    covers the question being asked."""
    config, con = _get_state()
    return _get_kpi(config, con, kpi_id)


@mcp.tool()
def run_safe_query(sql: str) -> dict[str, Any]:
    """Run a read-only SQL SELECT against the allowed table(s) for questions
    no existing KPI answers. Must be a single SELECT/WITH statement with
    explicit columns (no `SELECT *`); denied columns and non-allowed tables
    are rejected; a row limit and timeout are always enforced."""
    config, con = _get_state()
    return _run_safe_query(config, con, sql)


@mcp.tool()
def search_records(table: str, filters: dict[str, Any] | None = None, limit: int = 20) -> dict[str, Any]:
    """Look up rows in one allowed table by exact-match column filters.
    Denied columns are never returned; results are always capped."""
    config, con = _get_state()
    return _search_records(config, con, table, filters, limit)


def main() -> None:
    try:
        load_runtime_config()
    except ConfigError as exc:
        raise SystemExit(f"mis-mcp-runtime failed to start: {exc}") from exc
    mcp.run()


if __name__ == "__main__":
    main()
