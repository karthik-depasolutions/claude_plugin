"""Table grain inference — deterministic, used by the binding resolver to
pick a sensible `fact` table alias and by KPI compilation to sanity-check
that a KPI's stated grain is compatible with the table it targets.

U2: now handles composite keys (2-3 columns) and near-unique (99%+)
detection. Requires a live DuckDB connection for the composite checks;
falls back to single-column logic when no connection is given.
"""

from __future__ import annotations

import itertools

import duckdb

from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import ColumnProfile, TableGrain


def _is_composite_unique(
    con: duckdb.DuckDBPyConnection, ref: str, cols: list[str]
) -> bool:
    quoted = " || '|' || ".join(f'CAST("{c}" AS VARCHAR)' for c in cols)
    row = con.execute(
        f"SELECT COUNT(*) AS total, COUNT(DISTINCT {quoted}) AS distinct_n FROM {ref}"
    ).fetchone()
    if row is None:
        return False
    total, distinct_n = row[0], row[1]
    return total > 0 and total == distinct_n


def infer_grains(
    data_source: DataSource,
    columns: list[ColumnProfile],
    con: duckdb.DuckDBPyConnection | None = None,
) -> list[TableGrain]:
    grains: list[TableGrain] = []
    physical_ref = {t.name: t.physical_ref for t in data_source.tables}
    for table in data_source.tables:
        table_cols = [c for c in columns if c.table == table.name]
        # Each of these is independently unique per row on its own - that
        # means the table's grain is "one row per X" for any one of them,
        # not a composite of all of them together (a real composite key is
        # only unique when its columns are combined - see the fallback
        # below for that case). Multiple independent single-column
        # candidates is now the common case since `is_likely_identifier`
        # is a genuine uniqueness fact rather than a `*_id`-name match, so
        # a customers table with unique customer_id AND unique email both
        # qualify - pick one, don't fabricate a fake composite grain out of
        # unrelated unique columns.
        pk_candidates = [
            c.name
            for c in table_cols
            if c.is_likely_identifier
            and c.null_percent == 0.0
            and c.cardinality == table.row_count
            and table.row_count > 0
        ]
        if pk_candidates:
            pk_col = pk_candidates[0]
            grains.append(
                TableGrain(
                    table=table.name,
                    grain_columns=[pk_col],
                    description=f"One row per unique {pk_col}",
                    confidence=0.9,
                )
            )
            continue

        # No single-column PK — try composite keys if we have a connection
        if con is not None and table.row_count > 0 and len(table_cols) >= 2:
            ref = physical_ref[table.name]
            # Candidate columns for composite: non-null, at least 2 distinct values,
            # not free-text with huge cardinality. Sorted by distinct_ratio desc.
            candidates = [
                c
                for c in table_cols
                if c.null_percent < 5.0 and c.cardinality > 1 and c.cardinality < table.row_count
            ]
            # Also include identifiers even if cardinality == row_count? No, already handled.
            # Prefer identifier / categorical / numeric over free_text
            candidates.sort(key=lambda c: (c.distinct_ratio, c.cardinality), reverse=True)
            candidates = candidates[:6]  # bound combinatorial explosion
            found = False
            # Try pairs first (most common: bridge tables = 2 FKs)
            for r in (2, 3):
                if found or len(candidates) < r:
                    break
                for combo in itertools.combinations(candidates, r):
                    col_names = [c.name for c in combo]
                    # Skip if any column has high null (already filtered) but double-check
                    if any(c.null_percent > 5.0 for c in combo):
                        continue
                    try:
                        if _is_composite_unique(con, ref, col_names):
                            # Check if this is a bridge (both columns are FK-like)
                            is_bridge = all(c.is_likely_identifier for c in combo)
                            desc = (
                                f"One row per unique combination of {', '.join(col_names)}"
                                + (" (bridge/junction table)" if is_bridge and r == 2 else "")
                            )
                            grains.append(
                                TableGrain(
                                    table=table.name,
                                    grain_columns=col_names,
                                    description=desc,
                                    confidence=0.85,
                                )
                            )
                            found = True
                            break
                    except Exception:
                        continue
                if found:
                    break
            if found:
                continue

            # Near-unique detection: 99%+ distinct ratio but not 100%
            near_unique = [c for c in table_cols if c.distinct_ratio >= 0.99 and c.cardinality < table.row_count]
            if near_unique:
                best = max(near_unique, key=lambda c: c.distinct_ratio)
                grains.append(
                    TableGrain(
                        table=table.name,
                        grain_columns=[best.name],
                        description=(
                            f"One row per {best.name} (near-unique: {best.distinct_ratio*100:.1f}% distinct, "
                            f"{table.row_count - best.cardinality} duplicates — check for dupes)"
                        ),
                        confidence=0.6,
                    )
                )
                continue

        grains.append(
            TableGrain(
                table=table.name,
                grain_columns=[c.name for c in table_cols],
                description="No single-column unique identifier found; grain is the full row",
                confidence=0.3,
            )
        )
    return grains
