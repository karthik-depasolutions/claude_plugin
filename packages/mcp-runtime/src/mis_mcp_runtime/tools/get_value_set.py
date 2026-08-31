"""Tier 1 Discovery Tool: get_value_set.

Safe categorical discovery: inspect distinct values and distribution for a field
without allowing arbitrary exploratory SQL. Denied columns are strictly rejected.
"""

from __future__ import annotations

import re
from typing import Any

import duckdb

from mis_mcp_runtime.config import RuntimeConfig

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def get_value_set(
    config: RuntimeConfig,
    con: duckdb.DuckDBPyConnection,
    field: str,
    table: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Retrieve the distinct values and frequency counts for a categorical field."""
    # 1. Denied column check
    if field in config.bindings.denied_columns:
        return {"error": f"Column {field!r} is a denied column and cannot be inspected."}

    # 2. Syntax sanitize
    if not _IDENTIFIER_PATTERN.match(field):
        return {"error": f"Invalid field identifier: {field!r}"}

    # 3. Resolve table
    target_table = None
    if table:
        for t in config.data_source.tables:
            if (t.name == table or t.physical_ref == table) and (
                t.name in config.bindings.allowed_tables or t.physical_ref in config.bindings.allowed_tables
            ):
                target_table = t
                break
    else:
        # Search for table containing this column among allowed tables
        for t in config.data_source.tables:
            if (
                t.name in config.bindings.allowed_tables or t.physical_ref in config.bindings.allowed_tables
            ) and field in t.columns:
                target_table = t
                break

    if not target_table:
        return {"error": f"Could not find allowed table containing field {field!r}."}

    if field not in target_table.columns:
        return {"error": f"Field {field!r} not found in table {target_table.name!r}."}

    ref = target_table.physical_ref
    bounded_limit = min(max(1, limit), 200)

    try:
        total_non_null = con.execute(
            f'SELECT COUNT(*) FROM {ref} WHERE "{field}" IS NOT NULL'
        ).fetchone()[0]

        sql = (
            f'SELECT "{field}" AS val, COUNT(*) AS cnt '
            f'FROM {ref} '
            f'WHERE "{field}" IS NOT NULL '
            f'GROUP BY 1 '
            f'ORDER BY cnt DESC, val ASC '
            f'LIMIT {bounded_limit}'
        )
        rows = con.execute(sql).fetchall()

        values = []
        for r in rows:
            val = str(r[0]) if r[0] is not None else "null"
            count = int(r[1])
            pct = round((count / total_non_null * 100.0) if total_non_null else 0.0, 2)
            values.append({"value": val, "count": count, "percent": pct})

        return {
            "table": target_table.name,
            "field": field,
            "total_non_null_rows": total_non_null,
            "distinct_returned": len(values),
            "values": values,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to retrieve value set for {field!r}: {exc}"}
