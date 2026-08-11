"""Check 3 - dry run.

Executes every compiled KPI against the real (or sampled) data in DuckDB and
evaluates each of its `assertions` expressions against the single-row
result. A KPI that parses but fails to execute, or violates its own stated
assertion (e.g. `total_revenue >= 0`), is a hard failure - it would return
nonsense or crash the runtime for a real customer.
"""

from __future__ import annotations

from typing import Any

from forge_core.models.common import CheckStatus
from forge_core.models.datasource import DataSource
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.validation import ValidationCheckResult, ValidationIssue
from forge_core.runtime_session import open_session


def _eval_assertion(expr: str, row: dict[str, Any]) -> bool:
    # `row` supplies the KPI's own result columns as bare names, e.g.
    # "total_revenue >= 0". No builtins, no attribute access - just arithmetic
    # comparisons over already-computed numbers.
    return bool(eval(expr, {"__builtins__": {}}, dict(row)))


def check_dry_run(kpi_defs: KpiDefsFile, source: DataSource) -> ValidationCheckResult:
    issues: list[ValidationIssue] = []
    con = open_session(source)
    try:
        for kpi in kpi_defs.kpis:
            location = f"kpi:{kpi.id}"
            try:
                result = con.execute(kpi.sql).fetchdf()
            except Exception as exc:
                issues.append(
                    ValidationIssue(severity="error", location=location, message=f"execution failed: {exc}")
                )
                continue

            if result.shape[0] < 1:
                issues.append(
                    ValidationIssue(severity="error", location=location, message="query returned zero rows")
                )
                continue

            row = result.iloc[0].to_dict()
            for assertion in kpi.assertions:
                try:
                    ok = _eval_assertion(assertion, row)
                except Exception as exc:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            location=location,
                            message=f"could not evaluate assertion {assertion!r}: {exc}",
                        )
                    )
                    continue
                if not ok:
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            location=location,
                            message=f"assertion failed: {assertion!r}",
                            details={"row": {k: str(v) for k, v in row.items()}},
                        )
                    )
    finally:
        con.close()

    status = CheckStatus.FAIL if any(i.severity == "error" for i in issues) else CheckStatus.PASS
    return ValidationCheckResult(check="dry_run", status=status, issues=issues)
