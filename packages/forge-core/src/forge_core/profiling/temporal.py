"""Temporal profiling — deterministic detection of time coverage.

For each table with DATE/DATETIME columns, computes:
- span (min to max)
- granularity (day / hour / month / year heuristic)
- gaps (missing periods if daily data has holes)
- distinct temporal values count
"""

from __future__ import annotations

import duckdb

from forge_core.models.common import ColumnRole
from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import ColumnProfile


def _detect_granularity(
    con: duckdb.DuckDBPyConnection, ref: str, col: str, distinct_n: int, total: int
) -> str | None:
    """Heuristic: sample distinct values and see their time components."""
    try:
        # Check if any value has non-midnight time component
        row = con.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE CAST("{col}" AS VARCHAR) LIKE '%:%') AS with_time,
                COUNT(DISTINCT DATE_TRUNC('month', "{col}")) AS distinct_months,
                COUNT(DISTINCT DATE_TRUNC('year', "{col}")) AS distinct_years
            FROM {ref} WHERE "{col}" IS NOT NULL
            """
        ).fetchone()
        if row is None:
            return None
        with_time, distinct_months, distinct_years = row
        if with_time and with_time > 0:
            # Has time component — check if hourly vs daily
            return "timestamp"
        # Date-only
        if distinct_years == 1 and distinct_months <= 1:
            return "day" if distinct_n > 10 else "month"
        if distinct_months and distinct_n / distinct_months < 2:
            return "month"
        if distinct_years and distinct_n / distinct_years < 13:
            return "month"
        return "day"
    except Exception:
        return None


def profile_temporal(
    data_source: DataSource,
    columns: list[ColumnProfile],
    con: duckdb.DuckDBPyConnection,
) -> dict[str, dict]:
    """Returns {table_name: {span, granularity, gaps, column, min, max, distinct}}"""
    result: dict[str, dict] = {}
    by_table: dict[str, list[ColumnProfile]] = {}
    for c in columns:
        if c.guessed_role in (ColumnRole.DATE, ColumnRole.DATETIME):
            by_table.setdefault(c.table, []).append(c)

    physical_ref = {t.name: t.physical_ref for t in data_source.tables}
    row_count_by_table = {t.name: t.row_count for t in data_source.tables}

    for table, cols in by_table.items():
        ref = physical_ref.get(table)
        if not ref:
            continue
        # Pick primary temporal column: lowest null%, highest cardinality
        cols_sorted = sorted(cols, key=lambda c: (c.null_percent, -c.cardinality))
        primary = cols_sorted[0]
        try:
            row = con.execute(
                f"""
                SELECT
                    MIN("{primary.name}") AS min_v,
                    MAX("{primary.name}") AS max_v,
                    COUNT(DISTINCT "{primary.name}") AS distinct_n,
                    COUNT(*) FILTER (WHERE "{primary.name}" IS NOT NULL) AS non_null
                FROM {ref}
                """
            ).fetchone()
            if row is None:
                continue
            min_v, max_v, distinct_n, non_null = row
            span = f"{min_v} to {max_v}" if min_v and max_v else None
            granularity = _detect_granularity(con, ref, primary.name, distinct_n or 0, non_null or 0) if distinct_n else None

            # Gap detection for daily granularity: expected days vs actual distinct
            gaps: list[str] = []
            if granularity == "day" and min_v and max_v and distinct_n:
                try:
                    gap_row = con.execute(
                        f"""
                        SELECT
                            CAST(MAX("{primary.name}") AS DATE) - CAST(MIN("{primary.name}") AS DATE) + 1 AS span_days,
                            COUNT(DISTINCT CAST("{primary.name}" AS DATE)) AS distinct_days
                        FROM {ref} WHERE "{primary.name}" IS NOT NULL
                        """
                    ).fetchone()
                    if gap_row and gap_row[0] and gap_row[1]:
                        span_days, distinct_days = gap_row[0], gap_row[1]
                        if span_days and distinct_days and span_days - distinct_days > max(2, span_days * 0.05):
                            gaps.append(f"{span_days - distinct_days} missing days in {span_days}-day span")
                except Exception:
                    pass

            result[table] = {
                "column": primary.name,
                "span": span,
                "granularity": granularity,
                "gaps": gaps,
                "min": str(min_v) if min_v else None,
                "max": str(max_v) if max_v else None,
                "distinct": distinct_n,
            }
        except Exception:
            continue

    return result


__all__ = ["profile_temporal"]
