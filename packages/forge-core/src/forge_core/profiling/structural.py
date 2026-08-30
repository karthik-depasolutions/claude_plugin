"""Layer 1 — deterministic structural profiling. No LLM call in this module,
ever (see docs/architecture.md §4.2). Works uniformly across every table in
a possibly multi-table DataSource.
"""

from __future__ import annotations

import re

import duckdb

from forge_core.models.common import ColumnRole
from forge_core.models.datasource import DataSource, TableDescriptor
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile

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


def _profile_table(
    con: duckdb.DuckDBPyConnection, table: TableDescriptor
) -> list[ColumnProfile]:
    ref = table.physical_ref
    row_count = table.row_count
    if not table.columns:
        return []

    # Batch column stats in chunks of 25 to avoid re-scanning views 100s of times
    profiles: list[ColumnProfile] = []
    chunk_size = 25
    for i in range(0, len(table.columns), chunk_size):
        chunk = table.columns[i : i + chunk_size]
        select_exprs = []
        for idx, col in enumerate(chunk):
            quoted = f'"{col.name}"'
            base = _base_type(col.raw_dtype)
            select_exprs.append(f"COUNT(*) FILTER (WHERE {quoted} IS NULL) AS n_{idx}")
            select_exprs.append(f"COUNT(DISTINCT {quoted}) AS c_{idx}")
            if base in _NUMERIC_DUCKDB_TYPES:
                select_exprs.append(f"MIN({quoted}) AS min_{idx}")
                select_exprs.append(f"MAX({quoted}) AS max_{idx}")

        stats_sql = f"SELECT {', '.join(select_exprs)} FROM {ref}"
        res = con.execute(stats_sql).fetchone()
        assert res is not None

        res_idx = 0
        for idx, col in enumerate(chunk):
            null_count = int(res[res_idx] or 0)
            cardinality = int(res[res_idx + 1] or 0)
            res_idx += 2

            base = _base_type(col.raw_dtype)
            min_value = max_value = None
            sample_values: list[str] = []
            if base in _NUMERIC_DUCKDB_TYPES:
                min_value, max_value = res[res_idx], res[res_idx + 1]
                res_idx += 2
            elif row_count > 0:
                quoted = f'"{col.name}"'
                rows = con.execute(
                    f"SELECT DISTINCT {quoted} FROM {ref} WHERE {quoted} IS NOT NULL LIMIT 5"
                ).fetchall()
                sample_values = [str(r[0]) for r in rows]

            null_percent = round((null_count / row_count * 100.0) if row_count else 0.0, 2)
            distinct_ratio = round((cardinality / row_count) if row_count else 0.0, 4)
            role = _guess_role(col.name, col.raw_dtype, cardinality, row_count)

            profiles.append(
                ColumnProfile(
                    table=table.name,
                    name=col.name,
                    dtype=col.raw_dtype,
                    null_percent=null_percent,
                    cardinality=cardinality,
                    distinct_ratio=min(distinct_ratio, 1.0),
                    guessed_role=role,
                    min_value=min_value,
                    max_value=max_value,
                    sample_values=sample_values,
                    is_likely_identifier=role == ColumnRole.IDENTIFIER,
                    is_likely_pii=_is_likely_pii(col.name, role),
                )
            )

    return profiles


def build_structural_profile(data_source: DataSource, con: duckdb.DuckDBPyConnection) -> StructuralProfile:
    columns: list[ColumnProfile] = []
    for table in data_source.tables:
        columns.extend(_profile_table(con, table))
    return StructuralProfile(columns=columns)
