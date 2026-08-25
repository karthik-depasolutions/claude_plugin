"""Assembles the P2-01 `EntityGraph` from what `structural.py`, `grain.py`,
and `relationships.py` already compute. No LLM call anywhere in this
module — see `models/entity_graph.py`'s own docstring for why that matters.

Per ADR 0001 (`docs/adr/0001-*.md`): exactly one entity is ever classified
`"fact"`. No pack, dataset, or KPI in this repository needs more than one,
so the classifier picks the single best-scoring candidate rather than
trying to support multiple simultaneous fact tables.
"""

from __future__ import annotations

import duckdb

from forge_core.models.common import ColumnRole
from forge_core.models.datasource import DataSource
from forge_core.models.entity_graph import (
    FAN_OUT_RISK_BY_CARDINALITY,
    Entity,
    EntityGraph,
    EntityRole,
    JoinEdge,
)
from forge_core.models.schema_profile import ColumnProfile, RelationshipCandidate, TableGrain
from forge_core.profiling.relationships import detect_cardinality, detect_declared_foreign_keys

_ADDITIVE_ROLES = {ColumnRole.NUMERIC, ColumnRole.CURRENCY}
_DIMENSION_ROLES = {ColumnRole.CATEGORICAL, ColumnRole.GEOGRAPHIC, ColumnRole.BOOLEAN_FLAG}
_TIME_ROLES = {ColumnRole.DATE, ColumnRole.DATETIME}


def _merge_candidates(
    declared: list[RelationshipCandidate], inferred: list[RelationshipCandidate]
) -> list[tuple[RelationshipCandidate, bool]]:
    """Declared FKs are ground truth and always win; an inferred candidate
    covering the same (from_table, from_column) is dropped rather than kept
    alongside it - one edge per real relationship, not two disagreeing ones."""
    declared_pairs = {(c.from_table, c.from_column) for c in declared}
    merged = [(c, True) for c in declared]
    merged += [
        (c, False) for c in inferred if (c.from_table, c.from_column) not in declared_pairs
    ]
    return merged


def _grain_for(grains: list[TableGrain], table: str) -> TableGrain:
    return next(g for g in grains if g.table == table)


def _fact_score(
    table_cols: list[ColumnProfile], outbound_edge_count: int, row_count: int
) -> float:
    has_measure = any(c.guessed_role in _ADDITIVE_ROLES for c in table_cols)
    has_time = any(c.guessed_role in _TIME_ROLES for c in table_cols)
    return (2.0 if has_measure else 0.0) + (2.0 if has_time else 0.0) + min(outbound_edge_count, 1) + row_count / 1000.0


def build_entity_graph(
    data_source: DataSource,
    columns: list[ColumnProfile],
    grains: list[TableGrain],
    con: duckdb.DuckDBPyConnection,
) -> EntityGraph:
    physical_ref = {t.name: t.physical_ref for t in data_source.tables}
    row_counts = {t.name: t.row_count for t in data_source.tables}

    declared = detect_declared_foreign_keys(data_source)
    from forge_core.profiling.relationships import detect_relationships

    inferred = detect_relationships(data_source, columns, con)
    candidates = _merge_candidates(declared, inferred)

    edges: list[JoinEdge] = []
    outbound_by_table: dict[str, set[str]] = {}
    for candidate, is_declared in candidates:
        cardinality, orphan_ratio = detect_cardinality(con, candidate, physical_ref)
        edges.append(
            JoinEdge(
                from_table=candidate.from_table,
                from_column=candidate.from_column,
                to_table=candidate.to_table,
                to_column=candidate.to_column,
                cardinality=cardinality,  # type: ignore[arg-type]
                overlap_ratio=candidate.confidence,
                orphan_ratio=orphan_ratio,
                confidence=candidate.confidence,
                origin="declared_fk" if is_declared else "value_overlap",
                verified=True,  # a real cardinality/overlap query has just run against it
                evidence=candidate.evidence,
                fan_out_risk=FAN_OUT_RISK_BY_CARDINALITY[cardinality],  # type: ignore[index]
            )
        )
        outbound_by_table.setdefault(candidate.from_table, set()).add(candidate.to_table)

    # Bridge reclassification: a table with >=2 outbound FKs and no
    # confident single-column PK of its own is the many-to-many join shape
    # - both its outbound edges (and their reverse) represent N:N traversal,
    # not the standard N:1 a plain FK implies. A composite key (e.g.
    # student_id+course_id) can itself have HIGH grain confidence - that's
    # the classic bridge-table shape, not evidence against it - so the test
    # must be "no confident SINGLE-column PK", not "low grain confidence".
    # Checking confidence alone let a clean two-column-key bridge table
    # (grain confidence 0.85, two columns) slip through and win the fact
    # slot on outbound-edge-count instead.
    def _has_confident_single_column_pk(table: str) -> bool:
        grain = _grain_for(grains, table)
        return grain.confidence >= 0.8 and len(grain.grain_columns) == 1

    bridge_tables = {
        table
        for table, parents in outbound_by_table.items()
        if len(parents) >= 2 and not _has_confident_single_column_pk(table)
    }
    for i, edge in enumerate(edges):
        if edge.from_table in bridge_tables:
            edges[i] = edge.model_copy(update={"cardinality": "N:N", "fan_out_risk": True})

    entities: list[Entity] = []
    # Bridge tables are never fact candidates - a junction table with no
    # measures and no time column of its own can still "win" on row_count
    # alone if it's allowed to compete, which is exactly backwards.
    fact_scores: dict[str, float] = {
        table.name: _fact_score(
            [c for c in columns if c.table == table.name],
            len(outbound_by_table.get(table.name, ())),
            table.row_count,
        )
        for table in data_source.tables
        if table.name not in bridge_tables
    }
    fact_table = (
        max(fact_scores, key=lambda t: (fact_scores[t], t))
        if any(score > 0 for score in fact_scores.values())
        else None
    )

    for table in data_source.tables:
        table_cols = [c for c in columns if c.table == table.name]
        grain = _grain_for(grains, table.name)
        has_single_pk = _has_confident_single_column_pk(table.name)

        role: EntityRole
        if table.name in bridge_tables:
            role = "bridge"
        elif table.name == fact_table:
            role = "fact"
        elif has_single_pk:
            role = "dimension"
        else:
            role = "unknown"

        entities.append(
            Entity(
                name=table.name,
                physical_table=table.physical_ref,
                role=role,
                key_columns=grain.grain_columns,
                measures=[c.name for c in table_cols if c.guessed_role in _ADDITIVE_ROLES and not c.is_likely_identifier],
                dimensions=[c.name for c in table_cols if c.guessed_role in _DIMENSION_ROLES],
                time_columns=[c.name for c in table_cols if c.guessed_role in _TIME_ROLES],
                row_count=row_counts.get(table.name, table.row_count),
                grain_confidence=grain.confidence,
            )
        )

    return EntityGraph(entities=entities, edges=edges)


__all__ = ["build_entity_graph"]
