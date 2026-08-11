"""Check 2 - SQL safety.

Every compiled KPI's SQL is re-parsed and checked against the same
read-only, allow-listed, denied-column policy the runtime enforces at
execution time. This is a deliberately *independent* second pass:
forge_core never imports mis_mcp_runtime (and vice versa - see
mis_mcp_runtime.security.sql_policy for the runtime's own copy) so the
pipeline can't accidentally trust only the compiler's own sqlglot pass.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from forge_core.models.bindings import SchemaBindings
from forge_core.models.common import CheckStatus
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.validation import ValidationCheckResult, ValidationIssue

_ALLOWED_ROOT_TYPES = (exp.Select, exp.With)
_FORBIDDEN_EXPRESSION_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Attach,
    exp.Command,
    exp.Pragma,
    exp.Merge,
    exp.TruncateTable,
    exp.Copy,
)


def _normalize(ref: str) -> str:
    return ref.replace('"', "").replace("'", "").strip().lower()


def check_sql_safety(kpi_defs: KpiDefsFile, bindings: SchemaBindings) -> ValidationCheckResult:
    issues: list[ValidationIssue] = []
    allowed_tables = {_normalize(t) for t in bindings.allowed_tables}
    denied_columns = {c.lower() for c in bindings.denied_columns}

    for kpi in kpi_defs.kpis:
        location = f"kpi:{kpi.id}"
        try:
            statements = [s for s in sqlglot.parse(kpi.sql, read="duckdb") if s is not None]
        except sqlglot.errors.ParseError as exc:
            issues.append(
                ValidationIssue(severity="error", location=location, message=f"unparseable SQL: {exc}")
            )
            continue

        if len(statements) != 1:
            issues.append(
                ValidationIssue(
                    severity="error", location=location, message="expected exactly one SQL statement"
                )
            )
            continue
        statement = statements[0]

        if not isinstance(statement, _ALLOWED_ROOT_TYPES):
            issues.append(
                ValidationIssue(
                    severity="error",
                    location=location,
                    message=f"root statement type {type(statement).__name__} is not SELECT/WITH",
                )
            )

        for forbidden in _FORBIDDEN_EXPRESSION_TYPES:
            if list(statement.find_all(forbidden)):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        location=location,
                        message=f"contains a forbidden operation: {forbidden.__name__}",
                    )
                )

        for select in statement.find_all(exp.Select):
            if any(isinstance(e, exp.Star) for e in select.expressions):
                issues.append(
                    ValidationIssue(severity="error", location=location, message="SELECT * is not allowed")
                )

        for table in statement.find_all(exp.Table):
            ref = _normalize(table.sql(dialect="duckdb"))
            is_allowed = ref in allowed_tables or any(
                ref.endswith(f".{a}") or a.endswith(f".{ref}") for a in allowed_tables
            )
            if not is_allowed:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        location=location,
                        message=f"references non-allow-listed table {table.sql(dialect='duckdb')!r}",
                    )
                )

        for column in statement.find_all(exp.Column):
            if column.name.lower() in denied_columns:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        location=location,
                        message=f"projects or filters on denied column {column.name!r}",
                    )
                )

    status = CheckStatus.FAIL if any(i.severity == "error" for i in issues) else CheckStatus.PASS
    return ValidationCheckResult(check="sql_safety", status=status, issues=issues)
