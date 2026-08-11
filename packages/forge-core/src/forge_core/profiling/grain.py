"""Table grain inference — deterministic, used by the binding resolver to
pick a sensible `fact` table alias and by KPI compilation to sanity-check
that a KPI's stated grain is compatible with the table it targets."""

from __future__ import annotations

from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import ColumnProfile, TableGrain


def infer_grains(data_source: DataSource, columns: list[ColumnProfile]) -> list[TableGrain]:
    grains: list[TableGrain] = []
    for table in data_source.tables:
        table_cols = [c for c in columns if c.table == table.name]
        pk_cols = [
            c.name
            for c in table_cols
            if c.is_likely_identifier
            and c.null_percent == 0.0
            and c.cardinality == table.row_count
            and table.row_count > 0
        ]
        if pk_cols:
            grains.append(
                TableGrain(
                    table=table.name,
                    grain_columns=pk_cols,
                    description=f"One row per unique {', '.join(pk_cols)}",
                    confidence=0.9,
                )
            )
        else:
            grains.append(
                TableGrain(
                    table=table.name,
                    grain_columns=[c.name for c in table_cols],
                    description="No single-column unique identifier found; grain is the full row",
                    confidence=0.3,
                )
            )
    return grains
