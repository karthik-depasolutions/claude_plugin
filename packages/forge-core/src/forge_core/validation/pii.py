"""Check 4 - PII scan.

Two independent passes: (1) a hard-fail guarantee that no denied column
identifier ever reaches a compiled KPI's SQL, and (2) a best-effort regex
scan of every generated artifact (skill/agent/command prose, the dashboard
snapshot) for values that *look* like PII (email, phone, SSN, Aadhaar, PAN).
The second pass is a defense-in-depth warning, not a hard fail - regex
pattern matches on free text are inherently approximate.
"""

from __future__ import annotations

import re

from forge_core.models.bindings import SchemaBindings
from forge_core.models.common import CheckStatus
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.validation import ValidationCheckResult, ValidationIssue

_VALUE_PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "aadhaar": re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "phone": re.compile(
        r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}|\d{10})\b"
    ),
}


def check_pii(
    kpi_defs: KpiDefsFile, bindings: SchemaBindings, generated_texts: dict[str, str]
) -> ValidationCheckResult:
    issues: list[ValidationIssue] = []

    for kpi in kpi_defs.kpis:
        for denied in bindings.denied_columns:
            if f'"{denied}"' in kpi.sql:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        location=f"kpi:{kpi.id}",
                        message=f"denied column {denied!r} appears in compiled SQL",
                    )
                )

    for location, text in generated_texts.items():
        for kind, pattern in _VALUE_PATTERNS.items():
            match = pattern.search(text)
            if match:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        location=location,
                        message=f"text resembles a {kind} value: {match.group(0)!r}",
                        suggestion="Confirm this is not real customer data leaking into a static artifact.",
                    )
                )

    status = CheckStatus.FAIL if any(i.severity == "error" for i in issues) else CheckStatus.PASS
    if status == CheckStatus.PASS and any(i.severity == "warning" for i in issues):
        status = CheckStatus.WARN
    return ValidationCheckResult(check="pii_scan", status=status, issues=issues)
