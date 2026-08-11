"""Executes a pre-compiled KPI from kpi_defs.json. This SQL was already
sqlglot-validated and bindings-resolved at generation time (see
forge_core.compiler.kpi_compiler) — this executor's job is just to run it
under the same row/timeout guardrails as everything else and check the
KPI's own assertions against the result.
"""

from __future__ import annotations

from typing import Any

import duckdb

from mis_mcp_runtime.config import CompiledKpiConfig
from mis_mcp_runtime.security.limits import run_with_timeout

_SAFE_BUILTINS = {"abs": abs, "min": min, "max": max, "round": round, "len": len}


def _check_assertions(assertions: list[str], row: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for expr in assertions:
        try:
            passed = bool(eval(expr, {"__builtins__": _SAFE_BUILTINS}, row))  # noqa: S307
            results.append({"assertion": expr, "passed": passed})
        except Exception as exc:  # noqa: BLE001 - report, never crash the tool call
            results.append({"assertion": expr, "passed": False, "error": str(exc)})
    return results


def execute_kpi(
    con: duckdb.DuckDBPyConnection, kpi: CompiledKpiConfig, timeout_seconds: int
) -> dict[str, Any]:
    df = run_with_timeout(con, kpi.sql, timeout_seconds)
    rows = df.where(df.notnull(), None).to_dict(orient="records")

    assertion_results: list[dict[str, Any]] = []
    if rows and kpi.assertions:
        assertion_results = _check_assertions(kpi.assertions, rows[0])

    return {
        "kpi_id": kpi.id,
        "label": kpi.label,
        "description": kpi.description,
        "unit": kpi.unit,
        "rows": rows,
        "row_count": len(rows),
        "assertions": assertion_results,
    }
