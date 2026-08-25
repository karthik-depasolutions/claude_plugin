"""P2-07 runtime half of the parameterized metric layer — renders a
`MetricConfig` + query-time parameters into concrete SQL, executed only
through the exact same safety pipeline `run_safe_query` already uses
(allow-list, denied columns, row limit, timeout). This is a deliberate,
independent duplicate of `forge_core.compiler.metric_compiler` (mirroring
the existing `assertion_policy.py` / `engine/assertions.py` split — see
`test_assertion_policy_parity.py` for why the two packages carry separate
copies rather than a shared import) — mis-mcp-runtime never imports
forge_core.

Non-negotiables (unchanged from the generator side): every identifier comes
from the validated `MetricConfig`, every literal is a bound parameter,
nothing from a caller is ever string-interpolated into SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mis_mcp_runtime.config import MetricConfig

_AGG_SQL = {
    "sum": "SUM", "mean": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT",
    "std": "STDDEV", "var": "VARIANCE", "median": "MEDIAN",
}
_FILTER_OP_SQL = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


class MetricQueryError(ValueError):
    pass


@dataclass(frozen=True)
class RenderedMetricQuery:
    sql: str
    params: list[Any] = field(default_factory=list)


def _render_aggregation(op: str, quoted_column: str) -> str:
    if op == "nunique":
        return f"COUNT(DISTINCT {quoted_column})"
    sql_fn = _AGG_SQL.get(op)
    if sql_fn is None:
        raise MetricQueryError(f"Unknown aggregation {op!r}")
    return f"{sql_fn}({quoted_column})"


def _dimension(metric: MetricConfig, group_by: str):
    for dim in metric.allowed_dimensions:
        if group_by in (dim.field_id, dim.physical):
            return dim
    valid = [d.field_id for d in metric.allowed_dimensions]
    raise MetricQueryError(f"{group_by!r} is not a valid dimension for metric {metric.id!r}. Valid: {valid}")


def render_metric_query(
    metric: MetricConfig,
    physical_ref: dict[str, str],
    *,
    group_by: str | None = None,
    time_grain: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 200,
) -> RenderedMetricQuery:
    if metric.base_entity not in physical_ref:
        raise MetricQueryError(f"No physical reference for base entity {metric.base_entity!r}")
    base_ref = physical_ref[metric.base_entity]

    joins: list[str] = []
    seen = {metric.base_entity}
    select_group: str | None = None

    measure_table = metric.measure_table or metric.base_entity
    for edge in metric.measure_join_path:
        if edge.to_table in seen:
            continue
        if edge.to_table not in physical_ref or edge.from_table not in physical_ref:
            raise MetricQueryError(f"Unknown table in measure join path: {edge.from_table}/{edge.to_table}")
        joins.append(
            f'JOIN {physical_ref[edge.to_table]} AS "{edge.to_table}" ON '
            f'"{edge.from_table}"."{edge.from_column}" = "{edge.to_table}"."{edge.to_column}"'
        )
        seen.add(edge.to_table)

    if group_by is not None:
        dim = _dimension(metric, group_by)
        for edge in dim.join_path:
            if edge.to_table in seen:
                continue
            if edge.to_table not in physical_ref or edge.from_table not in physical_ref:
                raise MetricQueryError(f"Unknown table in join path: {edge.from_table}/{edge.to_table}")
            joins.append(
                f'JOIN {physical_ref[edge.to_table]} AS "{edge.to_table}" ON '
                f'"{edge.from_table}"."{edge.from_column}" = "{edge.to_table}"."{edge.to_column}"'
            )
            seen.add(edge.to_table)
        select_group = f'"{dim.table}"."{dim.physical}"'

    if time_grain is not None:
        if time_grain not in metric.allowed_time_grains:
            raise MetricQueryError(
                f"{time_grain!r} is not a valid time grain for metric {metric.id!r}. "
                f"Valid: {metric.allowed_time_grains}"
            )
        if not metric.time_column:
            raise MetricQueryError(f"time_grain requested but metric {metric.id!r} has no time_column")

    table_by_filterable_column = {metric.measure_column: measure_table}
    for d in metric.allowed_dimensions:
        table_by_filterable_column.setdefault(d.physical, d.table)

    conditions: list[str] = []
    params: list[Any] = []
    # metric.default_filters are the metric's own baseline scope (e.g. an
    # agent-proposed "completed enrollments only" view, P2-08) - always
    # applied, in addition to whatever the caller passes at query time.
    for f in metric.default_filters:
        if f.column not in table_by_filterable_column:
            raise MetricQueryError(f"{f.column!r} is not a filterable field for metric {metric.id!r}")
        col_ref = f'"{table_by_filterable_column[f.column]}"."{f.column}"'
        if f.op in ("in", "not_in"):
            placeholders = ", ".join(["?"] * len(f.values))
            keyword = "NOT IN" if f.op == "not_in" else "IN"
            conditions.append(f"{col_ref} {keyword} ({placeholders})")
            params.extend(f.values)
        else:
            op_sql = _FILTER_OP_SQL.get(f.op)
            if op_sql is None:
                raise MetricQueryError(f"Unknown filter op {f.op!r} on metric {metric.id!r}")
            conditions.append(f"{col_ref} {op_sql} ?")
            params.append(f.values[0])
    for column, value in (filters or {}).items():
        if column not in table_by_filterable_column:
            raise MetricQueryError(f"{column!r} is not a filterable field for metric {metric.id!r}")
        conditions.append(f'"{table_by_filterable_column[column]}"."{column}" = ?')
        params.append(value)

    quoted_measure = f'"{measure_table}"."{metric.measure_column}"'
    agg_sql = _render_aggregation(metric.aggregation, quoted_measure)

    select_parts = [f"{agg_sql} AS value"]
    group_parts: list[str] = []
    if select_group is not None:
        select_parts.insert(0, f"{select_group} AS grp")
        group_parts.append(select_group)
    if time_grain is not None:
        bucket = f"DATE_TRUNC('{time_grain}', CAST(\"{metric.base_entity}\".\"{metric.time_column}\" AS TIMESTAMP))"
        select_parts.insert(0, f"{bucket} AS time_bucket")
        group_parts.append(bucket)

    sql = f'SELECT {", ".join(select_parts)} FROM {base_ref} AS "{metric.base_entity}"'
    sql += "".join(f" {j}" for j in joins)
    if conditions:
        sql += f" WHERE {' AND '.join(conditions)}"
    if group_parts:
        sql += f" GROUP BY {', '.join(group_parts)} ORDER BY value DESC LIMIT {int(limit)}"
    else:
        sql += f" LIMIT {int(limit)}"

    return RenderedMetricQuery(sql=sql, params=params)


__all__ = ["MetricQueryError", "RenderedMetricQuery", "render_metric_query"]
