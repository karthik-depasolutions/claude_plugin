"""Layer 1 — deterministic structural profiling. No LLM call in this module,
ever (see docs/architecture.md §4.2). Works uniformly across every table in
a possibly multi-table DataSource.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import duckdb

from forge_core.models.common import ColumnRole
from forge_core.models.datasource import DataSource, TableDescriptor
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile

if TYPE_CHECKING:
    from forge_core.ingestion.catalog import CatalogFacts

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


def _profile_table(
    con: duckdb.DuckDBPyConnection,
    table: TableDescriptor,
    *,
    declared_fk_columns: set[str] | None = None,
    distinct_query_budget: list[int] | None = None,
) -> list[ColumnProfile]:
    """Profile one table.

    `declared_fk_columns` is the set of column names (unqualified) that are
    declared FK participants for this table — they get `ColumnRole.FOREIGN_KEY`
    regardless of the name-heuristic result.

    `distinct_query_budget` is a single-element `[remaining]` list shared across
    all tables: while > 0 a non-numeric column gets a `SELECT DISTINCT ... LIMIT
    5` query for its sample values (and the counter decrements); once exhausted,
    sample values are read from `table.sample_rows` (fetched at INGEST) with no
    extra query. `None` = unlimited (legacy).
    """
    fk_cols = declared_fk_columns or set()
    ingest_samples: dict[str, list[str]] = {}
    for row in table.sample_rows[:5]:
        for k, v in row.items():
            if v is not None and len(ingest_samples.setdefault(k, [])) < 5:
                ingest_samples[k].append(str(v))
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
        for col in chunk:
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
                use_query = distinct_query_budget is None or distinct_query_budget[0] > 0
                if use_query:
                    quoted = f'"{col.name}"'
                    rows = con.execute(
                        f"SELECT DISTINCT {quoted} FROM {ref} WHERE {quoted} IS NOT NULL LIMIT 5"
                    ).fetchall()
                    sample_values = [str(r[0]) for r in rows]
                    if distinct_query_budget is not None:
                        distinct_query_budget[0] -= 1
                else:
                    sample_values = ingest_samples.get(col.name, [])

            null_percent = round((null_count / row_count * 100.0) if row_count else 0.0, 2)
            distinct_ratio = round((cardinality / row_count) if row_count else 0.0, 4)
            role = _guess_role(col.name, col.raw_dtype, cardinality, row_count)
            # Declared FK columns override the heuristic (fills the dead FOREIGN_KEY enum member).
            if col.name in fk_cols:
                role = ColumnRole.FOREIGN_KEY

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
                )
            )

    return profiles


def build_structural_profile(
    data_source: DataSource,
    con: duckdb.DuckDBPyConnection,
    *,
    catalog: CatalogFacts | None = None,  # type: ignore[name-defined]
    max_distinct_sample_columns: int | None = None,
) -> StructuralProfile:
    """Build the deterministic structural profile.

    Parameters
    ----------
    catalog:
        When supplied (agent_schema mode), declared FK columns in each table
        receive ``ColumnRole.FOREIGN_KEY`` instead of whatever the name-heuristic
        would guess.
    max_distinct_sample_columns:
        Cap on per-column ``SELECT DISTINCT`` sample-value queries across the
        whole source (``None`` = unlimited). The single biggest lever on a
        remote database - beyond the cap, sample values come from the INGEST
        rows with no extra query.
    """
    columns: list[ColumnProfile] = []
    # Build per-table FK column sets from the catalog.
    fk_sets: dict[str, set[str]] = {}
    if catalog:
        for fk in catalog.foreign_keys:
            fk_sets.setdefault(fk.from_table, set()).update(fk.from_columns)

    distinct_budget = (
        None if max_distinct_sample_columns is None else [max_distinct_sample_columns]
    )
    for table in data_source.tables:
        declared_fk_cols = fk_sets.get(table.name)
        columns.extend(
            _profile_table(
                con, table,
                declared_fk_columns=declared_fk_cols,
                distinct_query_budget=distinct_budget,
            )
        )
    return StructuralProfile(columns=columns)
