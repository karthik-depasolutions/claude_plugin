"""Deterministic join/foreign-key candidate detection.

Purely mechanical: name-based candidate generation, then value-overlap
verification via DuckDB. No LLM involved — the architecture doc is explicit
that structural facts must never depend on a model call.

U2: adds containment-based and fuzzy-name detection beyond exact-name
matching, plus type-compatibility checks, to surface joins that don't share
identical column names (e.g. bookings.customer_id -> customers.id).
"""

from __future__ import annotations

import re
import sqlite3

import duckdb

from forge_core.models.common import SourceKind
from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import ColumnProfile, RelationshipCandidate

MIN_OVERLAP_RATIO = 0.5
MIN_OVERLAP_HIGH_CONF = 0.8
NAME_SIM_THRESHOLD = 0.3


def _tokens(name: str) -> set[str]:
    return set(re.split(r"[_\s]+", name.lower().strip()))


def _name_similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    # Substring bonus: "customer_id" contains "customer"
    al, bl = a.lower(), b.lower()
    if al in bl or bl in al:
        jaccard = max(jaccard, 0.6)
    # Suffix: "_id" columns often match bare "id"
    if al.endswith("_id") and bl == "id":
        jaccard = max(jaccard, 0.5)
    if bl.endswith("_id") and al == "id":
        jaccard = max(jaccard, 0.5)
    return jaccard


def _is_type_compatible(a_dtype: str, b_dtype: str) -> bool:
    def base(dtype: str) -> str:
        return dtype.split("(")[0].strip().upper()

    a_base, b_base = base(a_dtype), base(b_dtype)
    numeric = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "FLOAT", "DOUBLE", "DECIMAL", "REAL", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT"}
    temporal = {"DATE", "TIMESTAMP", "TIME", "TIMESTAMP WITH TIME ZONE"}
    varchar = {"VARCHAR", "TEXT", "CHAR", "NVARCHAR"}
    # Same family or both varchar-like
    if a_base in numeric and b_base in numeric:
        return True
    if a_base in temporal and b_base in temporal:
        return True
    if a_base in varchar and b_base in varchar:
        return True
    return a_base == b_base


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
    seen: set[tuple[str, str, str, str]] = set()
    for _name, cols in by_name.items():
        if len(cols) < 2:
            continue
        pk_cols = [c for c in cols if _is_pk_candidate(c, row_counts[c.table])]
        for parent in pk_cols:
            for child in cols:
                if child.table == parent.table:
                    continue
                key = (child.table, child.name, parent.table, parent.name)
                if key in seen:
                    continue
                seen.add(key)
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

    # U2 — fuzzy-name + containment detection for FKs that don't share exact names
    # e.g. enrollments.student_id -> students.id, or bookings.customer_id -> customers.customer_id
    pk_columns = [c for c in columns if _is_pk_candidate(c, row_counts[c.table])]
    for parent in pk_columns:
        for child in columns:
            if child.table == parent.table:
                continue
            key = (child.table, child.name, parent.table, parent.name)
            if key in seen:
                continue
            # Only consider child columns that look FK-ish or share name tokens
            name_sim = _name_similarity(child.name, parent.name)
            # Also consider parent table name vs child column name (e.g. customer_id vs customers)
            table_sim = _name_similarity(child.name, parent.table)
            best_sim = max(name_sim, table_sim)
            if best_sim < NAME_SIM_THRESHOLD:
                # Still allow high-containment pairs even with low name sim, but only
                # if child is identifier-like or low-cardinality FK candidate
                if not (child.is_likely_identifier or child.cardinality < row_counts[child.table] * 0.8):
                    continue
            if not _is_type_compatible(child.dtype, parent.dtype):
                continue
            # Quick containment pre-check: skip if child distinct > parent distinct * 1.2 (child can't be FK)
            if child.cardinality > row_counts[parent.table] * 1.2:
                continue
            overlap = _overlap_ratio(
                con, physical_ref[child.table], child.name, physical_ref[parent.table], parent.name
            )
            # High name similarity needs 0.5 overlap; low name sim needs 0.8 to avoid false positives
            threshold = MIN_OVERLAP_RATIO if best_sim >= 0.5 else MIN_OVERLAP_HIGH_CONF
            if overlap >= threshold:
                seen.add(key)
                candidates.append(
                    RelationshipCandidate(
                        from_table=child.table,
                        from_column=child.name,
                        to_table=parent.table,
                        to_column=parent.name,
                        confidence=round(overlap * (0.7 + 0.3 * best_sim), 3),
                        evidence=(
                            f"{overlap * 100:.1f}% of distinct {child.table}.{child.name} values "
                            f"exist in {parent.table}.{parent.name} (name_sim={best_sim:.2f}, type_compatible)"
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
                WHERE {child_q} IS NOT NULL AND CAST({child_q} AS VARCHAR) NOT IN (SELECT CAST({parent_q} AS VARCHAR) FROM {parent_ref} WHERE {parent_q} IS NOT NULL)
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
    # Cast to VARCHAR for cross-type FKs (e.g. INTEGER id vs VARCHAR foreign key)
    row = con.execute(
        f"""
        SELECT
            COUNT(DISTINCT {child_q}) FILTER (WHERE {child_q} IS NOT NULL) AS total_distinct,
            COUNT(DISTINCT {child_q}) FILTER (
                WHERE {child_q} IS NOT NULL AND CAST({child_q} AS VARCHAR) IN (SELECT CAST({parent_q} AS VARCHAR) FROM {parent_ref} WHERE {parent_q} IS NOT NULL)
            ) AS matching
        FROM {child_ref}
        """
    ).fetchone()
    assert row is not None
    total_distinct, matching = row
    if not total_distinct:
        return 0.0
    return matching / total_distinct
