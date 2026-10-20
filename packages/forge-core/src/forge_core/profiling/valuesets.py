"""Full distinct value sets for low-cardinality columns — deterministic.

`ColumnProfile.sample_values` is only 5 arbitrary values; that's not enough
for the synthesis pass to write an enum decode ("status: 1=pending,
2=confirmed, ..."). This captures the *complete* set for any column with few
enough distinct values to be an enum/category, keyed "table.column".
"""

from __future__ import annotations

import duckdb

from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import ColumnProfile

MAX_VALUE_SET_SIZE = 50
_SKIP_ROLES = {"free_text", "identifier", "email", "phone", "date", "datetime"}


def capture_value_sets(
    data_source: DataSource,
    columns: list[ColumnProfile],
    con: duckdb.DuckDBPyConnection,
    *,
    max_columns: int | None = None,
) -> dict[str, list[str]]:
    """`max_columns` caps how many columns get a `SELECT DISTINCT` probe (one
    query each) - `None` keeps the legacy unbounded behaviour. Under a cap the
    lowest-cardinality eligible columns (the most enum-like) are probed first."""
    physical_ref = {t.name: t.physical_ref for t in data_source.tables}
    eligible = [
        col
        for col in columns
        if col.guessed_role.value not in _SKIP_ROLES and 0 < col.cardinality <= MAX_VALUE_SET_SIZE
    ]
    if max_columns is not None:
        eligible.sort(key=lambda c: c.cardinality)
        eligible = eligible[:max_columns]
    out: dict[str, list[str]] = {}
    for col in eligible:
        ref = physical_ref.get(col.table)
        if ref is None:
            continue
        quoted = f'"{col.name}"'
        try:
            rows = con.execute(
                f"SELECT DISTINCT {quoted} FROM {ref} WHERE {quoted} IS NOT NULL "
                f"ORDER BY 1 LIMIT {MAX_VALUE_SET_SIZE + 1}"
            ).fetchall()
        except duckdb.Error:
            continue
        if len(rows) <= MAX_VALUE_SET_SIZE:
            out[f"{col.table}.{col.name}"] = [str(r[0]) for r in rows]
    return out
