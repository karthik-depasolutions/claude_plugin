"""Semantic investigation tools for the Business Context Discovery Agent.

Enforces strict tenant isolation, safety boundaries, and read-only execution:
1. Every table and column is validated against the real schema allowlist.
2. Denied/PII columns are strictly blocked from inspection and queries.
3. Row limits and query timeouts are enforced centrally.
4. AST validation guarantees read-only SELECT queries only.
"""

from __future__ import annotations

import logging
from typing import Any

import duckdb
import sqlglot
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile
from forge_core.runtime_session import open_session

logger = logging.getLogger("forge_core.agentic.context_tools")

MAX_SAMPLE_ROWS = 15
MAX_DISTINCT_VALUES = 100
MAX_QUERY_LIMIT = 50


class AllowlistViolation(ValueError):
    """Raised when a requested table/column is invalid, denied, or violates tenant policy."""


class ContextToolkit:
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


# --- Output Schemas for Tools ---


class SchemaOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tables: list[dict[str, Any]]
    total_tables: int


class ColumnOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str
    column: str
    dtype: str
    null_pct: float
    cardinality: int
    distinct_ratio: float
    min_value: str | None = None
    max_value: str | None = None
    sample_values: list[str] = Field(default_factory=list)


class DuplicateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str
    column: str
    total_rows: int
    unique_values: int
    has_duplicates: bool
    top_repeated_values: list[dict[str, Any]] = Field(default_factory=list)


class InconsistentCategories(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str
    column: str
    inconsistent_clusters: list[list[str]] = Field(default_factory=list)
    has_casing_variations: bool = False


# --- Tool Implementation Functions ---


def _inspect_schema(toolkit: ContextToolkit) -> SchemaOverview:
    tables_info = []
    for t in toolkit.data_source.tables:
        cols = [c.name for c in toolkit.structural.columns_for(t.name) if c.name.lower() not in toolkit.denied_columns]
        tables_info.append({"table": t.name, "row_count": t.row_count, "columns": cols})
    return SchemaOverview(tables=tables_info, total_tables=len(tables_info))


def _inspect_column(toolkit: ContextToolkit, table: str, column: str) -> ColumnOverview:
    col = toolkit.column(table, column)
    ref = toolkit.table_ref(table)
    con = toolkit.connect()
    samples: list[str] = []
    try:
        quoted = f'"{col.name}"'
        df = con.execute(
            f"SELECT DISTINCT CAST({quoted} AS VARCHAR) AS v FROM {ref} WHERE {quoted} IS NOT NULL LIMIT 10"
        ).fetchdf()
        samples = [str(x) for x in df["v"].tolist() if x is not None]
    except Exception as exc:
        logger.warning("Error fetching samples for %s.%s: %s", table, column, exc)
    finally:
        con.close()

    return ColumnOverview(
        table=table,
        column=column,
        dtype=col.dtype,
        null_pct=round(col.null_percent, 2),
        cardinality=col.cardinality,
        distinct_ratio=round(col.distinct_ratio, 4),
        min_value=str(col.min_value) if col.min_value is not None else None,
        max_value=str(col.max_value) if col.max_value is not None else None,
        sample_values=samples,
    )


def _get_duplicate_profile(toolkit: ContextToolkit, table: str, column: str) -> DuplicateProfile:
    col = toolkit.column(table, column)
    ref = toolkit.table_ref(table)
    con = toolkit.connect()
    top_repeated = []
    try:
        quoted = f'"{col.name}"'
        sql = f"""
            SELECT CAST({quoted} AS VARCHAR) AS val, COUNT(*) AS count
            FROM {ref}
            WHERE {quoted} IS NOT NULL
            GROUP BY 1
            HAVING COUNT(*) > 1
            ORDER BY count DESC
            LIMIT 5
        """
        df = con.execute(sql).fetchdf()
        for _, row in df.iterrows():
            top_repeated.append({"value": str(row["val"]), "count": int(row["count"])})
    finally:
        con.close()

    table_obj = next((t for t in toolkit.data_source.tables if t.name == table), None)
    total_rows = table_obj.row_count if table_obj else 0

    return DuplicateProfile(
        table=table,
        column=column,
        total_rows=total_rows,
        unique_values=col.cardinality,
        has_duplicates=len(top_repeated) > 0 or (col.cardinality < total_rows and total_rows > 0),
        top_repeated_values=top_repeated,
    )


def _detect_inconsistent_categories(toolkit: ContextToolkit, table: str, column: str) -> InconsistentCategories:
    col = toolkit.column(table, column)
    ref = toolkit.table_ref(table)
    con = toolkit.connect()
    clusters = []
    try:
        quoted = f'"{col.name}"'
        sql = f"SELECT DISTINCT CAST({quoted} AS VARCHAR) as val FROM {ref} WHERE {quoted} IS NOT NULL LIMIT {MAX_DISTINCT_VALUES}"
        df = con.execute(sql).fetchdf()
        raw_vals = [str(x) for x in df["val"].tolist()]

        groups: dict[str, list[str]] = {}
        for v in raw_vals:
            groups.setdefault(v.strip().lower(), []).append(v)
        clusters = [variants for variants in groups.values() if len(variants) > 1]
    finally:
        con.close()

    return InconsistentCategories(
        table=table,
        column=column,
        inconsistent_clusters=clusters,
        has_casing_variations=len(clusters) > 0,
    )


def _sample_rows(toolkit: ContextToolkit, table: str, limit: int = 5) -> list[dict[str, Any]]:
    toolkit.table_ref(table)
    cols = [c.name for c in toolkit.structural.columns_for(table) if c.name.lower() not in toolkit.denied_columns]
    con = toolkit.connect()
    try:
        col_list = ", ".join(f'"{c}"' for c in cols)
        ref = toolkit.table_ref(table)
        df = con.execute(f"SELECT {col_list} FROM {ref} LIMIT {min(limit, MAX_SAMPLE_ROWS)}").fetchdf()
        return df.to_dict(orient="records")
    finally:
        con.close()


def _run_safe_duckdb_query(toolkit: ContextToolkit, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Runs a validated read-only SELECT query."""
    try:
        parsed = sqlglot.parse_one(query, read="duckdb")
    except Exception as exc:
        raise AllowlistViolation(f"Invalid SQL syntax: {exc}") from exc

    if not isinstance(parsed, exp.Select):
        raise AllowlistViolation("Only read-only SELECT statements are permitted.")

    # Check denied columns in AST
    for col_node in parsed.find_all(exp.Column):
        c_name = col_node.name.lower()
        if c_name in toolkit.denied_columns:
            raise AllowlistViolation(f"Column '{c_name}' is denied by security/PII policy.")

    # Every referenced table must be one of this tenant's own. Without this,
    # an unrecognised name is simply left un-rewritten and handed to DuckDB,
    # which would happily resolve another attached database or an internal
    # catalog view - a tenant-isolation hole, not just a bad query.
    # CTE aliases are legal references, so allow names the query itself defines.
    cte_names = {cte.alias_or_name for cte in parsed.find_all(exp.CTE)}
    for table_node in parsed.find_all(exp.Table):
        if table_node.name not in toolkit.physical_ref and table_node.name not in cte_names:
            raise AllowlistViolation(
                f"{table_node.name!r} is not a valid table in this dataset. "
                f"Valid tables: {sorted(toolkit.physical_ref)}"
            )

    # Rewrite logical table names to physical table references
    def _transform_table(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Table) and node.name in toolkit.physical_ref:
            phys = toolkit.physical_ref[node.name]
            return sqlglot.to_table(phys)
        return node

    transformed = parsed.transform(_transform_table)
    rewritten_query = transformed.sql(dialect="duckdb")

    con = toolkit.connect()
    try:
        final_query = f"SELECT * FROM ({rewritten_query}) AS subq LIMIT {min(limit, MAX_QUERY_LIMIT)}"
        df = con.execute(final_query).fetchdf()
        return df.to_dict(orient="records")
    finally:
        con.close()


def build_context_discovery_tools(
    data_source: DataSource,
    structural: StructuralProfile,
    denied_columns: set[str] | None = None,
    tenant_id: str | None = None,
    datasource_ref: str | None = None,
) -> list[StructuredTool]:
    """Builds the full suite of safe Context Discovery tools."""
    toolkit = ContextToolkit(
        data_source=data_source,
        structural=structural,
        denied_columns=denied_columns,
        tenant_id=tenant_id,
        datasource_ref=datasource_ref,
    )

    def _safe(fn: Any, *args: Any, **kwargs: Any) -> str:
        try:
            res = fn(toolkit, *args, **kwargs)
            return res.model_dump_json() if hasattr(res, "model_dump_json") else str(res)
        except AllowlistViolation as exc:
            return f"Tool Error: {exc}"
        except Exception as exc:
            logger.warning("Context tool execution error: %s", exc)
            return f"Tool Error: {exc}"

    def inspect_schema() -> str:
        """Returns the full list of tables, row counts, and non-denied column names."""
        return _safe(_inspect_schema)

    def inspect_column(table: str, column: str) -> str:
        """Returns detailed statistics, type, null percentage, and sample values for a column."""
        return _safe(_inspect_column, table, column)

    def get_duplicate_profile(table: str, column: str) -> str:
        """Analyzes duplicate and repeated values in a column to assess record grain and uniqueness."""
        return _safe(_get_duplicate_profile, table, column)

    def detect_inconsistent_categories(table: str, column: str) -> str:
        """Checks if a categorical column contains casing variants (e.g. 'guitar' vs 'Guitar') or spelling variations."""
        return _safe(_detect_inconsistent_categories, table, column)

    def sample_rows(table: str, limit: int = 5) -> str:
        """Returns a list of sample rows from a table (max 15 rows)."""
        return _safe(_sample_rows, table, limit)

    def run_safe_duckdb_query(query: str, limit: int = 10) -> str:
        """Executes a validated read-only SELECT query against the dataset."""
        return _safe(_run_safe_duckdb_query, query, limit)

    # Deliberately small. The data map handed to the agent up front already
    # carries the schema, every column's type/cardinality/null-rate/range/
    # top-8 values, the verified joins and the grain - so a tool whose only
    # job is to read those back is a round trip that buys nothing and costs
    # a full conversation resend. What survives is what the map genuinely
    # cannot answer:
    #
    #   sample_rows                    whole rows read together - how columns
    #                                  relate *within* one record, which no
    #                                  per-column summary can show
    #   get_duplicate_profile          which values repeat and how often
    #                                  (the record-grain question)
    #   detect_inconsistent_categories the full value set, past the top 8,
    #                                  clustered by casing/whitespace
    #   run_safe_duckdb_query          escape hatch for anything else
    #
    # `inspect_schema` and `inspect_column` are gone: both returned strictly
    # less than the map already states.
    return [
        StructuredTool.from_function(sample_rows),
        StructuredTool.from_function(get_duplicate_profile),
        StructuredTool.from_function(detect_inconsistent_categories),
        StructuredTool.from_function(run_safe_duckdb_query),
    ]


__all__ = [
    "AllowlistViolation",
    "ColumnOverview",
    "ContextToolkit",
    "DuplicateProfile",
    "InconsistentCategories",
    "SchemaOverview",
    "build_context_discovery_tools",
]
