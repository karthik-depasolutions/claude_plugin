"""Deterministic join/foreign-key candidate detection.

Purely mechanical: name-based candidate generation, then value-overlap
verification via DuckDB. No LLM involved — the architecture doc is explicit
that structural facts must never depend on a model call.

Recall matters here (a missed relationship means the cookbook can't join
those tables), so candidate generation is generous - `customer_id`,
`customerId`, `customer`, `cust_ref`, `fk_customer` all match a `customers`
table with a `customer_id`/`id` primary key. Precision is then enforced by
the value-containment check, not by the name heuristic.

No relationship at all is a normal outcome, not an error.
"""

from __future__ import annotations

import re

import duckdb

from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import ColumnProfile, RelationshipCandidate

WEAK_OVERLAP = 0.80
STRONG_OVERLAP = 0.95
_SUFFIX_RE = re.compile(r"(_?(id|key|ref|fk|no|num|number))$", re.IGNORECASE)


def _singularize(name: str) -> str:
    low = name.lower()
    if low.endswith("ies") and len(low) > 3:
        return low[:-3] + "y"
    if low.endswith("ses") and len(low) > 3:
        return low[:-2]
    if low.endswith("s") and not low.endswith("ss"):
        return low[:-1]
    return low


def _stem(col_name: str) -> str:
    """A column's name with a trailing id/key/ref/fk/no/number token removed."""
    return _SUFFIX_RE.sub("", col_name.lower()).strip("_")


def _is_pk_candidate(col: ColumnProfile, row_count: int) -> bool:
    if not (col.is_likely_identifier and col.null_percent == 0.0 and row_count > 0):
        return False
    return col.cardinality == row_count


def _fk_name_matches(child: ColumnProfile, parent_table: str, parent_pk: ColumnProfile) -> bool:
    child_low = child.name.lower()
    child_stem = _stem(child.name)
    table_forms = {parent_table.lower(), _singularize(parent_table)}
    pk_forms = {parent_pk.name.lower(), _stem(parent_pk.name)}

    # exact PK-name reuse: customers.customer_id <- orders.customer_id ;
    # or <table>_id / <table>id / fk_<table> / <table>_ref / the bare table name
    exact_reuse = child_low in pk_forms and child_low not in {"id", "key", "ref"}
    return exact_reuse or child_stem in table_forms


_NON_FK_ROLES = {"free_text", "date", "datetime", "boolean_flag", "email", "phone"}


def _plausible_fk_dtype(child: ColumnProfile) -> bool:
    # An FK is an id-shaped column - never a free-text / date / bool / contact.
    return child.guessed_role.value not in _NON_FK_ROLES


def detect_relationships(
    data_source: DataSource,
    columns: list[ColumnProfile],
    con: duckdb.DuckDBPyConnection,
) -> list[RelationshipCandidate]:
    row_counts = {t.name: t.row_count for t in data_source.tables}
    physical_ref = {t.name: t.physical_ref for t in data_source.tables}
    by_table: dict[str, list[ColumnProfile]] = {}
    for c in columns:
        by_table.setdefault(c.table, []).append(c)

    parents: list[ColumnProfile] = [
        c for c in columns if _is_pk_candidate(c, row_counts.get(c.table, 0))
    ]

    seen: set[tuple[str, str, str, str]] = set()
    candidates: list[RelationshipCandidate] = []
    for parent in parents:
        for child_table, child_cols in by_table.items():
            if child_table == parent.table:
                continue
            for child in child_cols:
                key = (child.table, child.name, parent.table, parent.name)
                if key in seen:
                    continue
                if not _fk_name_matches(child, parent.table, parent):
                    continue
                if not _plausible_fk_dtype(child):
                    continue
                overlap = _overlap_ratio(
                    con, physical_ref[child.table], child.name, physical_ref[parent.table], parent.name
                )
                if overlap < WEAK_OVERLAP:
                    continue
                seen.add(key)
                strength = "strong" if overlap >= STRONG_OVERLAP else "weak"
                candidates.append(
                    RelationshipCandidate(
                        from_table=child.table,
                        from_column=child.name,
                        to_table=parent.table,
                        to_column=parent.name,
                        confidence=round(overlap, 3),
                        strength=strength,
                        evidence=(
                            f"{overlap * 100:.1f}% of distinct {child.table}.{child.name} values "
                            f"exist in {parent.table}.{parent.name} (a unique/PK-like column)"
                        ),
                    )
                )
    candidates.sort(key=lambda r: (r.from_table, r.from_column, r.to_table))
    return candidates


def _overlap_ratio(
    con: duckdb.DuckDBPyConnection, child_ref: str, child_col: str, parent_ref: str, parent_col: str
) -> float:
    child_q, parent_q = f'"{child_col}"', f'"{parent_col}"'
    try:
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
    except duckdb.Error:
        # Type mismatch between the two columns (e.g. VARCHAR vs BIGINT) - not a
        # real relationship, treat as no overlap rather than crashing the profile.
        return 0.0
    assert row is not None
    total_distinct, matching = row
    if not total_distinct:
        return 0.0
    return matching / total_distinct
