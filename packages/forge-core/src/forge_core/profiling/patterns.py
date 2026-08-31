"""Deterministic statistical pattern mining — Stage 2f.

Raw numbers only; interpretation ("seasonally adjust before reporting YoY")
is the synthesis pass's job. Kinds:

  correlations            - numeric column pairs that move together
  temporal                - row volume over time: monthly curve, trend,
                            seasonality, day-of-week, year-over-year growth
  functional_dependencies - column A's value fixes column B's (hidden hierarchy)
  redundancies            - two columns that are always equal
  mismatches              - a stored aggregate on the parent side of a
                            relationship that disagrees with re-aggregating
                            the child rows (denormalization gone stale)
  segments                - how a table's rows break down across a dimension

Correlations and temporal are single-scan aggregates and run on any table.
The O(cols^2) / relationship probes are skipped above `PROBE_MAX_ROWS`.
"""

from __future__ import annotations

import re
import statistics

import duckdb

from forge_core.models.datasource import DataSource, TableDescriptor
from forge_core.models.schema_profile import (
    ColumnProfile,
    Correlation,
    DenormalizationMismatch,
    FunctionalDependency,
    PatternsRaw,
    RedundantColumns,
    RelationshipCandidate,
    Segment,
    TemporalPattern,
)

PROBE_MAX_ROWS = 2_000_000
MIN_CORRELATION_N = 20
MIN_ABS_R = 0.5
MAX_NUMERIC_COLS = 12
MAX_LOWCARD_COLS = 8
MAX_FINDINGS_PER_KIND_PER_TABLE = 10
_NUMERIC_ROLES = {"numeric", "currency"}
_TEMPORAL_ROLES = {"date", "datetime"}
_DIMENSION_ROLES = {"categorical", "geographic", "boolean_flag"}
_TOTAL_NAME_RE = re.compile(r"(total|amount|subtotal|grand|sum|revenue|value|price|cost)$", re.IGNORECASE)
_QTY_NAME_RE = re.compile(r"(qty|quantity|count|units|num|number)$", re.IGNORECASE)
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def mine_patterns(
    data_source: DataSource,
    columns: list[ColumnProfile],
    con: duckdb.DuckDBPyConnection,
    relationships: list[RelationshipCandidate] | None = None,
) -> PatternsRaw:
    by_table: dict[str, list[ColumnProfile]] = {}
    for c in columns:
        by_table.setdefault(c.table, []).append(c)

    raw = PatternsRaw()
    for table in data_source.tables:
        cols = by_table.get(table.name, [])
        raw.correlations.extend(_correlations(con, table, cols))
        raw.temporal.extend(_temporal(con, table, cols))
        raw.segments.extend(_segments(con, table, cols))
        if 0 < table.row_count <= PROBE_MAX_ROWS:
            raw.functional_dependencies.extend(_functional_dependencies(con, table, cols))
            raw.redundancies.extend(_redundancies(con, table, cols))

    raw.mismatches.extend(
        _denormalization_mismatches(data_source, by_table, relationships or [], con)
    )
    return raw


def _correlations(
    con: duckdb.DuckDBPyConnection, table: TableDescriptor, cols: list[ColumnProfile]
) -> list[Correlation]:
    numeric = sorted(
        (c for c in cols if c.guessed_role.value in _NUMERIC_ROLES and c.cardinality > 1),
        key=lambda c: c.cardinality,
        reverse=True,
    )[:MAX_NUMERIC_COLS]
    if len(numeric) < 2:
        return []
    ref = table.physical_ref
    pairs = [(a, b) for i, a in enumerate(numeric) for b in numeric[i + 1 :]]
    corr_exprs = ", ".join(
        f'corr("{a.name}", "{b.name}") AS r_{i}' for i, (a, b) in enumerate(pairs)
    )
    count_exprs = ", ".join(f'count("{c.name}") AS n_{i}' for i, c in enumerate(numeric))
    try:
        corr_row = con.execute(f"SELECT {corr_exprs} FROM {ref}").fetchone()
        count_row = con.execute(f"SELECT {count_exprs} FROM {ref}").fetchone()
    except duckdb.Error:
        return []
    counts = {c.name: int(count_row[i] or 0) for i, c in enumerate(numeric)}

    out: list[Correlation] = []
    for i, (a, b) in enumerate(pairs):
        r = corr_row[i]
        n = min(counts[a.name], counts[b.name])
        if r is None or n < MIN_CORRELATION_N or abs(r) < MIN_ABS_R:
            continue
        out.append(
            Correlation(table=table.name, column_a=a.name, column_b=b.name, pearson_r=round(r, 3), n=n)
        )
    out.sort(key=lambda c: abs(c.pearson_r), reverse=True)
    return out[:MAX_FINDINGS_PER_KIND_PER_TABLE]


def _temporal(
    con: duckdb.DuckDBPyConnection, table: TableDescriptor, cols: list[ColumnProfile]
) -> list[TemporalPattern]:
    out: list[TemporalPattern] = []
    for col in cols:
        if col.guessed_role.value not in _TEMPORAL_ROLES:
            continue
        q = f'"{col.name}"'
        ts = f"CAST({q} AS TIMESTAMP)"
        try:
            rows = con.execute(
                f"SELECT strftime({ts}, '%Y-%m') AS m, COUNT(*) AS n "
                f"FROM {table.physical_ref} WHERE {q} IS NOT NULL GROUP BY 1 ORDER BY 1"
            ).fetchall()
            dow_rows = con.execute(
                f"SELECT dayofweek({ts}) AS d, COUNT(*) AS n "
                f"FROM {table.physical_ref} WHERE {q} IS NOT NULL GROUP BY 1"
            ).fetchall()
        except duckdb.Error:
            continue
        buckets = {str(m): int(n) for m, n in rows if m is not None}
        if len(buckets) < 3:
            continue
        # DuckDB dayofweek: 0 = Sunday .. 6 = Saturday
        dow = {_WEEKDAYS[(int(d) + 6) % 7]: int(n) for d, n in dow_rows if d is not None}
        out.append(
            TemporalPattern(
                table=table.name,
                column=col.name,
                buckets=buckets,
                trend=_trend(list(buckets.values())),
                seasonal=_seasonal(list(buckets.values())),
                day_of_week={wd: dow.get(wd, 0) for wd in _WEEKDAYS if wd in dow},
                year_over_year=_year_over_year(buckets),
            )
        )
    return out


def _year_over_year(monthly: dict[str, int]) -> dict[str, float]:
    by_year: dict[str, int] = {}
    for month, n in monthly.items():
        by_year[month[:4]] = by_year.get(month[:4], 0) + n
    years = sorted(by_year)
    out: dict[str, float] = {}
    for prev, cur in zip(years, years[1:], strict=False):
        base = by_year[prev]
        if base:
            out[cur] = round(by_year[cur] / base, 3)
    return out


def _trend(counts: list[int]) -> str:
    third = max(1, len(counts) // 3)
    first = statistics.fmean(counts[:third])
    last = statistics.fmean(counts[-third:])
    if first == 0:
        return "rising" if last > 0 else "flat"
    if last >= first * 1.15:
        return "rising"
    if last <= first * 0.85:
        return "falling"
    return "flat"


def _seasonal(counts: list[int]) -> bool:
    if len(counts) < 12:
        return False
    mean = statistics.fmean(counts)
    if mean == 0:
        return False
    return statistics.pstdev(counts) / mean > 0.25


def _lowcard(cols: list[ColumnProfile]) -> list[ColumnProfile]:
    return sorted(
        (c for c in cols if 2 <= c.cardinality <= 50 and c.guessed_role.value != "free_text"),
        key=lambda c: c.cardinality,
    )[:MAX_LOWCARD_COLS]


def _functional_dependencies(
    con: duckdb.DuckDBPyConnection, table: TableDescriptor, cols: list[ColumnProfile]
) -> list[FunctionalDependency]:
    candidates = _lowcard(cols)
    ref = table.physical_ref
    out: list[FunctionalDependency] = []
    for a in candidates:
        for b in candidates:
            if a.name == b.name or a.cardinality < b.cardinality:
                continue  # a finer-grained column can't be functionally determined by a coarser one
            try:
                row = con.execute(
                    f'SELECT MAX(cnt) FROM (SELECT COUNT(DISTINCT "{b.name}") AS cnt '
                    f'FROM {ref} WHERE "{a.name}" IS NOT NULL GROUP BY "{a.name}")'
                ).fetchone()
            except duckdb.Error:
                continue
            if row and row[0] == 1:
                out.append(FunctionalDependency(table=table.name, determinant=a.name, dependent=b.name))
                if len(out) >= MAX_FINDINGS_PER_KIND_PER_TABLE:
                    return out
    return out


def _redundancies(
    con: duckdb.DuckDBPyConnection, table: TableDescriptor, cols: list[ColumnProfile]
) -> list[RedundantColumns]:
    by_role: dict[str, list[ColumnProfile]] = {}
    for c in cols:
        if c.guessed_role.value == "identifier":
            continue
        by_role.setdefault(c.guessed_role.value, []).append(c)
    ref = table.physical_ref
    out: list[RedundantColumns] = []
    for group in by_role.values():
        # Cap the per-role comparison set - an exact-equality probe is O(n^2)
        # queries and a genuinely redundant pair almost always has the same
        # cardinality, so compare within cardinality-ordered neighbours only.
        capped = sorted(group, key=lambda c: c.cardinality)[:MAX_LOWCARD_COLS]
        for i, a in enumerate(capped):
            for b in capped[i + 1 :]:
                if a.cardinality != b.cardinality:
                    continue
                try:
                    row = con.execute(
                        f'SELECT COUNT(*) FROM {ref} WHERE "{a.name}" IS DISTINCT FROM "{b.name}"'
                    ).fetchone()
                except duckdb.Error:
                    continue
                if row and row[0] == 0:
                    out.append(
                        RedundantColumns(
                            table=table.name,
                            column_a=a.name,
                            column_b=b.name,
                            note=f'"{a.name}" and "{b.name}" hold identical values in every row',
                        )
                    )
                    if len(out) >= MAX_FINDINGS_PER_KIND_PER_TABLE:
                        return out
    return out


def _segments(
    con: duckdb.DuckDBPyConnection, table: TableDescriptor, cols: list[ColumnProfile]
) -> list[Segment]:
    if table.row_count < 20:
        return []
    dims = sorted(
        (
            c
            for c in cols
            if c.guessed_role.value in _DIMENSION_ROLES
            and 2 <= c.cardinality <= 20
            and not c.is_likely_identifier
        ),
        # prefer ~6 groups: enough to be interesting, few enough to read
        key=lambda c: abs(c.cardinality - 6),
    )[:3]
    ref = table.physical_ref
    out: list[Segment] = []
    for col in dims:
        q = f'"{col.name}"'
        try:
            rows = con.execute(
                f"SELECT CAST({q} AS VARCHAR) AS v, COUNT(*) AS n FROM {ref} "
                f"WHERE {q} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 6"
            ).fetchall()
            total = con.execute(f"SELECT COUNT(*) FROM {ref} WHERE {q} IS NOT NULL").fetchone()
        except duckdb.Error:
            continue
        n_total = int(total[0]) if total else 0
        if n_total == 0 or not rows:
            continue
        groups = [(str(v), round(int(n) / n_total, 3)) for v, n in rows][:5]
        top1 = groups[0][1]
        top3 = sum(s for _, s in groups[:3])
        concentration = "high" if top1 >= 0.5 else "moderate" if top3 >= 0.8 else "even"
        out.append(
            Segment(table=table.name, dimension=col.name, top_groups=groups, concentration=concentration)
        )
    return out


def _denormalization_mismatches(
    data_source: DataSource,
    by_table: dict[str, list[ColumnProfile]],
    relationships: list[RelationshipCandidate],
    con: duckdb.DuckDBPyConnection,
) -> list[DenormalizationMismatch]:
    ref = {t.name: t.physical_ref for t in data_source.tables}
    row_count = {t.name: t.row_count for t in data_source.tables}
    out: list[DenormalizationMismatch] = []

    for rel in relationships:
        parent, child = rel.to_table, rel.from_table
        if parent not in ref or child not in ref:
            continue
        if max(row_count.get(parent, 0), row_count.get(child, 0)) > PROBE_MAX_ROWS:
            continue
        child_cols = by_table.get(child, [])
        amount = next(
            (
                c
                for c in child_cols
                if c.guessed_role.value in _NUMERIC_ROLES and _TOTAL_NAME_RE.search(c.name)
            ),
            None,
        )
        if amount is None:
            continue
        qty = next(
            (c for c in child_cols if c.guessed_role.value == "numeric" and _QTY_NAME_RE.search(c.name)),
            None,
        )
        child_expr = f'"{amount.name}" * "{qty.name}"' if qty else f'"{amount.name}"'

        for pc in by_table.get(parent, []):
            if pc.guessed_role.value not in _NUMERIC_ROLES or not _TOTAL_NAME_RE.search(pc.name):
                continue
            sub = (
                f'(SELECT COALESCE(SUM({child_expr}), 0) FROM {ref[child]} c '
                f'WHERE c."{rel.from_column}" = p."{rel.to_column}")'
            )
            try:
                row = con.execute(
                    f'SELECT COUNT(*) FILTER (WHERE ABS(COALESCE(p."{pc.name}", 0) - {sub}) > 0.01) AS bad, '
                    f'COUNT(*) AS total FROM {ref[parent]} p'
                ).fetchone()
            except duckdb.Error:
                continue
            if not row or not row[1] or row[0] == 0:
                continue
            out.append(
                DenormalizationMismatch(
                    parent_table=parent,
                    parent_column=pc.name,
                    child_table=child,
                    child_expression=f"SUM({child_expr}) over {child}.{rel.from_column}",
                    mismatched_rows=int(row[0]),
                    checked_rows=int(row[1]),
                    example=f'{row[0]} of {row[1]} {parent} rows disagree with re-summed {child}',
                )
            )
            if len(out) >= MAX_FINDINGS_PER_KIND_PER_TABLE:
                return out
    return out
