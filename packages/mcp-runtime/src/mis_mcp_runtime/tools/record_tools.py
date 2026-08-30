"""Tier 3 Record & Entity Exploration Tools: get_record and get_related_records.

Enables inspecting individual entity records and traversing foreign key relationships
without forcing Claude to construct raw SQL JOIN statements or query denied columns.
"""

from __future__ import annotations

import re
from typing import Any

import duckdb

from mis_mcp_runtime.config import RuntimeConfig
from mis_mcp_runtime.engine.rows import to_json_rows

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def get_record(
    config: RuntimeConfig,
    con: duckdb.DuckDBPyConnection,
    table_or_entity: str,
    id_value: str | int,
    id_column: str | None = None,
) -> dict[str, Any]:
    """Retrieve a single entity record by its unique identifier."""
    if not _IDENTIFIER_PATTERN.match(table_or_entity):
        return {"error": f"Invalid table/entity identifier: {table_or_entity!r}"}

    target_table = None
    for t in config.data_source.tables:
        if (t.name == table_or_entity or t.physical_ref == table_or_entity) and (
            t.name in config.bindings.allowed_tables or t.physical_ref in config.bindings.allowed_tables
        ):
            target_table = t
            break

    if not target_table:
        return {
            "error": f"Table {table_or_entity!r} is not in allowed tables.",
            "allowed_tables": config.bindings.allowed_tables,
        }

    table = target_table
    denied = set(config.bindings.denied_columns)
    safe_columns = [c for c in table.columns if c not in denied]
    if not safe_columns:
        return {"error": "No non-denied columns available for this table."}

    # Resolve ID column
    resolved_id_col = id_column
    if not resolved_id_col:
        # Search for key or id columns
        resolved_id_col = next(
            (c for c in safe_columns if c.lower() in ("id", f"{table.name.lower()}_id", "uuid", "key")),
            safe_columns[0],
        )

    if resolved_id_col in denied:
        return {"error": f"ID column {resolved_id_col!r} is a denied column."}
    if resolved_id_col not in table.columns:
        return {"error": f"ID column {resolved_id_col!r} not found in table {table.name!r}."}

    col_list = ", ".join(f'"{c}"' for c in safe_columns)
    ref = table.physical_ref

    try:
        sql = f'SELECT {col_list} FROM {ref} WHERE "{resolved_id_col}" = ? LIMIT 1'
        df = con.execute(sql, [str(id_value)]).fetchdf()
        rows = to_json_rows(df)
        if not rows:
            return {
                "found": False,
                "table": table.name,
                "id_column": resolved_id_col,
                "id_value": id_value,
                "message": f"No record found with {resolved_id_col} = {id_value!r}",
            }

        return {
            "found": True,
            "table": table.name,
            "id_column": resolved_id_col,
            "id_value": id_value,
            "record": rows[0],
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to retrieve record: {exc}"}


def get_related_records(
    config: RuntimeConfig,
    con: duckdb.DuckDBPyConnection,
    source_table: str,
    source_id: str | int,
    target_table: str,
    foreign_key: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Retrieve related records across tables by following relational foreign keys."""
    if source_table not in config.bindings.allowed_tables:
        return {"error": f"Source table {source_table!r} is not allowed."}
    if target_table not in config.bindings.allowed_tables:
        return {"error": f"Target table {target_table!r} is not allowed."}

    target = next((t for t in config.data_source.tables if t.name == target_table), None)
    if not target:
        return {"error": f"Target table {target_table!r} not found."}

    denied = set(config.bindings.denied_columns)
    safe_columns = [c for c in target.columns if c not in denied]

    # Resolve foreign key in target table
    fk = foreign_key
    if not fk:
        candidate_fks = [
            f"{source_table.lower()}_id",
            f"{source_table.lower().rstrip('s')}_id",
            "parent_id",
        ]
        fk = next((c for c in target.columns if c.lower() in candidate_fks and c not in denied), None)

    if not fk:
        return {
            "error": f"Could not automatically determine foreign key from {source_table!r} to {target_table!r}. Please specify foreign_key.",
            "target_columns": safe_columns,
        }

    if fk in denied:
        return {"error": f"Foreign key column {fk!r} is a denied column."}

    col_list = ", ".join(f'"{c}"' for c in safe_columns)
    ref = target.physical_ref
    bounded_limit = min(max(1, limit), 100)

    try:
        sql = f'SELECT {col_list} FROM {ref} WHERE "{fk}" = ? LIMIT {bounded_limit}'
        df = con.execute(sql, [str(source_id)]).fetchdf()
        rows = to_json_rows(df)

        return {
            "source_table": source_table,
            "source_id": source_id,
            "target_table": target_table,
            "foreign_key": fk,
            "count": len(rows),
            "records": rows,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to retrieve related records: {exc}"}
