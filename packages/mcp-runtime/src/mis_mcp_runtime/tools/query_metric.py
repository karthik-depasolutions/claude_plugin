"""`query_metric` / `list_metrics` — P2-07's parameterized replacement for a
frozen `get_kpi` lookup. Renders a `MetricConfig` + runtime parameters into
SQL (`engine/metric_query.py`), then routes it through the EXACT SAME
guardrail chain `run_safe_query` uses (allow-list, denied columns, row
limit, timeout) before DuckDB ever sees it - the parameterized rendering is
safe by construction, but there is no reason to trust that construction
alone when the existing pipeline is one function call away.
"""

from __future__ import annotations

from typing import Any

import duckdb

from mis_mcp_runtime.config import RuntimeConfig
from mis_mcp_runtime.engine.metric_query import MetricQueryError, render_metric_query
from mis_mcp_runtime.engine.rows import to_json_rows
from mis_mcp_runtime.security.allowlist import AllowlistError, check_tables_allowed
from mis_mcp_runtime.security.limits import QueryTimeoutError, enforce_row_limit, run_with_timeout
from mis_mcp_runtime.security.pii_policy import PiiPolicyError, check_no_denied_columns
from mis_mcp_runtime.security.sql_policy import SqlPolicyError, parse_single_select


def query_metric(
    config: RuntimeConfig,
    con: duckdb.DuckDBPyConnection,
    metric_id: str,
    group_by: str | None = None,
    time_grain: str | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metric = next((m for m in config.metrics if m.id == metric_id), None)
    if metric is None:
        available = [m.id for m in config.metrics]
        return {"error": f"Unknown metric_id {metric_id!r}. Available: {available}"}

    physical_ref = {t.name: t.physical_ref for t in config.data_source.tables}
    try:
        rendered = render_metric_query(
            metric, physical_ref, group_by=group_by, time_grain=time_grain,
            filters=filters, limit=config.max_query_rows,
        )
        statement = parse_single_select(rendered.sql)
        check_tables_allowed(statement, config.bindings.allowed_tables)
        check_no_denied_columns(statement, config.bindings.denied_columns)
        statement = enforce_row_limit(statement, config.max_query_rows)
        final_sql = statement.sql(dialect="duckdb")
        df = run_with_timeout(con, final_sql, config.query_timeout_seconds, params=rendered.params)
    except (MetricQueryError, SqlPolicyError, AllowlistError, PiiPolicyError, QueryTimeoutError) as exc:
        return {"error": str(exc), "error_type": type(exc).__name__}

    rows = to_json_rows(df)
    return {"rows": rows, "row_count": len(rows), "executed_sql": final_sql}


def list_metrics(config: RuntimeConfig) -> dict[str, Any]:
    return {
        "metrics": [
            {
                "id": m.id,
                "label": m.label,
                "description": m.description,
                "unit": m.unit,
                "allowed_dimensions": [d.field_id for d in m.allowed_dimensions],
                "allowed_time_grains": m.allowed_time_grains,
            }
            for m in config.metrics
        ]
    }


__all__ = ["list_metrics", "query_metric"]
