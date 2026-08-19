"""Check 3 - dry run.

Executes every compiled KPI against the real (or sampled) data in DuckDB and
evaluates each of its `assertions` expressions against the single-row
result. A KPI that parses but fails to execute, or violates its own stated
assertion (e.g. `total_revenue >= 0`), is a hard failure - it would return
nonsense or crash the runtime for a real customer.

Assertions are evaluated with the whitelist AST evaluator (not eval): they
are LLM-authored on the --agent path and can carry prompt-injected code.
"""

from __future__ import annotations

from forge_core.models.common import CheckStatus
from forge_core.models.datasource import DataSource
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.validation import ValidationCheckResult, ValidationIssue
from forge_core.runtime_session import open_session
from forge_core.validation.assertion_policy import evaluate_assertion


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
                    ok = evaluate_assertion(assertion, dict(row))
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
