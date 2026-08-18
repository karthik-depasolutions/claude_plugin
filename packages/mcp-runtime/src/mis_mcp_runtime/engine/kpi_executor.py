"""Executes a pre-compiled KPI from kpi_defs.json. This SQL was already
sqlglot-validated and bindings-resolved at generation time (see
forge_core.compiler.kpi_compiler) — this executor's job is just to run it
under the same row/timeout guardrails as everything else and check the
KPI's own assertions against the result.
"""

from __future__ import annotations

import time
from typing import Any

import duckdb

from mis_mcp_runtime.config import CompiledKpiConfig
from mis_mcp_runtime.engine.rows import to_json_rows
from mis_mcp_runtime.security.limits import QueryTimeoutError, run_with_timeout

_QUERY_RETRY_ATTEMPTS = 3
_QUERY_RETRY_BASE_DELAY_S = 1.5

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
    last_error: Exception | None = None
    df = None
    for attempt in range(_QUERY_RETRY_ATTEMPTS):
        try:
            df = run_with_timeout(con, kpi.sql, timeout_seconds)
            break
        except (duckdb.Error, QueryTimeoutError) as exc:
            last_error = exc
            if attempt < _QUERY_RETRY_ATTEMPTS - 1:
                time.sleep(_QUERY_RETRY_BASE_DELAY_S * (attempt + 1))
    if df is None:
        assert last_error is not None
        raise last_error

    rows = to_json_rows(df)

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
