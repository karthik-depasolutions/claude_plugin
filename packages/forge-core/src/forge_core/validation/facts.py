"""Check 1 - fact check.

Every binding must resolve to a real `table.column` that actually exists in
the profiled `SchemaProfile`; a non-optional KPI that the compiler had to
skip because a required canonical role never resolved is a hard failure -
it means the generated plugin would silently be missing a KPI it claims to
offer.
"""

from __future__ import annotations

from forge_core.models.bindings import SchemaBindings
from forge_core.models.common import CheckStatus
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.schema_profile import SchemaProfile
from forge_core.models.validation import ValidationCheckResult, ValidationIssue


def check_facts(
    pack: IndustryPack, bindings: SchemaBindings, profile: SchemaProfile, skipped_kpi_ids: list[str]
) -> ValidationCheckResult:
    issues: list[ValidationIssue] = []
    valid_columns_by_table = {
        table.name: {c.name for c in profile.structural.columns if c.table == table.name}
        for table in profile.source.tables
    }

    for table_binding in bindings.tables:
        if table_binding.physical not in {t.physical_ref for t in profile.source.tables}:
            issues.append(
                ValidationIssue(
                    severity="error",
                    location=f"table:{table_binding.alias}",
                    message=f"binding points at {table_binding.physical!r}, which is not a real table",
                )
            )

    for column in bindings.columns:
        all_known_columns = {c for cols in valid_columns_by_table.values() for c in cols}
        if column.physical not in all_known_columns:
            issues.append(
                ValidationIssue(
                    severity="error",
                    location=f"column:{column.role}",
                    message=f"binds to {column.physical!r}, which is not a real profiled column",
                )
            )

    skipped = set(skipped_kpi_ids)
    for kpi in pack.kpis:
        if kpi.id not in skipped:
            continue
        severity = "warning" if kpi.optional else "error"
        issues.append(
            ValidationIssue(
                severity=severity,
                location=f"kpi:{kpi.id}",
                message="required canonical role(s) could not be resolved to a real column",
                details={"requires": kpi.requires.model_dump()},
            )
        )

    if any(i.severity == "error" for i in issues):
        status = CheckStatus.FAIL
    elif issues:
        status = CheckStatus.WARN
    else:
        status = CheckStatus.PASS
    return ValidationCheckResult(check="fact_check", status=status, issues=issues)
