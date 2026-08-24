"""Builds the P2-03 `DataMap` from what profiling already computed - one
deterministic pass, zero LLM calls, using the exact same connection/columns
`build_structural_only` already produced. See `models/data_map.py` for the
contract and why this exists.
"""

from __future__ import annotations

import re

import duckdb

from forge_core.models.common import ColumnRole
from forge_core.models.data_map import ColumnMapEntry, DataMap, EntityMapEntry
from forge_core.models.datasource import DataSource
from forge_core.models.entity_graph import EntityGraph
from forge_core.models.schema_profile import ColumnProfile, StructuralProfile

MAX_TOP_VALUES = 8
TOP_VALUES_ROW_LIMIT = 2_000_000  # skip the GROUP BY scan on huge tables - not worth the cost

_NUMERIC_DUCKDB_TYPES = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
    "UINTEGER", "UBIGINT", "FLOAT", "DOUBLE", "DECIMAL", "REAL",
}
_CURRENCY_NAME_HINTS = re.compile(r"amount|price|cost|revenue|fee|salary|total|balance|inr|usd|rupee|rs_")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def _base_type(dtype: str) -> str:
    return dtype.split("(", maxsplit=1)[0].strip().upper()


def _format_fingerprint(col: ColumnProfile) -> str | None:
    if col.guessed_role in (ColumnRole.DATE, ColumnRole.DATETIME):
        return "iso_date"
    if col.guessed_role == ColumnRole.EMAIL:
        return "email"
    if _base_type(col.dtype) in _NUMERIC_DUCKDB_TYPES and _CURRENCY_NAME_HINTS.search(col.name.lower()):
        return "currency"
    if col.sample_values and all(_UUID_RE.match(v) for v in col.sample_values):
        return "uuid"
    if col.guessed_role == ColumnRole.CATEGORICAL and col.cardinality <= 12:
        return "enum"
    return None


def _is_ambiguous(col: ColumnProfile, fingerprint: str | None) -> bool:
    """The deterministic signals disagree or are weak. A numeric column with
    no currency fingerprint is exactly the "score bound to revenue_amount"
    shape (review P0.2) - no name evidence either way. Free text is always
    worth a second look given how much damage a wrong guess there does
    (P1.2). A wide categorical is borderline between a genuine dimension
    and a free-text/identifier column that slipped past the cardinality
    heuristic."""
    if col.guessed_role == ColumnRole.NUMERIC and fingerprint is None:
        return True
    if col.guessed_role == ColumnRole.FREE_TEXT:
        return True
    if col.guessed_role == ColumnRole.CATEGORICAL and col.cardinality > 20:
        return True
    return False


def _percentiles(
    con: duckdb.DuckDBPyConnection, ref: str, col_name: str, dtype: str
) -> tuple[float | None, float | None, float | None]:
    if _base_type(dtype) not in _NUMERIC_DUCKDB_TYPES:
        return None, None, None
    quoted = f'"{col_name}"'
    row = con.execute(
        f"SELECT quantile_cont({quoted}, 0.25), quantile_cont({quoted}, 0.5), "
        f"quantile_cont({quoted}, 0.75) FROM {ref} WHERE {quoted} IS NOT NULL"
    ).fetchone()
    if row is None:
        return None, None, None
    return (
        round(row[0], 4) if row[0] is not None else None,
        round(row[1], 4) if row[1] is not None else None,
        round(row[2], 4) if row[2] is not None else None,
    )


def _top_values(
    con: duckdb.DuckDBPyConnection, ref: str, col_name: str, row_count: int, is_likely_pii: bool
) -> list[tuple[str, int]]:
    if is_likely_pii or row_count == 0 or row_count > TOP_VALUES_ROW_LIMIT:
        return []
    quoted = f'"{col_name}"'
    rows = con.execute(
        f"SELECT {quoted}, COUNT(*) AS n FROM {ref} WHERE {quoted} IS NOT NULL "
        f"GROUP BY {quoted} ORDER BY n DESC LIMIT {MAX_TOP_VALUES}"
    ).fetchall()
    return [(str(v), int(n)) for v, n in rows]


def _column_map_entry(
    con: duckdb.DuckDBPyConnection, ref: str, col: ColumnProfile, row_count: int
) -> ColumnMapEntry:
    fingerprint = _format_fingerprint(col)
    p25, p50, p75 = _percentiles(con, ref, col.name, col.dtype)
    return ColumnMapEntry(
        name=col.name,
        dtype=col.dtype,
        null_pct=col.null_percent,
        cardinality=col.cardinality,
        distinct_ratio=col.distinct_ratio,
        min_value=None if col.min_value is None else str(col.min_value),
        max_value=None if col.max_value is None else str(col.max_value),
        p25=p25,
        p50=p50,
        p75=p75,
        format_fingerprint=fingerprint,
        top_values=_top_values(con, ref, col.name, row_count, col.is_likely_pii),
        guessed_role=col.guessed_role,
        is_likely_pii=col.is_likely_pii,
        ambiguous=_is_ambiguous(col, fingerprint),
    )


def build_data_map(
    data_source: DataSource,
    structural: StructuralProfile,
    con: duckdb.DuckDBPyConnection,
) -> DataMap:
    physical_ref = {t.name: t.physical_ref for t in data_source.tables}
    row_count = {t.name: t.row_count for t in data_source.tables}
    grain_by_table = {g.table: g for g in structural.grains}
    graph: EntityGraph | None = structural.entity_graph

    entities: list[EntityMapEntry] = []
    ambiguous_columns: list[str] = []
    for table in data_source.tables:
        table_cols = [c for c in structural.columns if c.table == table.name]
        entity = graph.entity(table.name) if graph else None
        role = entity.role if entity else "fact"  # a single-table source is definitionally the fact
        grain = grain_by_table.get(table.name)
        grain_desc = grain.description if grain else "unknown"

        columns: list[ColumnMapEntry] = []
        for col in table_cols:
            entry = _column_map_entry(con, physical_ref[table.name], col, row_count[table.name])
            columns.append(entry)
            if entry.ambiguous:
                ambiguous_columns.append(f"{table.name}.{col.name}")

        entities.append(
            EntityMapEntry(
                name=table.name, role=role, grain=grain_desc, row_count=row_count[table.name], columns=columns
            )
        )

    edges = [e for e in (graph.edges if graph else []) if e.verified]
    return DataMap(entities=entities, edges=edges, ambiguous_columns=ambiguous_columns)


def render_prompt(data_map: DataMap, *, char_budget: int = 30_000) -> str:
    """Full detail for ambiguous columns; one line each for the rest. If the
    full render still exceeds the budget, unambiguous columns degrade to a
    bare name list per table - tables are never dropped, only summarized
    more tersely, so the agent always sees the whole shape of the data."""
    full = _render(data_map, verbose_unambiguous=True)
    if len(full) <= char_budget:
        return full
    return _render(data_map, verbose_unambiguous=False)


def _render(data_map: DataMap, *, verbose_unambiguous: bool) -> str:
    lines: list[str] = []
    for entity in data_map.entities:
        lines.append(f"## {entity.name} (role={entity.role}, rows={entity.row_count}, grain={entity.grain})")
        for col in entity.columns:
            if col.ambiguous:
                lines.append(_verbose_line(col))
            elif verbose_unambiguous:
                lines.append(f"  - {col.name}: {col.dtype}, role={col.guessed_role.value}")
            else:
                lines.append(f"  - {col.name}")
        lines.append("")

    if data_map.edges:
        lines.append("## Verified joins")
        for edge in data_map.edges:
            lines.append(
                f"  - {edge.from_table}.{edge.from_column} -> {edge.to_table}.{edge.to_column} "
                f"({edge.cardinality}, overlap={edge.overlap_ratio:.2f}, origin={edge.origin})"
            )
        lines.append("")

    if data_map.ambiguous_columns:
        lines.append("## Ambiguous columns needing a decision")
        lines.append("  " + ", ".join(data_map.ambiguous_columns))

    return "\n".join(lines)


def _verbose_line(col: ColumnMapEntry) -> str:
    parts = [f"  - {col.name}: {col.dtype}, role={col.guessed_role.value} [AMBIGUOUS]"]
    parts.append(f"null%={col.null_pct}, cardinality={col.cardinality}, distinct_ratio={col.distinct_ratio}")
    if col.min_value is not None or col.max_value is not None:
        parts.append(f"range=[{col.min_value}, {col.max_value}]")
    if col.p50 is not None:
        parts.append(f"p25/p50/p75={col.p25}/{col.p50}/{col.p75}")
    if col.format_fingerprint:
        parts.append(f"fingerprint={col.format_fingerprint}")
    if col.top_values:
        top = ", ".join(f"{v!r}:{n}" for v, n in col.top_values[:5])
        parts.append(f"top_values=[{top}]")
    return "    " + "; ".join(parts)


__all__ = ["build_data_map", "render_prompt"]
