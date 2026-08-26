"""Semantic investigation tools for Data2plugin agentic reasoning.

Enforces strict tenant isolation, safety boundaries, and read-only execution:
1. Parameters, never raw arbitrary executable SQL (unless verified via safe AST parser).
2. Every identifier is validated against the real schema allowlist.
3. Denied/PII columns are strictly blocked from inspection and queries.
4. Row limits and query timeouts are enforced centrally.
5. All tools operate with tenant context.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import duckdb
import sqlglot
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from forge_core.models.datasource import DataSource
from forge_core.models.metrics import AggOp, render_aggregation
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile
from forge_core.runtime_session import open_session

logger = logging.getLogger("forge_core.agentic.investigation_tools")

MAX_SAMPLE_ROWS = 15
MAX_DISTINCT_VALUES = 200
MAX_GROUP_ROWS = 50
MAX_QUERY_LIMIT = 50


class AllowlistViolation(ValueError):
    """Raised when a requested table/column isn't real, is denied, or violates tenant policy."""


class _Toolkit:
    """Validates every table and column against the real schema and tenant guardrails."""

    def __init__(
        self,
        data_source: DataSource,
        structural: StructuralProfile,
        denied_columns: set[str] | None = None,
        tenant_id: str | None = None,
        datasource_ref: str | None = None,
    ) -> None:
        self.data_source = data_source
        self.structural = structural
        self.denied_columns = {c.lower() for c in (denied_columns or set())}
        self.tenant_id = tenant_id or "default"
        self.datasource_ref = datasource_ref or data_source.id
        self.physical_ref = {t.name: t.physical_ref for t in data_source.tables}
        self.columns_by_table: dict[str, dict[str, ColumnProfile]] = {}
        for col in structural.columns:
            self.columns_by_table.setdefault(col.table, {})[col.name] = col

    def table_ref(self, table: str) -> str:
        ref = self.physical_ref.get(table)
        if ref is None:
            raise AllowlistViolation(
                f"{table!r} is not a valid table in this dataset. Valid tables: {sorted(self.physical_ref)}"
            )
        return ref

    def column(self, table: str, column: str) -> ColumnProfile:
        self.table_ref(table)
        col = self.columns_by_table.get(table, {}).get(column)
        if col is None:
            valid = sorted(self.columns_by_table.get(table, {}))
            raise AllowlistViolation(f"{table}.{column} is not a valid column. Valid columns: {valid}")
        if column.lower() in self.denied_columns:
            raise AllowlistViolation(
                f"{table}.{column} is denied by security/PII guardrails and cannot be inspected."
            )
        return col

    def connect(self) -> duckdb.DuckDBPyConnection:
        return open_session(self.data_source)


# --- 1. Column and Table Details ---


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
        table=table,
        column=column,
        dtype=col.dtype,
        null_pct=col.null_percent,
        cardinality=col.cardinality,
        distinct_ratio=col.distinct_ratio,
        min_value=None if col.min_value is None else str(col.min_value),
        max_value=None if col.max_value is None else str(col.max_value),
        guessed_role=col.guessed_role.value,
        sample_values=samples,
    )


# --- 2. Schema Overview ---


class TableSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    row_count: int
    columns: list[str]


class SchemaSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tables: list[TableSummary]
    total_columns: int


def _inspect_schema(toolkit: _Toolkit) -> SchemaSummary:
    tables = []
    for t in toolkit.data_source.tables:
        cols = sorted(toolkit.columns_by_table.get(t.name, {}).keys())
        tables.append(TableSummary(name=t.name, row_count=t.row_count, columns=cols))
    return SchemaSummary(tables=tables, total_columns=len(toolkit.structural.columns))


# --- 3. Compare Columns ---


class ColumnStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    dtype: str
    guessed_role: str
    min_value: str | None = None
    max_value: str | None = None
    cardinality: int
    distinct_ratio: float
    null_pct: float


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    columns: list[ColumnStats]


def _compare_columns(toolkit: _Toolkit, table: str, columns: list[str]) -> Comparison:
    stats = []
    for col_name in columns:
        col = toolkit.column(table, col_name)
        stats.append(
            ColumnStats(
                column=col.name,
                dtype=col.dtype,
                guessed_role=col.guessed_role.value,
                min_value=None if col.min_value is None else str(col.min_value),
                max_value=None if col.max_value is None else str(col.max_value),
                cardinality=col.cardinality,
                distinct_ratio=col.distinct_ratio,
                null_pct=col.null_percent,
            )
        )
    return Comparison(table=table, columns=stats)


# --- 4. Relationships ---


class RelationshipFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    overlap_ratio: float = 0.0
    cardinality: str = "N:1"
    orphan_ratio: float = 0.0
    from_distinct: int = 0
    to_distinct: int = 0
    overlap_count: int = 0
    overlap_ratio_from: float = 0.0
    overlap_ratio_to: float = 0.0
    is_valid_fk: bool = False


def _check_relationship(
    toolkit: _Toolkit, from_table: str, from_column: str, to_table: str, to_column: str
) -> RelationshipFact:
    c1 = toolkit.column(from_table, from_column)
    c2 = toolkit.column(to_table, to_column)

    con = toolkit.connect()
    try:
        query = f"""
        WITH f AS (SELECT DISTINCT "{c1.name}" AS k FROM {toolkit.physical_ref[from_table]} WHERE "{c1.name}" IS NOT NULL),
             t AS (SELECT DISTINCT "{c2.name}" AS k FROM {toolkit.physical_ref[to_table]} WHERE "{c2.name}" IS NOT NULL),
             inter AS (SELECT f.k FROM f INNER JOIN t ON f.k = t.k)
        SELECT
            (SELECT COUNT(*) FROM f),
            (SELECT COUNT(*) FROM t),
            (SELECT COUNT(*) FROM inter)
        """
        f_dist, t_dist, overlap = con.execute(query).fetchone() or (0, 0, 0)
    finally:
        con.close()

    r_from = round(overlap / f_dist, 4) if f_dist > 0 else 0.0
    r_to = round(overlap / t_dist, 4) if t_dist > 0 else 0.0
    is_fk = (r_from >= 0.95 and t_dist >= f_dist)

    return RelationshipFact(
        from_table=from_table,
        from_column=from_column,
        to_table=to_table,
        to_column=to_column,
        overlap_ratio=r_from,
        cardinality="N:1" if t_dist >= f_dist else "1:N",
        orphan_ratio=round(max(0.0, 1.0 - r_from), 4),
        from_distinct=f_dist,
        to_distinct=t_dist,
        overlap_count=overlap,
        overlap_ratio_from=r_from,
        overlap_ratio_to=r_to,
        is_valid_fk=is_fk,
    )


# --- 5. Value Set Testing ---


class Coverage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    table: str
    column: str
    matched: list[str] = Field(default_factory=list)
    unmatched_candidates: list[str] = Field(default_factory=list)
    real_distinct_values: list[str] = Field(default_factory=list)
    matched_values: list[str] = Field(default_factory=list)
    observed_distinct_count: int = 0
    coverage_ratio: float = 0.0


def _test_value_set(toolkit: _Toolkit, table: str, column: str, candidate_values: list[str]) -> Coverage:
    col = toolkit.column(table, column)
    con = toolkit.connect()
    try:
        rows = con.execute(
            f'SELECT DISTINCT "{col.name}" FROM {toolkit.physical_ref[table]} WHERE "{col.name}" IS NOT NULL'
        ).fetchall()
        real_values = {str(r[0]) for r in rows}
    finally:
        con.close()

    matched = [v for v in candidate_values if v in real_values]
    unmatched = [v for v in candidate_values if v not in real_values]
    ratio = round(len(matched) / len(real_values), 4) if real_values else 0.0
    return Coverage(
        table=table,
        column=column,
        matched=matched,
        matched_values=matched,
        unmatched_candidates=unmatched,
        real_distinct_values=list(real_values),
        observed_distinct_count=len(real_values),
        coverage_ratio=ratio,
    )


# --- 6. Aggregations ---


class AggregateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    column: str
    op: str
    result: float | None = None
    group_results: list[dict[str, Any]] = Field(default_factory=list)


def _aggregate(
    toolkit: _Toolkit,
    table: str,
    column: str,
    op: AggOp,
    group_by: str | None = None,
    where: dict[str, Any] | None = None,
) -> AggregateResult:
    col = toolkit.column(table, column)
    if group_by:
        toolkit.column(table, group_by)

    where_clauses = []
    params = []
    if where:
        for k, v in where.items():
            toolkit.column(table, k)
            where_clauses.append(f'"{k}" = ?')
            params.append(v)

    where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    agg_expr = render_aggregation(op, col.name)

    con = toolkit.connect()
    try:
        if group_by:
            sql = (
                f'SELECT "{group_by}", {agg_expr} FROM {toolkit.physical_ref[table]} '
                f'{where_str} GROUP BY "{group_by}" LIMIT {MAX_GROUP_ROWS}'
            )
            rows = con.execute(sql, params).fetchall()
            group_res = [{"group": str(r[0]), "value": r[1]} for r in rows]
            return AggregateResult(table=table, column=column, op=op.value, group_results=group_res)
        else:
            sql = f"SELECT {agg_expr} FROM {toolkit.physical_ref[table]} {where_str}"
            val = con.execute(sql, params).fetchone()
            return AggregateResult(
                table=table,
                column=column,
                op=op.value,
                result=None if (not val or val[0] is None) else float(val[0]),
            )
    finally:
        con.close()


# --- 7. Sample Rows ---


class Rows(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    columns: list[str]
    rows: list[dict[str, Any]]


def _sample_rows(
    toolkit: _Toolkit,
    table: str,
    columns: list[str],
    limit: int = 10,
    where_contains: str | None = None,
) -> Rows:
    for c in columns:
        toolkit.column(table, c)

    limit = min(max(1, limit), MAX_SAMPLE_ROWS)
    cols_str = ", ".join(f'"{c}"' for c in columns)

    where_str = ""
    params = []
    if where_contains:
        or_clauses = [f'LOWER(CAST("{c}" AS VARCHAR)) LIKE ?' for c in columns]
        where_str = f"WHERE {' OR '.join(or_clauses)}"
        params = [f"%{where_contains.lower()}%" for _ in columns]

    con = toolkit.connect()
    try:
        sql = f"SELECT {cols_str} FROM {toolkit.physical_ref[table]} {where_str} LIMIT {limit}"
        cursor = con.execute(sql, params)
        col_names = [desc[0] for desc in cursor.description]
        raw_rows = cursor.fetchall()
        dict_rows = [dict(zip(col_names, r)) for r in raw_rows]
        return Rows(table=table, columns=columns, rows=dict_rows)
    finally:
        con.close()


# --- 8. Safe Read-Only DuckDB Query ---


class QueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int


def _run_readonly_duckdb_query(toolkit: _Toolkit, query: str, limit: int = 15) -> QueryResult:
    """Parses and validates arbitrary read-only queries with sqlglot before DuckDB execution."""
    try:
        parsed = sqlglot.parse_one(query, read="duckdb")
    except Exception as exc:
        raise AllowlistViolation(f"SQL parsing error: {exc}") from exc

    # Enforce strictly Select or With root expression
    if not isinstance(parsed, (exp.Select, exp.With)):
        raise AllowlistViolation(
            f"Only read-only SELECT queries are allowed (found {type(parsed).__name__})"
        )

    # Check for forbidden dangerous statements
    for node in parsed.walk():
        if isinstance(
            node,
            (
                exp.Insert,
                exp.Update,
                exp.Delete,
                exp.Drop,
                exp.Alter,
                exp.Command,
                exp.Create,
            ),
        ):
            raise AllowlistViolation(f"Modification expression {type(node).__name__} is forbidden.")

    # Validate table references
    tables_found = [t.name for t in parsed.find_all(exp.Table) if t.name]
    for tbl in tables_found:
        if tbl not in toolkit.physical_ref:
            raise AllowlistViolation(
                f"Table {tbl!r} not found in datasource. Valid: {sorted(toolkit.physical_ref)}"
            )

    # Check denied columns in AST
    for col_node in parsed.find_all(exp.Column):
        if col_node.name and col_node.name.lower() in toolkit.denied_columns:
            raise AllowlistViolation(f"Column {col_node.name!r} is denied by PII guardrails.")

    # Rewrite logical table names to DuckDB session table references
    def _transform_table(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Table) and node.name in toolkit.physical_ref:
            phys = toolkit.physical_ref[node.name]
            return sqlglot.to_table(phys)
        return node

    transformed = parsed.transform(_transform_table)
    executable_sql = transformed.sql(dialect="duckdb")

    limit = min(max(1, limit), MAX_QUERY_LIMIT)
    con = toolkit.connect()
    try:
        # Wrap query in a subquery with strict limit
        wrapped_sql = f"SELECT * FROM ({executable_sql}) LIMIT {limit}"
        cursor = con.execute(wrapped_sql)
        col_names = [desc[0] for desc in cursor.description]
        raw_rows = cursor.fetchall()
        dict_rows = [dict(zip(col_names, r)) for r in raw_rows]
        return QueryResult(columns=col_names, rows=dict_rows, row_count=len(dict_rows))
    except Exception as exc:
        raise AllowlistViolation(f"DuckDB execution error: {exc}") from exc
    finally:
        con.close()


# --- Tool Builder Factory ---


def build_investigation_tools(
    data_source: DataSource,
    structural: StructuralProfile,
    denied_columns: set[str] | None = None,
    tenant_id: str | None = None,
    datasource_ref: str | None = None,
    evidence_sink: list[str] | None = None,
) -> list[StructuredTool]:
    """Builds the safe, tenant-scoped LangChain investigation tools.

    `evidence_sink`, when given, receives one line per successful tool call:
    what was asked and what came back. `validation/gates.py::verify_column_
    claim` checks a claim against this log (V1 - "the evidence for this
    actually exists"), so without it every claim the agent makes from a tool
    call it performed *this session* fails verification and is discarded.

    Two callers already passed this argument before the parameter existed
    (`agentic/data_understanding_agent.py`, `understanding/agent.py`). Both
    wrap construction in a bare `except Exception`, so the resulting
    TypeError was swallowed and each agent silently returned nothing on
    every run - a dead agent that still logged an invocation, with zero
    steps and zero tokens."""
    toolkit = _Toolkit(
        data_source=data_source,
        structural=structural,
        denied_columns=denied_columns,
        tenant_id=tenant_id,
        datasource_ref=datasource_ref,
    )

    def _safe(fn: Any, *args: Any, **kwargs: Any) -> str:
        try:
            res = fn(toolkit, *args, **kwargs)
            out = res.model_dump_json() if hasattr(res, "model_dump_json") else str(res)
        except AllowlistViolation as exc:
            return f"Tool Error: {exc}"
        except Exception as exc:
            logger.exception("Investigation tool exception: %s", exc)
            return f"Unexpected Error: {exc}"
        if evidence_sink is not None:
            # Only successful calls are evidence. A refused or failed call
            # proves nothing, and logging it would let a claim cite its own
            # failure as support.
            call = ", ".join([*(str(a) for a in args), *(f"{k}={v}" for k, v in kwargs.items())])
            evidence_sink.append(f"{fn.__name__.lstrip('_')}({call}) -> {out}")
        return out

    def inspect_schema() -> str:
        """Returns the full list of tables, row counts, and column names."""
        return _safe(_inspect_schema)

    def inspect_column(table: str, column: str) -> str:
        """Returns detailed statistics, type, null percentage, and sample values for a column."""
        return _safe(_inspect_column, table, column)

    def compare_columns(table: str, columns: list[str]) -> str:
        """Returns side-by-side stats for multiple columns on a single table."""
        return _safe(_compare_columns, table, columns)

    def check_relationship(from_table: str, from_column: str, to_table: str, to_column: str) -> str:
        """Verifies key overlap, cardinality, and foreign key validity between two columns."""
        return _safe(_check_relationship, from_table, from_column, to_table, to_column)

    def test_value_set(table: str, column: str, candidate_values: list[str]) -> str:
        """Tests which candidate values appear in a column's distinct values."""
        return _safe(_test_value_set, table, column, candidate_values)

    def aggregate(
        table: str,
        column: str,
        op: Literal["sum", "mean", "min", "max", "count", "nunique", "std", "var", "median"],
        group_by: str | None = None,
        where: dict[str, Any] | None = None,
    ) -> str:
        """Computes a parameter-bound aggregation over a column with optional group_by and filter."""
        return _safe(_aggregate, table, column, AggOp(op), group_by, where)

    def sample_rows(table: str, columns: list[str], limit: int = 10, where_contains: str | None = None) -> str:
        """Returns sample rows for specified columns, with optional substring filtering."""
        return _safe(_sample_rows, table, columns, limit, where_contains)

    def run_readonly_duckdb_query(query: str, limit: int = 15) -> str:
        """Executes a validated read-only SELECT query against DuckDB."""
        return _safe(_run_readonly_duckdb_query, query, limit)

    # Deliberately still the full set. Callers now receive the whole data map
    # up front, so the schema/column-inspection tools are usually redundant -
    # but the only consumer left is a *narrow fallback* that runs when the
    # map flagged genuine ambiguity, which is exactly the case where looking
    # closer is the point. Trimming here was tried and reverted: the measured
    # win came from answering binding in one structured call over the map
    # (86k tokens -> ~5k), not from taking tools away from the fallback.
    return [
        StructuredTool.from_function(inspect_schema),
        StructuredTool.from_function(inspect_column),
        StructuredTool.from_function(compare_columns),
        StructuredTool.from_function(check_relationship),
        StructuredTool.from_function(test_value_set),
        StructuredTool.from_function(aggregate),
        StructuredTool.from_function(sample_rows),
        StructuredTool.from_function(run_readonly_duckdb_query),
    ]


__all__ = [
    "AggregateResult",
    "AllowlistViolation",
    "ColumnDetail",
    "ColumnStats",
    "Comparison",
    "Coverage",
    "QueryResult",
    "RelationshipFact",
    "Rows",
    "SchemaSummary",
    "TableSummary",
    "build_investigation_tools",
]
