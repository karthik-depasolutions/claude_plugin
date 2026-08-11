"""Deterministic join/foreign-key candidate detection.

Purely mechanical: name-based candidate generation, then value-overlap
verification via DuckDB. No LLM involved — the architecture doc is explicit
that structural facts must never depend on a model call.
"""

from __future__ import annotations

import duckdb

from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import ColumnProfile, RelationshipCandidate

MIN_OVERLAP_RATIO = 0.5


def _is_pk_candidate(col: ColumnProfile, row_count: int) -> bool:
    if not (col.is_likely_identifier and col.null_percent == 0.0 and row_count > 0):
        return False
    return col.cardinality == row_count


def detect_relationships(
    data_source: DataSource,
    columns: list[ColumnProfile],
    con: duckdb.DuckDBPyConnection,
) -> list[RelationshipCandidate]:
    row_counts = {t.name: t.row_count for t in data_source.tables}
    physical_ref = {t.name: t.physical_ref for t in data_source.tables}

    identifier_cols = [c for c in columns if c.is_likely_identifier]
    by_name: dict[str, list[ColumnProfile]] = {}
    for c in identifier_cols:
        by_name.setdefault(c.name.lower(), []).append(c)

    candidates: list[RelationshipCandidate] = []
    for _name, cols in by_name.items():
        if len(cols) < 2:
            continue
        pk_cols = [c for c in cols if _is_pk_candidate(c, row_counts[c.table])]
        for parent in pk_cols:
            for child in cols:
                if child.table == parent.table:
                    continue
                overlap = _overlap_ratio(
                    con, physical_ref[child.table], child.name, physical_ref[parent.table], parent.name
                )
                if overlap >= MIN_OVERLAP_RATIO:
                    candidates.append(
                        RelationshipCandidate(
                            from_table=child.table,
                            from_column=child.name,
                            to_table=parent.table,
                            to_column=parent.name,
                            confidence=round(overlap, 3),
                            evidence=(
                                f"{overlap * 100:.1f}% of distinct {child.table}.{child.name} values "
                                f"exist in {parent.table}.{parent.name} (a unique/PK-like column)"
                            ),
                        )
                    )
    return candidates


def _overlap_ratio(
    con: duckdb.DuckDBPyConnection, child_ref: str, child_col: str, parent_ref: str, parent_col: str
) -> float:
    child_q, parent_q = f'"{child_col}"', f'"{parent_col}"'
    row = con.execute(
        f"""
        SELECT
            COUNT(DISTINCT {child_q}) FILTER (WHERE {child_q} IS NOT NULL) AS total_distinct,
            COUNT(DISTINCT {child_q}) FILTER (
                WHERE {child_q} IS NOT NULL AND {child_q} IN (SELECT {parent_q} FROM {parent_ref})
            ) AS matching
        FROM {child_ref}
        """
    ).fetchone()
    assert row is not None
    total_distinct, matching = row
    if not total_distinct:
        return 0.0
    return matching / total_distinct
