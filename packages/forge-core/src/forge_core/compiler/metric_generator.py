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

from datetime import UTC, datetime

from forge_core.models.claims import ColumnClaim
from forge_core.models.common import ColumnRole
from forge_core.models.entity_graph import Entity, EntityGraph, JoinEdge
from forge_core.models.metrics import AggOp, DimensionRef, MetricDefinition, Provenance
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile

MAX_JOIN_HOPS = 2
MIN_EDGE_CONFIDENCE = 0.7
MIN_DIMENSION_CARDINALITY = 2
MAX_DIMENSION_CARDINALITY = 50

_DIMENSION_ROLES = {ColumnRole.CATEGORICAL, ColumnRole.GEOGRAPHIC, ColumnRole.BOOLEAN_FLAG}
_TIME_GRAINS_FOR: list[str] = ["day", "week", "month", "quarter", "year"]


def _aggregations_for(claim: ColumnClaim | None) -> list[tuple[str, AggOp]]:
    """(id_suffix, op) pairs to generate per measure. SUM is default-deny:
    additivity is a semantic property (does summing two of these values mean
    anything?), never a shape, so it is granted ONLY by a gate-verified
    ColumnClaim naming AggOp.SUM in `valid_aggregations` - never by a
    ColumnRole. `claim=None` (no agent, or the agent didn't resolve this
    column) is the safe default: every numeric measure still gets
    mean/min/max/median, which are mathematically sound regardless of
    whether the values are additive."""
    if claim is not None and AggOp.SUM in claim.valid_aggregations:
        return [("total", AggOp.SUM), ("average", AggOp.MEAN), ("min", AggOp.MIN), ("max", AggOp.MAX)]
    return [("average", AggOp.MEAN), ("min", AggOp.MIN), ("max", AggOp.MAX), ("median", AggOp.MEDIAN)]


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


def _status_like_dimension(dimensions: list[DimensionRef]) -> DimensionRef | None:
    return next((d for d in dimensions if any(k in d.physical.lower() for k in ("status", "state"))), None)


def _provenance_for(
    aggregations: list[tuple[str, AggOp]], dimensions: list[DimensionRef], claim: ColumnClaim | None
) -> Provenance:
    """A SUM total is a correct SQL aggregate regardless of scope, but when
    a status/state-shaped column exists to filter by and this metric
    doesn't (no default_filters), whether every status genuinely belongs in
    the total is an unconfirmed business assumption - not a data question,
    a policy question. Keyed off whether SUM was actually granted, not off
    a literal "currency" unit string, so the same caveat applies to any
    additive measure with a status dimension (order count, item quantity,
    ...), not only money. Same risk whether the measure lives on the fact
    table directly or is broadcast in from a joined entity (P2-07)."""
    has_sum = any(op == AggOp.SUM for _, op in aggregations)
    status_dim = _status_like_dimension(dimensions) if has_sum else None
    origin = "inferred_llm" if claim is not None else "deterministic"
    base_evidence = (
        claim.evidence if claim is not None else ["generated deterministically, no LLM involved"]
    )
    if status_dim is None:
        return Provenance(
            origin=origin,
            confidence=claim.confidence if claim is not None else 1.0,
            evidence=base_evidence,
            computed_by="metric_generator.generate_metrics",
            computed_at=datetime.now(UTC).isoformat(),
        )
    return Provenance(
        origin=origin,
        confidence=min(0.6, claim.confidence if claim is not None else 1.0),
        evidence=[
            *base_evidence,
            f"sums across every row regardless of {status_dim.physical!r} - whether every status "
            f"value genuinely belongs in this total (e.g. does a cancelled/dropped/refunded row "
            f"count?) has not been confirmed; a variant filtered by {status_dim.physical!r} would "
            f"be more trustworthy where one exists",
        ],
        computed_by="metric_generator.generate_metrics",
        computed_at=datetime.now(UTC).isoformat(),
    )


def _broadcast_measure_sources(
    fact_table: str, graph: EntityGraph | None, structural: StructuralProfile, denied_columns: set[str]
) -> list[tuple[Entity, ColumnProfile, list[JoinEdge]]]:
    """Measures that live on a *different* entity than the fact table, but
    are safe to aggregate at the fact grain because the join back to the
    fact table is verified, short, and non-fan-out - e.g. `courses.price_inr`
    broadcast across every `enrollments` row via the verified N:1
    `enrollments.course_id -> courses.course_id` edge. This is the standard
    star-schema "degenerate measure" pattern, not a hack: each fact row
    genuinely inherits exactly one value from the dimension row it joins to,
    so SUM/AVG/etc. over the fact table's rows is well-defined and
    unambiguous. Requires an entity_graph (ADR 0001) - single-table sources
    have nothing to broadcast from."""
    if graph is None:
        return []
    col_by_table_name: dict[str, ColumnProfile] = {
        (c.table, c.name): c for c in structural.columns  # type: ignore[misc]
    }
    results: list[tuple[Entity, ColumnProfile, list[JoinEdge]]] = []
    for entity in graph.entities:
        if entity.name == fact_table or not entity.measures:
            continue
        path = graph.join_path(fact_table, entity.name)
        if path is None or len(path) > MAX_JOIN_HOPS or not _safe_path_ok(path):
            continue
        for measure_name in entity.measures:
            col = col_by_table_name.get((entity.name, measure_name))
            if col is None or col.name in denied_columns:
                continue
            results.append((entity, col, path))
    return results


def _claim_for(claims: dict[str, ColumnClaim] | None, table: str, column: str) -> ColumnClaim | None:
    return claims.get(f"{table}.{column}") if claims else None


def generate_metrics(
    fact_table: str,
    structural: StructuralProfile,
    denied_columns: set[str],
    claims: dict[str, ColumnClaim] | None = None,
) -> list[MetricDefinition]:
    """Deterministic combinatorics over real structural facts, PLUS
    gate-verified agent claims (Part 1/2 of the understanding-agent
    architecture) for the one thing structure alone can't determine:
    additivity. `claims`, keyed `"{table}.{column}"`, is optional and
    defaults to None (the `--no-llm` / test path) - every measure still
    gets mean/min/max/median with no claim, just never SUM (see
    `_aggregations_for`). `denied_columns` (from
    `packaging.denial.compute_denied_columns`, flattened) excludes PII
    columns from ever becoming a measure or dimension, mirroring the same
    guardrail every other layer of this pipeline enforces."""
    graph = structural.entity_graph
    fact_cols = [c for c in structural.columns if c.table == fact_table]
    measures = [
        c
        for c in fact_cols
        if c.guessed_role == ColumnRole.NUMERIC
        and not c.is_likely_identifier
        and c.name not in denied_columns
        # A numeric foreign key (e.g. course_id referencing another table's
        # PK) passes the structural NUMERIC-not-identifier test on THIS
        # table alone, but averaging/summing an ID number is meaningless.
        # A claim explicitly saying "identifier" (or dimension/time/flag)
        # is real evidence this column isn't a measure at all, not just a
        # question of which aggregations apply to it - exclude it outright
        # rather than ship "average course_id".
        and (_claim_for(claims, c.table, c.name) is None or _claim_for(claims, c.table, c.name).kind == "measure")
    ]
    time_cols = [c.name for c in fact_cols if c.guessed_role in (ColumnRole.DATE, ColumnRole.DATETIME)]
    allowed_time_grains = _TIME_GRAINS_FOR if time_cols else []
    time_column = time_cols[0] if time_cols else None
    dimensions = [d for d in _dimension_candidates(fact_table, graph, structural) if d.physical not in denied_columns]

    metrics: list[MetricDefinition] = []
    for measure in measures:
        claim = _claim_for(claims, measure.table, measure.name)
        aggregations = _aggregations_for(claim)
        unit = (claim.unit if claim and claim.unit else None) or "numeric"
        for suffix, op in aggregations:
            metric_id = f"{suffix}_{measure.name}"
            status_dim = _status_like_dimension(dimensions) if op == AggOp.SUM else None
            description = f"{op.value} of {measure.name} on {fact_table}" + (
                f" - across every row regardless of {status_dim.physical!r}, not confirmed as the "
                f"correct business scope"
                if status_dim is not None
                else ""
            )
            metrics.append(
                MetricDefinition(
                    id=metric_id,
                    label=metric_id.replace("_", " ").title(),
                    description=description,
                    base_entity=fact_table,
                    measure_column=measure.name,
                    aggregation=op,
                    unit=unit,
                    allowed_dimensions=dimensions,
                    allowed_time_grains=allowed_time_grains,
                    time_column=time_column,
                    prov=_provenance_for(aggregations, dimensions, claim),
                )
            )

    for entity, measure, path in _broadcast_measure_sources(fact_table, graph, structural, denied_columns):
        claim = _claim_for(claims, measure.table, measure.name)
        aggregations = _aggregations_for(claim)
        unit = (claim.unit if claim and claim.unit else None) or "numeric"
        for suffix, op in aggregations:
            metric_id = f"{suffix}_{measure.name}"
            if metric_id in {m.id for m in metrics}:
                metric_id = f"{suffix}_{entity.name}_{measure.name}"
            status_dim = _status_like_dimension(dimensions) if op == AggOp.SUM else None
            description = f"{op.value} of {entity.name}.{measure.name}, joined to every {fact_table} row" + (
                f" - across every row regardless of {status_dim.physical!r}, not confirmed as the "
                f"correct business scope"
                if status_dim is not None
                else ""
            )
            metrics.append(
                MetricDefinition(
                    id=metric_id,
                    label=metric_id.replace("_", " ").title(),
                    description=description,
                    base_entity=fact_table,
                    measure_column=measure.name,
                    measure_table=entity.name,
                    measure_join_path=path,
                    aggregation=op,
                    unit=unit,
                    allowed_dimensions=dimensions,
                    allowed_time_grains=allowed_time_grains,
                    time_column=time_column,
                    prov=_provenance_for(aggregations, dimensions, claim),
                )
            )

    fact_entity = graph.fact_entity() if graph is not None else None
    key_column = (
        fact_entity.key_columns[0]
        if fact_entity is not None and fact_entity.key_columns
        else next((c.name for c in fact_cols if c.is_likely_identifier), None)
    )
    if key_column is not None and key_column not in denied_columns:
        metrics.append(
            MetricDefinition(
                id=f"count_{fact_table}",
                label=f"Count {fact_table}".replace("_", " ").title(),
                description=f"Number of {fact_table} rows",
                base_entity=fact_table,
                measure_column=key_column,
                aggregation=AggOp.COUNT,
                unit="count",
                allowed_dimensions=dimensions,
                allowed_time_grains=allowed_time_grains,
                time_column=time_column,
                prov=_provenance_for([("count", AggOp.COUNT)], dimensions, None),
            )
        )

    return metrics


__all__ = ["generate_metrics"]
