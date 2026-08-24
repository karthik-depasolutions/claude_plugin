"""Layer 1 — deterministic structural profiling. No LLM call in this module,
ever (see docs/architecture.md §4.2). Works uniformly across every table in
a possibly multi-table DataSource.
"""

from __future__ import annotations

import re

import duckdb

from forge_core.models.common import ColumnRole
from forge_core.models.datasource import DataSource, TableDescriptor
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile, TableGrain

_NAME_PATTERNS: list[tuple[re.Pattern[str], ColumnRole]] = [
    (re.compile(r"email"), ColumnRole.EMAIL),
    (re.compile(r"phone|mobile|contact_no"), ColumnRole.PHONE),
    (re.compile(r"^id$|_id$|^uuid$|_uuid$|_key$"), ColumnRole.IDENTIFIER),
    (re.compile(r"date|_at$|_on$|timestamp"), ColumnRole.DATE),
    (re.compile(r"amount|price|cost|revenue|fee|salary|total|balance|inr|usd"), ColumnRole.CURRENCY),
    (re.compile(r"^is_|^has_|_flag$|active$"), ColumnRole.BOOLEAN_FLAG),
    (re.compile(r"city|state|country|region|pincode|zip|postal|address"), ColumnRole.GEOGRAPHIC),
]
# Deliberately NOT matched by name pattern alone: "*_name" columns. Whether a
# name-like column is a low-cardinality dimension (package_name, lab_partner)
# or genuine free text (customer_name) depends on cardinality, not the column
# name — so it's decided by the fallback logic in _guess_role below.

_PERSON_NAME_HINTS = re.compile(
    r"(customer|patient|guardian|contact|user|full|first|last|student|employee|client|"
    r"account_holder|owner)[_\s]*name|^name$",
    re.IGNORECASE,
)
_OTHER_PII_HINTS = re.compile(
    r"phone|email|address|dob|birth|ssn|aadhaar|pan_number|passport|account_number|card_number",
    re.IGNORECASE,
)

_NUMERIC_DUCKDB_TYPES = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
    "UINTEGER", "UBIGINT", "FLOAT", "DOUBLE", "DECIMAL", "REAL",
}
_TEMPORAL_DUCKDB_TYPES = {"DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIME"}
_BOOLEAN_DUCKDB_TYPES = {"BOOLEAN"}


def _base_type(dtype: str) -> str:
    return dtype.split("(", maxsplit=1)[0].strip().upper()


def _guess_role(name: str, dtype: str, cardinality: int, row_count: int) -> ColumnRole:
    lower = name.lower()
    base = _base_type(dtype)

    if base in _BOOLEAN_DUCKDB_TYPES:
        return ColumnRole.BOOLEAN_FLAG
    if base in _TEMPORAL_DUCKDB_TYPES:
        return ColumnRole.DATETIME if "TIMESTAMP" in base or "TIME" in base else ColumnRole.DATE

    for pattern, role in _NAME_PATTERNS:
        if pattern.search(lower):
            if role == ColumnRole.CURRENCY and base not in _NUMERIC_DUCKDB_TYPES:
                continue
            return role

    if base in _NUMERIC_DUCKDB_TYPES:
        if row_count and cardinality <= 2:
            return ColumnRole.BOOLEAN_FLAG
        return ColumnRole.NUMERIC

    if row_count and cardinality <= max(1, min(30, row_count // 2)):
        return ColumnRole.CATEGORICAL

    return ColumnRole.FREE_TEXT


def _is_likely_pii(name: str, role: ColumnRole) -> bool:
    if role in (ColumnRole.EMAIL, ColumnRole.PHONE):
        return True
    lower = name.lower()
    return bool(_PERSON_NAME_HINTS.search(lower) or _OTHER_PII_HINTS.search(lower))


def _profile_column(
    con: duckdb.DuckDBPyConnection, table: TableDescriptor, col_name: str, dtype: str, row_count: int
) -> ColumnProfile:
    ref = table.physical_ref
    quoted = f'"{col_name}"'
    stats = con.execute(
        f"SELECT COUNT(*) FILTER (WHERE {quoted} IS NULL), COUNT(DISTINCT {quoted}) FROM {ref}"
    ).fetchone()
    assert stats is not None  # an aggregate query always returns exactly one row
    null_count, cardinality = int(stats[0]), int(stats[1])
    null_percent = round((null_count / row_count * 100.0) if row_count else 0.0, 2)
    distinct_ratio = round((cardinality / row_count) if row_count else 0.0, 4)

    role = _guess_role(col_name, dtype, cardinality, row_count)

    min_value = max_value = None
    sample_values: list[str] = []
    base = _base_type(dtype)
    if base in _NUMERIC_DUCKDB_TYPES:
        mm = con.execute(f"SELECT MIN({quoted}), MAX({quoted}) FROM {ref}").fetchone()
        assert mm is not None
        min_value, max_value = mm[0], mm[1]
    else:
        rows = con.execute(
            f"SELECT DISTINCT {quoted} FROM {ref} WHERE {quoted} IS NOT NULL LIMIT 5"
        ).fetchall()
        sample_values = [str(r[0]) for r in rows]

    return ColumnProfile(
        table=table.name,
        name=col_name,
        dtype=dtype,
        null_percent=null_percent,
        cardinality=cardinality,
        distinct_ratio=min(distinct_ratio, 1.0),
        guessed_role=role,
        min_value=min_value,
        max_value=max_value,
        sample_values=sample_values,
        is_likely_identifier=role == ColumnRole.IDENTIFIER,
        is_likely_pii=_is_likely_pii(col_name, role),
    )


def reclassify_dimension_labels(
    data_source: DataSource, columns: list[ColumnProfile], grains: list[TableGrain]
) -> list[ColumnProfile]:
    """P2-02, grain-aware second pass over FREE_TEXT columns only - runs
    after grain inference, since it needs grains that depend on the columns
    it's revising. `_guess_role`'s `row_count // 2` rule is exactly inverted
    for a dimension table: every genuine dimension label has
    cardinality == row_count BY DESIGN (one row per distinct thing), which
    is precisely what made `courses.course_name` (4/4 rows) fail the old
    test and get destroyed (review P1.2). This is deliberately just a
    reclassification of FREE_TEXT -> CATEGORICAL, never the reverse -
    nothing here can make a column MORE likely to be denied."""
    grain_by_table = {g.table: g for g in grains}
    row_count_by_table = {t.name: t.row_count for t in data_source.tables}
    updated: list[ColumnProfile] = []
    for col in columns:
        # Never promote a PII-shaped column - low cardinality (a small
        # customer roster) is not what makes a personal-name column a
        # legitimate dimension to group by. Denial already keys off
        # is_likely_pii independently of guessed_role, so this guard is
        # about keeping the role label itself honest, not a safety fix.
        if col.guessed_role != ColumnRole.FREE_TEXT or col.is_likely_pii:
            updated.append(col)
            continue

        row_count = row_count_by_table.get(col.table, 0)
        grain = grain_by_table.get(col.table)
        is_dimension_shaped = grain is not None and grain.confidence >= 0.8 and len(grain.grain_columns) == 1
        avg_length = (
            sum(len(v) for v in col.sample_values) / len(col.sample_values) if col.sample_values else 0.0
        )

        promote = (is_dimension_shaped and col.cardinality == row_count and avg_length < 60) or (
            row_count > 0 and col.cardinality <= max(2, min(50, int(row_count**0.5) * 3))
        )
        updated.append(col.model_copy(update={"guessed_role": ColumnRole.CATEGORICAL}) if promote else col)
    return updated


def build_structural_profile(data_source: DataSource, con: duckdb.DuckDBPyConnection) -> StructuralProfile:
    columns: list[ColumnProfile] = []
    for table in data_source.tables:
        for col in table.columns:
            columns.append(_profile_column(con, table, col.name, col.raw_dtype, table.row_count))
    return StructuralProfile(columns=columns)
