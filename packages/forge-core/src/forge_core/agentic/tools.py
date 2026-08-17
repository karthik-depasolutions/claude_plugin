"""Tools handed to the LangChain schema-binding agent (`binding_agent.py`).

Every tool here is scoped to one already-ingested `DataSource`/fact table via
closures - the agent can look, but everything it can do is read-only and
bounded (small row limits, single already-allow-listed table), same trust
boundary the rest of the pipeline already operates in during generation.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import ColumnProfile
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

    return [
        StructuredTool.from_function(list_candidate_columns),
        StructuredTool.from_function(preview_column_values),
        StructuredTool.from_function(search_industry_terminology),
    ]


__all__ = ["build_binding_tools"]
