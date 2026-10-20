"""Check 8 - schema-model integrity.

The knowledge pack (`config/schema_model.json`) is synthesized during
PROFILE and fact-checked / cookbook-validated there. This re-verifies it at
the packaging gate, catching drift between synthesis and packaging or a
stale on-disk cache:

  - every table / column the model names must exist in the structural profile
  - every `verified` cookbook query must still execute against the data
  - a thin knowledge pack (low cookbook pass rate / column coverage) WARNs -
    it still ships, but it's the weakest, most-copyable deliverable
"""

from __future__ import annotations

from forge_core.models.common import CheckStatus
from forge_core.models.schema_model import SchemaModel
from forge_core.models.schema_profile import SchemaProfile
from forge_core.models.validation import ValidationCheckResult, ValidationIssue
from forge_core.runtime_session import open_session

_MAX_COOKBOOK_CHECKED = 60
_MIN_COOKBOOK_PASS_RATE = 0.7
_MIN_SCHEMA_COVERAGE = 1.0


def check_schema_model(
    schema_model: SchemaModel | None, profile: SchemaProfile
) -> ValidationCheckResult:
    if schema_model is None:
        return ValidationCheckResult(
            check="schema_model",
            status=CheckStatus.SKIPPED,
            skipped_reason="no schema model was synthesized for this run",
        )

    issues: list[ValidationIssue] = []
    real_tables = {t.name for t in profile.source.tables}
    real_columns = {(c.table, c.name) for c in profile.structural.columns}

    for td in schema_model.tables:
        if td.name not in real_tables:
            issues.append(
                ValidationIssue(
                    severity="error", location=f"tables/{td.name}",
                    message=f"documents table {td.name!r}, which is not in the profiled schema",
                )
            )
            continue
        for cd in td.columns:
            if (td.name, cd.name) not in real_columns:
                issues.append(
                    ValidationIssue(
                        severity="error", location=f"tables/{td.name}/{cd.name}",
                        message=f"documents column {cd.name!r} on {td.name!r}, which does not exist",
                    )
                )

    for rel in schema_model.relationships:
        for ref in (rel.from_ref, rel.to_ref):
            table, _, column = ref.partition(".")
            if (table, column) not in real_columns:
                issues.append(
                    ValidationIssue(
                        severity="warning", location=f"relationships/{ref}",
                        message=f"relationship references {ref!r}, which is not a real column",
                    )
                )

    if schema_model.coverage:
        pass_rate = schema_model.cookbook_pass_rate()
        coverage = schema_model.schema_coverage()
        if schema_model.coverage.get("cookbook_total", 0) and pass_rate < _MIN_COOKBOOK_PASS_RATE:
            issues.append(
                ValidationIssue(
                    severity="warning", location="cookbook",
                    message=f"cookbook pass rate {pass_rate:.0%} is below "
                    f"{_MIN_COOKBOOK_PASS_RATE:.0%} - the NL->SQL knowledge pack is thin",
                )
            )
        if coverage < _MIN_SCHEMA_COVERAGE:
            issues.append(
                ValidationIssue(
                    severity="warning", location="tables",
                    message=f"only {coverage:.0%} of columns have a specific meaning "
                    "(the rest are structural-role fallbacks)",
                )
            )

    verified = [e for e in schema_model.cookbook if e.verified][:_MAX_COOKBOOK_CHECKED]
    if verified:
        con = open_session(profile.source)
        try:
            for i, entry in enumerate(verified):
                try:
                    con.execute(entry.sql).fetchmany(1)
                except Exception as exc:  # noqa: BLE001 - a non-runnable cookbook query is the finding
                    issues.append(
                        ValidationIssue(
                            severity="error", location=f"cookbook[{i}]",
                            message=f"verified cookbook query no longer executes: {exc}",
                            details={"sql": entry.sql[:400]},
                        )
                    )
        finally:
            con.close()

    if any(i.severity == "error" for i in issues):
        status = CheckStatus.FAIL
    elif issues:
        status = CheckStatus.WARN
    else:
        status = CheckStatus.PASS
    return ValidationCheckResult(check="schema_model", status=status, issues=issues)
