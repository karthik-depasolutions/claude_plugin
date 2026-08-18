"""`get_data_profile` — per-column data quality stats (null %, cardinality).
Prefers the pre-computed, PII-scrubbed `schema_summary.json` shipped with the
plugin; falls back to a live (still denied-column-excluded) computation."""

from __future__ import annotations

from typing import Any

import duckdb

from mis_mcp_runtime.config import RuntimeConfig
from mis_mcp_runtime.security.limits import QueryTimeoutError, run_with_timeout


def get_data_profile(config: RuntimeConfig, con: duckdb.DuckDBPyConnection, table: str) -> dict[str, Any]:
    table_cfg = next((t for t in config.data_source.tables if t.name == table), None)
    if table_cfg is None:
        return {"error": f"Unknown table {table!r}. Allowed: {[t.name for t in config.data_source.tables]}"}

    summary_tables = {t["name"]: t for t in config.schema_summary.get("tables", [])}
    if table in summary_tables and "column_profiles" in summary_tables[table]:
        return {"table": table, "columns": summary_tables[table]["column_profiles"]}

    denied = set(config.bindings.denied_columns)
    columns = [c for c in table_cfg.columns if c not in denied]
    if not columns:
        return {"table": table, "columns": []}

    # One scan with a pair of aggregates per column, rather than one full scan
    # per column - and routed through run_with_timeout so this tool sits behind
    # the same timeout guardrail as every other query path. Aliases are indexed
    # rather than column-derived so no column name is ever spliced into one.
    aggregates = ["COUNT(*) AS total"]
    for i, col in enumerate(columns):
        quoted = f'"{col}"'
        aggregates.append(f"COUNT(*) FILTER (WHERE {quoted} IS NULL) AS nulls_{i}")
        aggregates.append(f"COUNT(DISTINCT {quoted}) AS distinct_{i}")
    sql = f"SELECT {', '.join(aggregates)} FROM {table_cfg.physical_ref}"

    try:
        df = run_with_timeout(con, sql, config.query_timeout_seconds)
    except QueryTimeoutError as exc:
        return {"error": str(exc)}
    if df.empty:
        return {"table": table, "columns": []}

    row = df.iloc[0]
    total = int(row["total"])
    profiles = [
        {
            "column": col,
            "null_percent": round((int(row[f"nulls_{i}"]) / total * 100.0) if total else 0.0, 2),
            "cardinality": int(row[f"distinct_{i}"]),
        }
        for i, col in enumerate(columns)
    ]
    return {"table": table, "columns": profiles}
