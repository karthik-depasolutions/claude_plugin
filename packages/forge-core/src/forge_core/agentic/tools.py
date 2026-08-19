"""Tools handed to the LangChain agents in this package (`binding_agent.py`,
`data_agent.py`).

Every tool here is read-only and bounded (small row limits, columns checked
against the real schema before any query runs) - same trust boundary the
rest of the pipeline already operates in during generation.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile
from forge_core.runtime_session import open_session

MAX_PREVIEW_ROWS = 15
MAX_SEARCH_RESULTS = 4


def _describe_columns(table_cols: list[ColumnProfile]) -> str:
    lines = []
    for c in table_cols:
        sample = ", ".join(c.sample_values[:5]) if c.sample_values else "(no samples)"
        lines.append(
            f"- {c.name} | dtype={c.dtype} | guessed_role={c.guessed_role.value} | "
            f"null%={c.null_percent:.1f} | distinct={c.cardinality} | samples=[{sample}]"
        )
    return "\n".join(lines)


def build_terminology_search_tool() -> StructuredTool:
    """Shared by both agents - not scoped to any table/schema, so there's
    exactly one implementation instead of two copies drifting apart."""

    def search_industry_terminology(query: str) -> str:
        """Search the web for how a business term is normally used (e.g.
        typical column names for 'net revenue' in a diagnostics lab, or what
        'grain' a booking record usually has) when the column names/samples
        alone don't make the right choice obvious. Returns a few short
        snippets, not full pages."""
        try:
            from ddgs import DDGS
        except ImportError:
            return "ERROR: web search is unavailable in this environment."
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=MAX_SEARCH_RESULTS))
        except Exception as exc:  # noqa: BLE001 - network/rate-limit errors are just a failed tool call
            return f"ERROR performing web search: {exc}"
        if not results:
            return "No results found."
        return "\n\n".join(f"{r.get('title', '')}: {r.get('body', '')}" for r in results)

    return StructuredTool.from_function(search_industry_terminology)


def build_binding_tools(
    table_cols: list[ColumnProfile], source: DataSource, fact_table_physical_ref: str
) -> list[StructuredTool]:
    """Tools scoped to one fact table for the duration of a single binding
    decision. Every column name a tool accepts is checked against the real
    column list before touching the database - the agent can request a
    lookup, but it can never get a query to run against a column (or table)
    that doesn't actually exist."""
    valid_names = {c.name for c in table_cols}

    def list_candidate_columns() -> str:
        """List every column available on the fact table being bound right
        now, with its data type, structurally-guessed role, null percentage,
        distinct-value count, and a few real sample values. Always call this
        first - never guess a column name that isn't in this list."""
        return _describe_columns(table_cols)

    def preview_column_values(column: str) -> str:
        """Run a live, read-only query against the customer's real data to
        see up to 15 distinct, non-null values of one column - use this to
        check whether a candidate column's *actual content* (not just its
        name) matches the concept you're trying to bind, e.g. confirming a
        column full of dollar amounts vs. one full of quantities."""
        if column not in valid_names:
            return f"ERROR: {column!r} is not a real column on this table. Call list_candidate_columns first."
        con = open_session(source)
        try:
            rows = con.execute(
                f'SELECT DISTINCT "{column}" FROM {fact_table_physical_ref} '
                f'WHERE "{column}" IS NOT NULL LIMIT {MAX_PREVIEW_ROWS}'
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 - surfaced to the agent as a tool result, not raised
            return f"ERROR running preview query: {exc}"
        finally:
            con.close()
        values = [str(r[0]) for r in rows]
        return f"Sample values of {column!r}: {values}" if values else f"{column!r} has no non-null values."

    return [
        StructuredTool.from_function(list_candidate_columns),
        StructuredTool.from_function(preview_column_values),
        build_terminology_search_tool(),
    ]


def build_data_understanding_tools(structural: StructuralProfile, source: DataSource) -> list[StructuredTool]:
    """Tools for the data-understanding agent (`data_agent.py`) - spans every
    table in the dataset, unlike `build_binding_tools`, which is scoped to
    one fact table for one role. PII-flagged columns can never be
    previewed, the same trust boundary the single-shot semantic profiler's
    redacted samples already enforce - the agent can ask about what a PII
    column probably means, but never see its real values."""
    valid = {(c.table, c.name): c for c in structural.columns}
    tables_by_name = {t.name: t for t in source.tables}

    def preview_column_values(table: str, column: str) -> str:
        """Run a live, read-only query to see up to 15 distinct, non-null
        real values of one column on one table - use this when a column's
        name and structural facts alone don't make its meaning clear.
        Refuses PII-flagged columns; reason about those from context only."""
        col = valid.get((table, column))
        if col is None:
            return f"ERROR: {table}.{column} is not a real table/column in this dataset."
        if col.is_likely_pii:
            return f"ERROR: {table}.{column} is flagged as likely PII - cannot preview real values."
        table_desc = tables_by_name.get(table)
        if table_desc is None:
            return f"ERROR: table {table!r} not found."
        con = open_session(source)
        try:
            rows = con.execute(
                f'SELECT DISTINCT "{column}" FROM {table_desc.physical_ref} '
                f'WHERE "{column}" IS NOT NULL LIMIT {MAX_PREVIEW_ROWS}'
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 - surfaced to the agent as a tool result, not raised
            return f"ERROR running preview query: {exc}"
        finally:
            con.close()
        values = [str(r[0]) for r in rows]
        label = f"{table}.{column}"
        return f"Sample values of {label}: {values}" if values else f"{label} has no non-null values."

    return [
        StructuredTool.from_function(preview_column_values),
        build_terminology_search_tool(),
    ]


__all__ = ["build_binding_tools", "build_data_understanding_tools", "build_terminology_search_tool"]
