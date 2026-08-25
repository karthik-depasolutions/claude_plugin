"""P2-07 — renders a `MetricDefinition` + query-time parameters into
concrete, sqlglot-validated SQL. This is the generator-side twin of what the
runtime's `query_metric` tool does at request time (mis-mcp-runtime ships an
independent copy, matching the existing `assertion_policy.py` /
`engine/assertions.py` split - see that pair's parity test for the pattern).

Non-negotiables (PHASE_2.md P2-07): every identifier comes from the
validated `MetricDefinition`, every literal is a bound parameter, nothing
from a caller is ever string-interpolated into SQL. Final SQL still passes
through `sqlglot.parse_one` before it's trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot

from forge_core.models.metrics import AggOp, FilterSpec, MetricDefinition, render_aggregation

MAX_METRIC_ROWS = 200

_SQL_FILTER_OP = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


class MetricCompileError(ValueError):
    pass


@dataclass(frozen=True)
class RenderedQuery:
    sql: str
    params: list[Any] = field(default_factory=list)


def _dimension_ref(metric: MetricDefinition, group_by: str):
    for dim in metric.allowed_dimensions:
        if group_by in (dim.field_id, dim.physical):
            return dim
    valid = [d.field_id for d in metric.allowed_dimensions]
    raise MetricCompileError(f"{group_by!r} is not a valid dimension for metric {metric.id!r}. Valid: {valid}")


def _time_bucket_expr(time_column: str, grain: str) -> str:
    return f"DATE_TRUNC('{grain}', CAST({time_column} AS TIMESTAMP))"


def render_metric_query(
    metric: MetricDefinition,
    physical_ref: dict[str, str],
    *,
    group_by: str | None = None,
    time_grain: str | None = None,
    filters: list[FilterSpec] | None = None,
    limit: int = MAX_METRIC_ROWS,
) -> RenderedQuery:
    if metric.base_entity not in physical_ref:
        raise MetricCompileError(f"No physical reference for base entity {metric.base_entity!r}")
    base_ref = physical_ref[metric.base_entity]

    joins: list[str] = []
    seen_tables = {metric.base_entity}
    select_group: str | None = None
    group_alias = "grp"

    measure_table = metric.measure_table or metric.base_entity
    for edge in metric.measure_join_path:
        if edge.to_table in seen_tables:
            continue
        if edge.to_table not in physical_ref or edge.from_table not in physical_ref:
            raise MetricCompileError(f"Unknown table in measure join path: {edge.from_table}/{edge.to_table}")
        joins.append(
            f'JOIN {physical_ref[edge.to_table]} AS "{edge.to_table}" ON '
            f'"{edge.from_table}"."{edge.from_column}" = "{edge.to_table}"."{edge.to_column}"'
        )
        seen_tables.add(edge.to_table)

    if group_by is not None:
        dim = _dimension_ref(metric, group_by)
        for edge in dim.join_path:
            if edge.to_table in seen_tables:
                continue
            if edge.to_table not in physical_ref or edge.from_table not in physical_ref:
                raise MetricCompileError(f"Unknown table in join path: {edge.from_table}/{edge.to_table}")
            joins.append(
                f'JOIN {physical_ref[edge.to_table]} AS "{edge.to_table}" ON '
                f'"{edge.from_table}"."{edge.from_column}" = "{edge.to_table}"."{edge.to_column}"'
            )
            seen_tables.add(edge.to_table)
        dim_table_alias = dim.table
        select_group = f'"{dim_table_alias}"."{dim.physical}"'

    if time_grain is not None:
        if time_grain not in metric.allowed_time_grains:
            raise MetricCompileError(
                f"{time_grain!r} is not a valid time grain for metric {metric.id!r}. "
                f"Valid: {metric.allowed_time_grains}"
            )
        if not metric.time_column:
            raise MetricCompileError(f"time_grain requested but metric {metric.id!r} has no time_column")

    table_by_filterable_column = {metric.measure_column: measure_table}
    for d in metric.allowed_dimensions:
        table_by_filterable_column.setdefault(d.physical, d.table)

    conditions: list[str] = []
    params: list[Any] = []
    # metric.default_filters are the metric's own baseline scope (e.g. an
    # agent-proposed "completed enrollments only" view, P2-08) - always
    # applied, in addition to whatever the caller passes at query time.
    for f in [*metric.default_filters, *(filters or [])]:
        if f.column not in table_by_filterable_column:
            raise MetricCompileError(f"{f.column!r} is not a filterable field for metric {metric.id!r}")
        col_ref = f'"{table_by_filterable_column[f.column]}"."{f.column}"'
        if f.op.value in ("in", "not_in"):
            placeholders = ", ".join(["?"] * len(f.values))
            keyword = "NOT IN" if f.op.value == "not_in" else "IN"
            conditions.append(f"{col_ref} {keyword} ({placeholders})")
            params.extend(f.values)
        else:
            conditions.append(f"{col_ref} {_SQL_FILTER_OP[f.op.value]} ?")
            params.append(f.values[0])

    quoted_measure = f'"{measure_table}"."{metric.measure_column}"'
    agg_sql = render_aggregation(metric.aggregation, quoted_measure)

    select_parts = [f"{agg_sql} AS value"]
    group_parts: list[str] = []
    if select_group is not None:
        select_parts.insert(0, f"{select_group} AS {group_alias}")
        group_parts.append(select_group)
    if time_grain is not None:
        bucket = _time_bucket_expr(f'"{metric.base_entity}"."{metric.time_column}"', time_grain)
        select_parts.insert(0, f"{bucket} AS time_bucket")
        group_parts.append(bucket)

    sql = f'SELECT {", ".join(select_parts)} FROM {base_ref} AS "{metric.base_entity}"'
    sql += "".join(f" {j}" for j in joins)
    if conditions:
        sql += f" WHERE {' AND '.join(conditions)}"
    if group_parts:
        sql += f" GROUP BY {', '.join(group_parts)}"
        sql += f" ORDER BY value DESC LIMIT {int(limit)}"
    else:
        sql += f" LIMIT {int(limit)}"

    try:
        parsed = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise MetricCompileError(f"metric {metric.id!r} produced invalid SQL: {exc}\nSQL: {sql}") from exc
    if not isinstance(parsed, (sqlglot.exp.Select, sqlglot.exp.With)):
        raise MetricCompileError(f"metric {metric.id!r} must compile to a SELECT/WITH statement")

    return RenderedQuery(sql=parsed.sql(dialect="duckdb"), params=params)


__all__ = ["MetricCompileError", "RenderedQuery", "render_metric_query"]
