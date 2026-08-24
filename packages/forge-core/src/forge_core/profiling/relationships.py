"""Deterministic join/foreign-key candidate detection.

Purely mechanical: name-based candidate generation, then value-overlap
verification via DuckDB. No LLM involved — the architecture doc is explicit
that structural facts must never depend on a model call.
"""

from __future__ import annotations

import sqlite3

import duckdb

from forge_core.models.common import SourceKind
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


def detect_declared_foreign_keys(data_source: DataSource) -> list[RelationshipCandidate]:
    """Ground truth read straight from the source database's own schema -
    zero inference, zero overlap query needed. Only implemented for SQLite
    today (verified against fixtures/datasets/edtech.sqlite's real declared
    FOREIGN KEY clauses via the raw sqlite3 module - DuckDB's sqlite_scanner
    exposes PRIMARY KEY through duckdb_constraints() but not FOREIGN KEY, so
    reading through DuckDB silently misses them). Every other source kind
    (CSV, Postgres, ...) falls back to detect_relationships' name+overlap
    inference - Postgres could read information_schema.key_column_usage the
    same way, but there is no live Postgres in this environment to verify
    that against, so it is deliberately left as a documented gap rather than
    shipped unverified."""
    if data_source.kind != SourceKind.SQLITE or not data_source.connection.original_paths:
        return []

    table_names = {t.name for t in data_source.tables}
    candidates: list[RelationshipCandidate] = []
    con = sqlite3.connect(data_source.connection.original_paths[0])
    try:
        for table in data_source.tables:
            for row in con.execute(f'PRAGMA foreign_key_list("{table.name}")').fetchall():
                # row: (id, seq, table, from, to, on_update, on_delete, match)
                to_table, from_col, to_col = row[2], row[3], row[4] or row[3]
                if to_table not in table_names:
                    continue
                candidates.append(
                    RelationshipCandidate(
                        from_table=table.name,
                        from_column=from_col,
                        to_table=to_table,
                        to_column=to_col,
                        confidence=1.0,
                        evidence=(
                            f"declared FOREIGN KEY in the source schema: "
                            f"{table.name}.{from_col} -> {to_table}.{to_col}"
                        ),
                    )
                )
    finally:
        con.close()
    return candidates


def detect_cardinality(
    con: duckdb.DuckDBPyConnection, candidate: RelationshipCandidate, physical_ref: dict[str, str]
) -> tuple[str, float]:
    """Runs the query that turns a candidate FK into a fact: is the child
    side (from_table.from_column) itself unique? If every non-null value is
    distinct, this edge is 1:1; otherwise it's N:1 from the child's
    perspective (many child rows per parent - the standard FK shape, and
    the direction detect_relationships/detect_declared_foreign_keys always
    produce). Also returns orphan_ratio - child values with no matching
    parent, surfaced as a data-quality signal by the caller."""
    child_ref, parent_ref = physical_ref[candidate.from_table], physical_ref[candidate.to_table]
    child_q, parent_q = f'"{candidate.from_column}"', f'"{candidate.to_column}"'
    row = con.execute(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE {child_q} IS NOT NULL) AS total,
            COUNT(DISTINCT {child_q}) FILTER (WHERE {child_q} IS NOT NULL) AS distinct_child,
            COUNT(*) FILTER (
                WHERE {child_q} IS NOT NULL AND {child_q} NOT IN (SELECT {parent_q} FROM {parent_ref})
            ) AS orphans
        FROM {child_ref}
        """
    ).fetchone()
    assert row is not None
    total, distinct_child, orphans = row
    orphan_ratio = round(orphans / total, 4) if total else 0.0
    cardinality = "1:1" if total > 0 and distinct_child == total else "N:1"
    return cardinality, orphan_ratio


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
