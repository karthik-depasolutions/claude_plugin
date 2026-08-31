"""Table grain inference — deterministic, used by the binding resolver to
pick a sensible `fact` table alias and by KPI compilation to sanity-check
that a KPI's stated grain is compatible with the table it targets.

Order of preference: a single-column unique identifier, then a 2- or
3-column combination that is unique, then "the whole row" as a fallback.
"""

from __future__ import annotations

from itertools import combinations

import duckdb

from forge_core.models.datasource import DataSource, TableDescriptor
from forge_core.models.schema_profile import ColumnProfile, TableGrain

# Composite-key search runs exact COUNT queries, so it can't be sampled - skip
# it on very large tables and fall back to full-row grain there.
COMPOSITE_KEY_MAX_ROWS = 2_000_000
MAX_COMPOSITE_CANDIDATES = 6


def _single_col_pk(table_cols: list[ColumnProfile], row_count: int) -> list[str]:
    return [
        c.name
        for c in table_cols
        if c.is_likely_identifier
        and c.null_percent == 0.0
        and c.cardinality == row_count
        and row_count > 0
    ]


def _composite_key(
    con: duckdb.DuckDBPyConnection, table: TableDescriptor, table_cols: list[ColumnProfile]
) -> list[str] | None:
    rc = table.row_count
    if not (0 < rc <= COMPOSITE_KEY_MAX_ROWS):
        return None
    # Non-null, discriminating, not free text. High cardinality first so the
    # smallest key is found with the fewest probes.
    candidates = sorted(
        (
            c
            for c in table_cols
            if c.null_percent == 0.0
            and 1 < c.cardinality < rc
            and c.guessed_role.value != "free_text"
        ),
        key=lambda c: c.cardinality,
        reverse=True,
    )[:MAX_COMPOSITE_CANDIDATES]
    ref = table.physical_ref
    for size in (2, 3):
        for combo in combinations(candidates, size):
            cols = ", ".join(f'"{c.name}"' for c in combo)
            try:
                distinct = con.execute(
                    f"SELECT COUNT(*) FROM (SELECT {cols} FROM {ref} GROUP BY {cols})"
                ).fetchone()
            except duckdb.Error:
                continue
            if distinct and distinct[0] == rc:
                return [c.name for c in combo]
    return None


def infer_grains(
    data_source: DataSource, columns: list[ColumnProfile], con: duckdb.DuckDBPyConnection
) -> list[TableGrain]:
    grains: list[TableGrain] = []
    for table in data_source.tables:
        table_cols = [c for c in columns if c.table == table.name]
        pk_cols = _single_col_pk(table_cols, table.row_count)
        if pk_cols:
            grains.append(
                TableGrain(
                    table=table.name,
                    grain_columns=pk_cols,
                    description=f"One row per unique {', '.join(pk_cols)}",
                    confidence=0.9,
                )
            )
            continue

        combo = _composite_key(con, table, table_cols)
        if combo:
            grains.append(
                TableGrain(
                    table=table.name,
                    grain_columns=combo,
                    description=f"One row per unique ({', '.join(combo)}) — inferred composite key",
                    confidence=0.7,
                )
            )
        else:
            grains.append(
                TableGrain(
                    table=table.name,
                    grain_columns=[c.name for c in table_cols],
                    description="No single- or multi-column unique key found; grain is the full row",
                    confidence=0.3,
                )
            )
    return grains
