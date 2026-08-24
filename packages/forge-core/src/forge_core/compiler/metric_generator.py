"""P2-07 — deterministic combinatoric generation of `MetricDefinition`s from
the entity graph (P2-01) and structural profile (P2-02/03). No LLM, no pack
enumeration, nothing invented: every metric is a real, additive column on
the fact entity, and every allowed dimension is a real column reached by a
verified, non-fan-out join path.

Where the review's "~7 frozen SQL strings -> ~400 answerable questions"
number comes from: generation produces one `MetricDefinition` per
(measure, aggregation) pair - the combinatorics with dimensions and time
grains happen at QUERY time (`query_metric`'s `group_by`/`time_grain`
params), not at generation time. A handful of `MetricDefinition`s, each
carrying a rich `allowed_dimensions` list, is what actually yields hundreds
of answerable slices - not hundreds of generated definitions.
"""

from __future__ import annotations

from forge_core.models.common import ColumnRole
from forge_core.models.entity_graph import EntityGraph
from forge_core.models.metrics import AggOp, DimensionRef, MetricDefinition
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile

MAX_JOIN_HOPS = 2
MIN_EDGE_CONFIDENCE = 0.7
MIN_DIMENSION_CARDINALITY = 2
MAX_DIMENSION_CARDINALITY = 50

_DIMENSION_ROLES = {ColumnRole.CATEGORICAL, ColumnRole.GEOGRAPHIC, ColumnRole.BOOLEAN_FLAG}
_TIME_GRAINS_FOR: list[str] = ["day", "week", "month", "quarter", "year"]


def _aggregations_for(measure: ColumnProfile) -> list[tuple[str, AggOp]]:
    """(id_suffix, op) pairs to generate per measure - a small, sensible set
    keyed on role, not an exhaustive cross product of every AggOp."""
    if measure.guessed_role == ColumnRole.CURRENCY:
        return [("total", AggOp.SUM), ("average", AggOp.MEAN)]
    return [("average", AggOp.MEAN)]  # NUMERIC (e.g. a score) - SUM rarely means anything


def _safe_path_ok(path: list) -> bool:
    return all(edge.verified and edge.confidence >= MIN_EDGE_CONFIDENCE and not edge.fan_out_risk for edge in path)


def _dimension_candidates(
    fact_table: str, graph: EntityGraph | None, structural: StructuralProfile
) -> list[DimensionRef]:
    """Every dimension-shaped column on every entity reachable from the fact
    table by a short, verified, non-fan-out path - including the fact
    table's own dimension columns (path=[])."""
    entities = graph.entities if graph is not None else []
    if not entities:
        # Single-table source: the fact "entity" is the whole dataset: no
        # entity_graph object exists (ADR 0001), so build candidates
        # directly from structural columns on that one table.
        cols = [c for c in structural.columns if c.table == fact_table]
        return [
            DimensionRef(
                field_id=f"{fact_table}.{c.name}", table=fact_table, physical=c.name, join_path=[],
                cardinality=c.cardinality, fan_out_safe=True,
            )
            for c in cols
            if c.guessed_role in _DIMENSION_ROLES and MIN_DIMENSION_CARDINALITY <= c.cardinality <= MAX_DIMENSION_CARDINALITY
        ]

    candidates: list[DimensionRef] = []
    for entity in entities:
        if entity.name == fact_table:
            path: list = []
        else:
            path = graph.join_path(fact_table, entity.name) if graph is not None else None
            if path is None or len(path) > MAX_JOIN_HOPS or not _safe_path_ok(path):
                continue
        col_by_name = {c.name: c for c in structural.columns if c.table == entity.name}
        for dim_name in entity.dimensions:
            col = col_by_name.get(dim_name)
            if col is None or col.is_likely_pii:
                continue
            if not (MIN_DIMENSION_CARDINALITY <= col.cardinality <= MAX_DIMENSION_CARDINALITY):
                continue
            candidates.append(
                DimensionRef(
                    field_id=f"{entity.name}.{dim_name}", table=entity.name, physical=dim_name,
                    join_path=path, cardinality=col.cardinality, fan_out_safe=not path or _safe_path_ok(path),
                )
            )
    return candidates


def generate_metrics(
    fact_table: str,
    structural: StructuralProfile,
    denied_columns: set[str],
) -> list[MetricDefinition]:
    """Deterministic, no LLM. `denied_columns` (from
    `packaging.denial.compute_denied_columns`, flattened) excludes PII
    columns from ever becoming a measure or dimension, mirroring the same
    guardrail every other layer of this pipeline enforces."""
    graph = structural.entity_graph
    fact_cols = [c for c in structural.columns if c.table == fact_table]
    measures = [
        c
        for c in fact_cols
        if c.guessed_role in (ColumnRole.NUMERIC, ColumnRole.CURRENCY)
        and not c.is_likely_identifier
        and c.name not in denied_columns
    ]
    time_cols = [c.name for c in fact_cols if c.guessed_role in (ColumnRole.DATE, ColumnRole.DATETIME)]
    allowed_time_grains = _TIME_GRAINS_FOR if time_cols else []
    time_column = time_cols[0] if time_cols else None
    dimensions = [d for d in _dimension_candidates(fact_table, graph, structural) if d.physical not in denied_columns]

    metrics: list[MetricDefinition] = []
    for measure in measures:
        for suffix, op in _aggregations_for(measure):
            metric_id = f"{suffix}_{measure.name}"
            unit = "currency" if measure.guessed_role == ColumnRole.CURRENCY else "numeric"
            metrics.append(
                MetricDefinition(
                    id=metric_id,
                    label=metric_id.replace("_", " ").title(),
                    description=f"{op.value} of {measure.name} on {fact_table}",
                    base_entity=fact_table,
                    measure_column=measure.name,
                    aggregation=op,
                    unit=unit,
                    allowed_dimensions=dimensions,
                    allowed_time_grains=allowed_time_grains,
                    time_column=time_column,
                )
            )
    return metrics


__all__ = ["generate_metrics"]
