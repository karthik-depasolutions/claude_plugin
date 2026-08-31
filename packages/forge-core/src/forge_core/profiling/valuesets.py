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
    data_source: DataSource, columns: list[ColumnProfile], con: duckdb.DuckDBPyConnection
) -> dict[str, list[str]]:
    physical_ref = {t.name: t.physical_ref for t in data_source.tables}
    out: dict[str, list[str]] = {}
    for col in columns:
        if col.guessed_role.value in _SKIP_ROLES:
            continue
        if not (0 < col.cardinality <= MAX_VALUE_SET_SIZE):
            continue
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
