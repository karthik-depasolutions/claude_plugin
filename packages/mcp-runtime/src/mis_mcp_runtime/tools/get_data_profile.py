"""`get_data_profile` — per-column data quality stats (null %, cardinality).
Prefers the pre-computed, PII-scrubbed `schema_summary.json` shipped with the
plugin; falls back to a live (still denied-column-excluded) computation."""

from __future__ import annotations

from typing import Any

import duckdb

from mis_mcp_runtime.config import RuntimeConfig


def get_data_profile(config: RuntimeConfig, con: duckdb.DuckDBPyConnection, table: str) -> dict[str, Any]:
    table_cfg = next((t for t in config.data_source.tables if t.name == table), None)
    if table_cfg is None:
        return {"error": f"Unknown table {table!r}. Allowed: {[t.name for t in config.data_source.tables]}"}

    summary_tables = {t["name"]: t for t in config.schema_summary.get("tables", [])}
    if table in summary_tables and "column_profiles" in summary_tables[table]:
        return {"table": table, "columns": summary_tables[table]["column_profiles"]}

    denied = set(config.bindings.denied_columns)
    columns = [c for c in table_cfg.columns if c not in denied]
    profiles = []
    for col in columns:
        quoted = f'"{col}"'
        row = con.execute(
            f"SELECT COUNT(*) AS total, "
            f"COUNT(*) FILTER (WHERE {quoted} IS NULL) AS nulls, "
            f"COUNT(DISTINCT {quoted}) AS distinct_count "
            f"FROM {table_cfg.physical_ref}"
        ).fetchone()
        total, nulls, distinct_count = row if row else (0, 0, 0)
        profiles.append(
            {
                "column": col,
                "null_percent": round((nulls / total * 100.0) if total else 0.0, 2),
                "cardinality": distinct_count,
            }
        )
    return {"table": table, "columns": profiles}
