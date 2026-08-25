"""P2-04 — the six-tool semantic investigation surface for the P2-05
data-understanding agent. `agentic/tools.py::preview_column_values` is
exactly the right pattern already in this repo (SQL inside the tool, agent
picks the column, column validated against the real schema before
interpolation) - this module generalizes it.

The safety boundary, the same one every tool in this package already keeps:
the agent supplies **parameters, never SQL text**. Every identifier is
checked against the real schema before interpolation; unknown -> error
naming valid options. Every literal is a bound parameter. Denied columns are
refused inside the tool (extends P1-02's all-clause walk to this surface).
Row limits are enforced in one place. This is what lets the agent roam
freely without the freedom extending to *what gets executed* - freedom over
what to look at, no freedom over what to run.
"""

from __future__ import annotations

from typing import Any, Literal

import duckdb
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from forge_core.models.datasource import DataSource
from forge_core.models.entity_graph import EntityGraph
from forge_core.models.metrics import AggOp, render_aggregation
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile
from forge_core.profiling.relationships import detect_cardinality
from forge_core.runtime_session import open_session

MAX_SAMPLE_ROWS = 15
MAX_DISTINCT_VALUES = 200
MAX_GROUP_ROWS = 50


class AllowlistViolation(ValueError):
    """Raised (and caught into a tool-result error string, never propagated
    to the agent loop as an exception) when a requested table/column isn't
    real or is denied."""


class _Toolkit:
    """Validates every (table, column) pair against the real schema and
    denied-column list before any query runs - the one place all six tools
    share that check, so it can't drift between them."""

    def __init__(
        self,
        data_source: DataSource,
        structural: StructuralProfile,
        denied_columns: set[str] | None = None,
    ) -> None:
        self.data_source = data_source
        self.structural = structural
        self.denied_columns = {c.lower() for c in (denied_columns or set())}
        self.physical_ref = {t.name: t.physical_ref for t in data_source.tables}
        self.columns_by_table: dict[str, dict[str, ColumnProfile]] = {}
        for col in structural.columns:
            self.columns_by_table.setdefault(col.table, {})[col.name] = col

    def table_ref(self, table: str) -> str:
        ref = self.physical_ref.get(table)
        if ref is None:
            raise AllowlistViolation(
                f"{table!r} is not a real table in this dataset. Valid tables: "
                f"{sorted(self.physical_ref)}"
            )
        return ref

    def column(self, table: str, column: str) -> ColumnProfile:
        self.table_ref(table)
        col = self.columns_by_table.get(table, {}).get(column)
        if col is None:
            valid = sorted(self.columns_by_table.get(table, {}))
            raise AllowlistViolation(f"{table}.{column} is not a real column. Valid columns: {valid}")
        if column.lower() in self.denied_columns:
            raise AllowlistViolation(
                f"{table}.{column} is denied by this plugin's guardrails and cannot be inspected."
            )
        return col

    def connect(self) -> duckdb.DuckDBPyConnection:
        return open_session(self.data_source)


# --- 1. inspect_column -------------------------------------------------------


class ColumnDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    dtype: str
    null_pct: float
    cardinality: int
    distinct_ratio: float
    min_value: str | None = None
    max_value: str | None = None
    guessed_role: str
    sample_values: list[str] = Field(default_factory=list)


def _inspect_column(toolkit: _Toolkit, table: str, column: str) -> ColumnDetail:
    col = toolkit.column(table, column)
    samples: list[str] = list(col.sample_values)
    if not col.is_likely_pii:
        con = toolkit.connect()
        try:
            rows = con.execute(
                f'SELECT DISTINCT "{col.name}" FROM {toolkit.physical_ref[table]} '
                f'WHERE "{col.name}" IS NOT NULL LIMIT {MAX_SAMPLE_ROWS}'
            ).fetchall()
            samples = [str(r[0]) for r in rows]
        finally:
            con.close()
    else:
        samples = []
    return ColumnDetail(
        table=table, column=column, dtype=col.dtype, null_pct=col.null_percent,
        cardinality=col.cardinality, distinct_ratio=col.distinct_ratio,
        min_value=None if col.min_value is None else str(col.min_value),
        max_value=None if col.max_value is None else str(col.max_value),
        guessed_role=col.guessed_role.value, sample_values=samples,
    )


# --- 2. compare_columns ------------------------------------------------------


class ColumnStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    dtype: str
    guessed_role: str
    min_value: str | None = None
    max_value: str | None = None
    cardinality: int
    null_pct: float


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    columns: list[ColumnStats]


def _compare_columns(toolkit: _Toolkit, table: str, columns: list[str]) -> Comparison:
    stats = []
    for name in columns:
        col = toolkit.column(table, name)
        stats.append(
            ColumnStats(
                column=name, dtype=col.dtype, guessed_role=col.guessed_role.value,
                min_value=None if col.min_value is None else str(col.min_value),
                max_value=None if col.max_value is None else str(col.max_value),
                cardinality=col.cardinality, null_pct=col.null_percent,
            )
        )
    return Comparison(table=table, columns=stats)


# --- 3. check_relationship ---------------------------------------------------


class RelationshipFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    overlap_ratio: float
    cardinality: str
    orphan_ratio: float
    verified: bool = True


def _check_relationship(
    toolkit: _Toolkit, from_table: str, from_column: str, to_table: str, to_column: str
) -> RelationshipFact:
    toolkit.column(from_table, from_column)
    toolkit.column(to_table, to_column)
    from forge_core.models.schema_profile import RelationshipCandidate

    con = toolkit.connect()
    try:
        candidate = RelationshipCandidate(
            from_table=from_table, from_column=from_column, to_table=to_table, to_column=to_column,
            confidence=0.0, evidence="agent hypothesis, being verified",
        )
        cardinality, orphan_ratio = detect_cardinality(con, candidate, toolkit.physical_ref)
        total_row = con.execute(
            f'SELECT COUNT(DISTINCT "{from_column}") FILTER (WHERE "{from_column}" IS NOT NULL) '
            f'FROM {toolkit.physical_ref[from_table]}'
        ).fetchone()
        overlap_row = con.execute(
            f'SELECT COUNT(DISTINCT "{from_column}") FILTER ('
            f'WHERE "{from_column}" IS NOT NULL AND "{from_column}" IN '
            f'(SELECT "{to_column}" FROM {toolkit.physical_ref[to_table]})) '
            f'FROM {toolkit.physical_ref[from_table]}'
        ).fetchone()
        total = (total_row or (0,))[0] or 0
        matching = (overlap_row or (0,))[0] or 0
        overlap_ratio = round(matching / total, 4) if total else 0.0
    finally:
        con.close()
    return RelationshipFact(
        from_table=from_table, from_column=from_column, to_table=to_table, to_column=to_column,
        overlap_ratio=overlap_ratio, cardinality=cardinality, orphan_ratio=orphan_ratio,
        verified=overlap_ratio >= 0.5,
    )


# --- 4. test_value_set --------------------------------------------------------


class Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    matched: list[str]
    unmatched_candidates: list[str]
    coverage_ratio: float
    real_distinct_values: list[str]


def _test_value_set(
    toolkit: _Toolkit, table: str, column: str, candidate_values: list[str]
) -> Coverage:
    col = toolkit.column(table, column)
    con = toolkit.connect()
    try:
        rows = con.execute(
            f'SELECT DISTINCT "{col.name}" FROM {toolkit.physical_ref[table]} '
            f'WHERE "{col.name}" IS NOT NULL LIMIT {MAX_DISTINCT_VALUES}'
        ).fetchall()
    finally:
        con.close()
    real_values = [str(r[0]) for r in rows]
    real_set = set(real_values)
    matched = [v for v in candidate_values if v in real_set]
    unmatched = [v for v in candidate_values if v not in real_set]
    coverage_ratio = round(len(matched) / len(candidate_values), 4) if candidate_values else 0.0
    return Coverage(
        table=table, column=column, matched=matched, unmatched_candidates=unmatched,
        coverage_ratio=coverage_ratio, real_distinct_values=real_values,
    )


# --- 5. aggregate --------------------------------------------------------------


class AggregateRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: str | None = None
    value: float | None = None


class AggregateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    op: AggOp
    group_by: str | None = None
    rows: list[AggregateRow] = Field(default_factory=list)


_WHERE_OP_SQL = {"eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _aggregate(
    toolkit: _Toolkit,
    table: str,
    column: str,
    op: AggOp,
    group_by: str | None = None,
    where: dict[str, Any] | None = None,
) -> AggregateResult:
    toolkit.column(table, column)
    if group_by is not None:
        toolkit.column(table, group_by)

    where = where or {}
    conditions: list[str] = []
    params: list[Any] = []
    for filter_column, value in where.items():
        toolkit.column(table, filter_column)  # unknown/denied column -> raises before any SQL builds
        conditions.append(f'"{filter_column}" = ?')
        params.append(value)  # always a bound parameter, never interpolated

    quoted_col = f'"{column}"'
    agg_sql = render_aggregation(op, quoted_col)
    where_sql = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    con = toolkit.connect()
    try:
        if group_by is not None:
            quoted_group = f'"{group_by}"'
            sql = (
                f"SELECT {quoted_group} AS grp, {agg_sql} AS val FROM {toolkit.physical_ref[table]}"
                f"{where_sql} GROUP BY {quoted_group} ORDER BY val DESC LIMIT {MAX_GROUP_ROWS}"
            )
            result = con.execute(sql, params).fetchall()
            rows = [AggregateRow(group=str(g), value=float(v) if v is not None else None) for g, v in result]
        else:
            sql = f"SELECT {agg_sql} AS val FROM {toolkit.physical_ref[table]}{where_sql}"
            result = con.execute(sql, params).fetchone()
            value = result[0] if result else None
            rows = [AggregateRow(group=None, value=float(value) if value is not None else None)]
    finally:
        con.close()
    return AggregateResult(table=table, column=column, op=op, group_by=group_by, rows=rows)


# --- 6. sample_rows ------------------------------------------------------------


class Rows(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    columns: list[str]
    rows: list[dict[str, Any]]


def _sample_rows(
    toolkit: _Toolkit, table: str, columns: list[str], limit: int, where_contains: str | None = None
) -> Rows:
    """`where_contains`, when given, scopes the sample to rows where ANY of
    `columns` contains that literal substring (case-insensitive) - the
    within-a-table equivalent of a Grep, for a customer term or code the
    agent needs to locate but doesn't yet know which column holds. Still
    just a parameter on the existing tool, not a new one (PHASE_2.md
    Appendix A #5: capability scales through parameters, never tool count).
    The literal is always a bound parameter, never interpolated into SQL."""
    for name in columns:
        col = toolkit.column(table, name)
        if col.is_likely_pii:
            raise AllowlistViolation(f"{table}.{name} is PII and cannot be sampled.")
    capped_limit = max(1, min(limit, MAX_SAMPLE_ROWS))
    quoted_cols = ", ".join(f'"{c}"' for c in columns)
    sql = f'SELECT {quoted_cols} FROM {toolkit.physical_ref[table]}'
    params: list[Any] = []
    if where_contains:
        needle = f"%{where_contains.lower()}%"
        conditions = " OR ".join(f'LOWER(CAST("{c}" AS VARCHAR)) LIKE ?' for c in columns)
        sql += f" WHERE {conditions}"
        params = [needle] * len(columns)
    sql += f" LIMIT {capped_limit}"
    con = toolkit.connect()
    try:
        result = con.execute(sql, params)
        rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
    finally:
        con.close()
    return Rows(table=table, columns=columns, rows=rows)


# --- LangChain tool wrappers ---------------------------------------------------


def build_investigation_tools(
    data_source: DataSource,
    structural: StructuralProfile,
    denied_columns: set[str] | None = None,
    *,
    evidence_sink: list[str] | None = None,
) -> list[StructuredTool]:
    """The six tools, bound to one validated toolkit instance. Every wrapper
    catches AllowlistViolation and any other exception into an error string
    - a tool never raises into the agent loop, it always reports.

    `evidence_sink`, when given, receives every tool's real result string as
    it happens - P2-06's V1 gate checks a claim's cited evidence against
    exactly this log, so a claim can never cite a "fact" no tool actually
    returned this run."""
    toolkit = _Toolkit(data_source, structural, denied_columns)

    def _safe(fn, *args, **kwargs) -> str:
        try:
            result = fn(toolkit, *args, **kwargs)
        except AllowlistViolation as exc:
            return f"ERROR: {exc}"
        except Exception as exc:  # noqa: BLE001 - reported to the agent, never raised
            return f"ERROR running query: {exc}"
        text = result.model_dump_json(indent=2)
        if evidence_sink is not None:
            evidence_sink.append(text)
        return text

    def inspect_column(table: str, column: str) -> str:
        """Full profile of one column: dtype, null%, cardinality, min/max,
        guessed role, and up to 15 real sample values (empty for a PII
        column). Superset of what the data map already told you - use this
        to drill into a column the map flagged ambiguous."""
        return _safe(_inspect_column, table, column)

    def compare_columns(table: str, columns: list[str]) -> str:
        """Side-by-side stats for multiple columns on the same table in one
        call, so you see the discriminating evidence at once - e.g. a
        0-100-bounded score column next to a right-skewed currency column."""
        return _safe(_compare_columns, table, columns)

    def check_relationship(from_table: str, from_column: str, to_table: str, to_column: str) -> str:
        """Runs a real overlap + cardinality query between two columns on
        two tables. Turns a HYPOTHESIS ("these might be the same key") into
        a VERIFIED FACT (overlap_ratio, cardinality, orphan_ratio) - never
        assert a relationship without calling this first."""
        return _safe(_check_relationship, from_table, from_column, to_table, to_column)

    def test_value_set(table: str, column: str, candidate_values: list[str]) -> str:
        """Checks which of your candidate values genuinely appear in a
        column's real distinct values, and what fraction of the real values
        your candidates cover. Use this before claiming a value set (e.g.
        "completed_values") - never assert one from a hint list alone."""
        return _safe(_test_value_set, table, column, candidate_values)

    def aggregate(
        table: str,
        column: str,
        op: Literal["sum", "mean", "min", "max", "count", "nunique", "std", "var", "median"],
        group_by: str | None = None,
        where: dict[str, Any] | None = None,
    ) -> str:
        """Computes a real aggregation over a column, optionally grouped and
        filtered. `op` is a closed set - there is no way to pass a raw
        expression here, by design. `where` is an exact-match filter dict
        (column -> value); values are always bound parameters, never SQL
        text."""
        return _safe(_aggregate, table, column, AggOp(op), group_by, where)

    def sample_rows(table: str, columns: list[str], limit: int = 10, where_contains: str | None = None) -> str:
        """Returns up to 15 real rows for the given columns on one table.
        Refuses any PII-flagged column outright. Pass `where_contains` to
        search for a literal substring (case-insensitive) across those
        columns instead of just taking the first rows - e.g. the customer
        mentioned a code or term and you need to find which column/rows
        actually contain it."""
        return _safe(_sample_rows, table, columns, limit, where_contains)

    return [
        StructuredTool.from_function(inspect_column),
        StructuredTool.from_function(compare_columns),
        StructuredTool.from_function(check_relationship),
        StructuredTool.from_function(test_value_set),
        StructuredTool.from_function(aggregate),
        StructuredTool.from_function(sample_rows),
    ]


__all__ = [
    "AggregateResult",
    "ColumnDetail",
    "Comparison",
    "Coverage",
    "RelationshipFact",
    "Rows",
    "build_investigation_tools",
]
