"""`describe_schema` — structural metadata only (tables, columns, dtypes,
guessed roles). No row-level data ever leaves this tool."""

from __future__ import annotations

from typing import Any

from mis_mcp_runtime.config import RuntimeConfig


def describe_schema(config: RuntimeConfig, table: str | None = None) -> dict[str, Any]:
    summary = config.schema_summary
    if summary and "tables" in summary:
        tables = summary["tables"]
        if table:
            matched = [t for t in tables if t.get("name") == table]
            if not matched:
                return {
                    "error": f"Table {table!r} not found. Available tables: {[t.get('name') for t in tables]}",
                    "available_tables": [t.get("name") for t in tables],
                }
            tables = matched
        return {
            "pack": summary.get("pack_slug"),
            "tables": tables,
            "denied_columns": config.bindings.denied_columns,
        }

    # Fallback: derive a minimal structural view straight from data_source.json
    # if no richer schema_summary.json was packaged.
    raw_tables = []
    for t in config.data_source.tables:
        if table and t.name != table:
            continue
        raw_tables.append({"name": t.name, "columns": t.columns})
    if table and not raw_tables:
        return {
            "error": f"Table {table!r} not found.",
            "available_tables": [t.name for t in config.data_source.tables],
        }
    return {"pack": None, "tables": raw_tables, "denied_columns": config.bindings.denied_columns}
