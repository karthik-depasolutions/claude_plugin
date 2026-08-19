"""`describe_schema` — structural metadata only (tables, columns, dtypes,
guessed roles). No row-level data ever leaves this tool."""

from __future__ import annotations

from typing import Any

from mis_mcp_runtime.config import RuntimeConfig


def describe_schema(config: RuntimeConfig) -> dict[str, Any]:
    summary = config.schema_summary
    if summary and "tables" in summary:
        return {
            "pack": summary.get("pack_slug"),
            "tables": summary["tables"],
            "denied_columns": config.bindings.denied_columns,
            # `.get` with a default is mandatory: hosted_mcp runs the current
            # runtime against already-on-disk plugin configs that predate this
            # field, so a missing key must yield {} rather than raise.
            "data_context": summary.get("data_context", {}),
        }

    # Fallback: derive a minimal structural view straight from data_source.json
    # if no richer schema_summary.json was packaged.
    tables = []
    for table in config.data_source.tables:
        tables.append({"name": table.name, "columns": table.columns})
    return {
        "pack": None,
        "tables": tables,
        "denied_columns": config.bindings.denied_columns,
        "data_context": (summary or {}).get("data_context", {}),
    }
