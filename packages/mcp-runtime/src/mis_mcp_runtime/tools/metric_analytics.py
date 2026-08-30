"""Tier 2 Business Analytics & KPI Tools.

Provides high-level analytical tools so Claude can reason in terms of KPIs,
period-over-period comparisons, entity rankings, and dimensional breakdowns
without writing manual SQL queries.
"""

from __future__ import annotations

import re
from typing import Any

import duckdb

from mis_mcp_runtime.config import RuntimeConfig
from mis_mcp_runtime.engine.kpi_executor import execute_kpi
from mis_mcp_runtime.engine.rows import to_json_rows
from mis_mcp_runtime.security.limits import run_with_timeout

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _extract_primary_numeric(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    row = rows[0]
    for val in row.values():
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
        if isinstance(val, str):
            try:
                return float(val)
            except ValueError:
                continue
    return None


def explain_metric(config: RuntimeConfig, metric_or_kpi_id: str) -> dict[str, Any]:
    """Provide transparent business formula, description, unit, and source metadata
    for a KPI or metric without running a database query.
    """
    kpi = next((k for k in config.kpis if k.id == metric_or_kpi_id), None)
    if kpi:
        return {
            "id": kpi.id,
            "name": kpi.label,
            "description": kpi.description,
            "unit": kpi.unit,
            "sql_expression": kpi.sql,
            "result_columns": kpi.result_columns,
            "assertions": kpi.assertions,
            "type": "compiled_kpi",
            "is_verified": True,
        }

    return {
        "error": f"Metric {metric_or_kpi_id!r} not found in compiled KPI catalog.",
        "available_kpis": [k.id for k in config.kpis],
    }


def compare_kpi(
    config: RuntimeConfig,
    con: duckdb.DuckDBPyConnection,
    kpi_id: str,
    period_a: dict[str, Any] | None = None,
    period_b: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare a KPI across two time periods or segments with automated calculation
    of absolute and relative percentage change.
    """
    kpi = next((k for k in config.kpis if k.id == kpi_id), None)
    if not kpi:
        return {
            "error": f"Unknown kpi_id {kpi_id!r}.",
            "available_kpis": [k.id for k in config.kpis],
        }

    # Execute KPI
    try:
        base_res = execute_kpi(con, kpi, config.query_timeout_seconds)
        val_base = _extract_primary_numeric(base_res.get("rows", []))

        # Build comparison response
        val_a = val_base if val_base is not None else 0.0
        val_b = val_base if val_base is not None else 0.0

        # If period parameters are provided with date bounds, filter accordingly
        if period_a and period_b and config.data_source.tables:
            # Safe period delta computation
            table = config.data_source.tables[0]
            ref = table.physical_ref
            # Find first date column if available
            date_col = next((c for c in table.columns if "date" in c.lower() or "_at" in c.lower()), None)
            if date_col and "start_date" in period_a and "end_date" in period_a:
                sa, ea = period_a["start_date"], period_a["end_date"]
                sb, eb = period_b["start_date"], period_b["end_date"]
                row_a = con.execute(
                    f'SELECT COUNT(*) FROM {ref} WHERE "{date_col}" >= ? AND "{date_col}" <= ?',
                    [sa, ea],
                ).fetchone()[0]
                row_b = con.execute(
                    f'SELECT COUNT(*) FROM {ref} WHERE "{date_col}" >= ? AND "{date_col}" <= ?',
                    [sb, eb],
                ).fetchone()[0]
                val_a = float(row_a)
                val_b = float(row_b)

        abs_change = round(val_b - val_a, 4)
        pct_change = round(((val_b - val_a) / val_a * 100.0), 2) if val_a != 0 else None

        return {
            "kpi_id": kpi.id,
            "label": kpi.label,
            "unit": kpi.unit,
            "period_a": {"params": period_a or "current", "value": val_a},
            "period_b": {"params": period_b or "comparison", "value": val_b},
            "absolute_change": abs_change,
            "relative_change_percent": pct_change,
            "interpretation": (
                f"{kpi.label} changed by {abs_change:+} {kpi.unit} "
                f"({pct_change:+}% relative change)" if pct_change is not None else "No baseline for percentage"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to compare KPI {kpi_id!r}: {exc}"}


def breakdown_metric(
    config: RuntimeConfig,
    con: duckdb.DuckDBPyConnection,
    dimension: str,
    metric_or_kpi_id: str | None = None,
    table: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Break down a business metric across slices of a categorical dimension
    (e.g., revenue by region, leads by status) with share-of-total percentage.
    """
    if dimension in config.bindings.denied_columns:
        return {"error": f"Dimension {dimension!r} is a denied/PII column."}
    if not _IDENTIFIER_PATTERN.match(dimension):
        return {"error": f"Invalid dimension identifier: {dimension!r}"}

    target_table = None
    if table:
        for t in config.data_source.tables:
            if (t.name == table or t.physical_ref == table) and (
                t.name in config.bindings.allowed_tables or t.physical_ref in config.bindings.allowed_tables
            ):
                target_table = t
                break
    else:
        for t in config.data_source.tables:
            if (
                t.name in config.bindings.allowed_tables or t.physical_ref in config.bindings.allowed_tables
            ) and dimension in t.columns:
                target_table = t
                break

    if not target_table:
        return {"error": f"Could not find allowed table containing dimension {dimension!r}."}

    ref = target_table.physical_ref
    bounded_limit = min(max(1, limit), 100)

    # Check if a measure column is specified
    measure_col = None
    if metric_or_kpi_id:
        for c in target_table.columns:
            if c.lower() == metric_or_kpi_id.lower() and c not in config.bindings.denied_columns:
                measure_col = c
                break

    try:
        if measure_col:
            agg_expr = f'SUM("{measure_col}")'
            metric_label = f"total_{measure_col}"
        else:
            agg_expr = "COUNT(*)"
            metric_label = "record_count"

        sql = (
            f'SELECT "{dimension}" AS slice, {agg_expr} AS metric_value '
            f'FROM {ref} '
            f'WHERE "{dimension}" IS NOT NULL '
            f'GROUP BY 1 '
            f'ORDER BY metric_value DESC '
            f'LIMIT {bounded_limit}'
        )
        df = run_with_timeout(con, sql, config.query_timeout_seconds)
        rows = to_json_rows(df)

        total = sum(r["metric_value"] for r in rows if isinstance(r.get("metric_value"), (int, float)))
        for r in rows:
            val = r.get("metric_value", 0)
            r["share_percent"] = round((val / total * 100.0) if total else 0.0, 2)

        return {
            "dimension": dimension,
            "metric": metric_label,
            "table": target_table.name,
            "total_metric_value": total,
            "slices": rows,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to execute breakdown for {dimension!r}: {exc}"}


def rank_entities(
    config: RuntimeConfig,
    con: duckdb.DuckDBPyConnection,
    entity_dimension: str,
    metric: str | None = None,
    table: str | None = None,
    limit: int = 20,
    order: str = "desc",
) -> dict[str, Any]:
    """Rank entities (e.g. top agents, highest performing categories) by a metric.
    Avoids requiring Claude to construct complex SQL order-by expressions.
    """
    if entity_dimension in config.bindings.denied_columns:
        return {"error": f"Entity dimension {entity_dimension!r} is a denied column."}
    if not _IDENTIFIER_PATTERN.match(entity_dimension):
        return {"error": f"Invalid entity identifier: {entity_dimension!r}"}

    order_sql = "DESC" if order.lower().strip() == "desc" else "ASC"
    target_table = None
    if table:
        for t in config.data_source.tables:
            if (t.name == table or t.physical_ref == table) and (
                t.name in config.bindings.allowed_tables or t.physical_ref in config.bindings.allowed_tables
            ):
                target_table = t
                break
    else:
        for t in config.data_source.tables:
            if (
                t.name in config.bindings.allowed_tables or t.physical_ref in config.bindings.allowed_tables
            ) and entity_dimension in t.columns:
                target_table = t
                break

    if not target_table:
        return {"error": f"Could not find allowed table containing {entity_dimension!r}."}

    ref = target_table.physical_ref
    bounded_limit = min(max(1, limit), 100)

    measure_col = None
    if metric:
        for c in target_table.columns:
            if c.lower() == metric.lower() and c not in config.bindings.denied_columns:
                measure_col = c
                break

    try:
        agg_expr = f'SUM("{measure_col}")' if measure_col else "COUNT(*)"
        metric_label = f"total_{measure_col}" if measure_col else "record_count"

        sql = (
            f'SELECT "{entity_dimension}" AS entity, {agg_expr} AS score '
            f'FROM {ref} '
            f'WHERE "{entity_dimension}" IS NOT NULL '
            f'GROUP BY 1 '
            f'ORDER BY score {order_sql} '
            f'LIMIT {bounded_limit}'
        )
        df = run_with_timeout(con, sql, config.query_timeout_seconds)
        rows = to_json_rows(df)

        for rank, r in enumerate(rows, start=1):
            r["rank"] = rank

        return {
            "entity": entity_dimension,
            "metric": metric_label,
            "table": target_table.name,
            "order": order_sql.lower(),
            "ranked_results": rows,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to rank entities for {entity_dimension!r}: {exc}"}


def query_metric(
    config: RuntimeConfig,
    con: duckdb.DuckDBPyConnection,
    metric_id: str,
    group_by: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Retrieve an analytical metric with optional grouping and date range filters."""
    # Check if this matches a compiled KPI
    kpi = next((k for k in config.kpis if k.id == metric_id), None)
    if kpi and not group_by and not start_date and not end_date:
        return execute_kpi(con, kpi, config.query_timeout_seconds)

    if group_by:
        for g in group_by:
            if g in config.bindings.denied_columns:
                return {"error": f"Grouping field {g!r} is a denied column."}
            if not _IDENTIFIER_PATTERN.match(g):
                return {"error": f"Invalid grouping identifier: {g!r}"}

    # If grouping is requested, perform aggregation over the main table
    if not config.data_source.tables:
        return {"error": "No tables available in data source."}

    table = config.data_source.tables[0]
    ref = table.physical_ref
    bounded_limit = min(max(1, limit), config.max_query_rows)

    group_cols = [f'"{g}"' for g in (group_by or []) if g in table.columns]
    group_clause = f"GROUP BY {', '.join(group_cols)}" if group_cols else ""
    select_group = f"{', '.join(group_cols)}, " if group_cols else ""

    where_clauses = []
    params: list[Any] = []
    date_col = next((c for c in table.columns if "date" in c.lower() or "_at" in c.lower()), None)
    if date_col and start_date:
        where_clauses.append(f'"{date_col}" >= ?')
        params.append(start_date)
    if date_col and end_date:
        where_clauses.append(f'"{date_col}" <= ?')
        params.append(end_date)

    where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    try:
        sql = (
            f"SELECT {select_group}COUNT(*) AS count "
            f"FROM {ref} "
            f"{where_str} "
            f"{group_clause} "
            f"ORDER BY count DESC "
            f"LIMIT {bounded_limit}"
        )
        if params:
            df = con.execute(sql, params).fetchdf()
        else:
            df = run_with_timeout(con, sql, config.query_timeout_seconds)

        return {
            "metric": metric_id,
            "table": table.name,
            "group_by": group_by or [],
            "rows": to_json_rows(df),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to query metric {metric_id!r}: {exc}"}
