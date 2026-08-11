"""`get_kpi` — the preferred way to get a number out of this plugin. Looks up
a pre-compiled, pre-validated KPI by id and executes it. This is the tool
skills and recipes are generated to reach for before ad-hoc SQL."""

from __future__ import annotations

from typing import Any

import duckdb

from mis_mcp_runtime.config import RuntimeConfig
from mis_mcp_runtime.engine.kpi_executor import execute_kpi


def get_kpi(config: RuntimeConfig, con: duckdb.DuckDBPyConnection, kpi_id: str) -> dict[str, Any]:
    kpi = next((k for k in config.kpis if k.id == kpi_id), None)
    if kpi is None:
        available = [k.id for k in config.kpis]
        return {"error": f"Unknown kpi_id {kpi_id!r}. Available: {available}"}
    return execute_kpi(con, kpi, config.query_timeout_seconds)


def list_kpis(config: RuntimeConfig) -> dict[str, Any]:
    return {
        "kpis": [
            {"id": k.id, "label": k.label, "description": k.description, "unit": k.unit}
            for k in config.kpis
        ]
    }
