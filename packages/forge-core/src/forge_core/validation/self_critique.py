"""Check 8 - self-critique.

A second, independent LLM pass reviews the *already-generated* prose
(skills/agents/commands/dashboard) against the compiled KPI catalog and
guardrails, looking for hallucinated facts, invented numbers, or guardrail
violations that slipped past the deterministic checks. `error` severity
findings block packaging; `warning` findings surface but don't block.
"""

from __future__ import annotations

from typing import Any

from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.common import CheckStatus
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.validation import ValidationCheckResult, ValidationIssue

_VALID_SEVERITIES = {"info", "warning", "error"}


def _build_prompt(pack: IndustryPack, kpi_defs: KpiDefsFile, generated_texts: dict[str, str]) -> str:
    kpi_catalog = "\n".join(f"- {k.id}: {k.label} - {k.description}" for k in kpi_defs.kpis)
    guardrails = "\n".join(f"- {n}" for n in pack.guardrails.notes)
    sections = "\n\n".join(f"### {name}\n{text}" for name, text in generated_texts.items())
    return (
        f"You are reviewing AI-generated content for a Claude plugin covering {pack.name} business data.\n"
        f"The ONLY real, verified KPIs are:\n{kpi_catalog}\n\n"
        f"The guardrails that must never be violated:\n{guardrails or '(none declared)'}\n\n"
        "Review the following generated files for: (1) any KPI id, metric, or number that is NOT in the "
        "list above, (2) any specific numeric claim about the business (fabricated data), (3) any "
        "guardrail violation, (4) any personally identifiable information.\n\n"
        f"{sections}\n\n"
        'Respond with strict JSON: {"findings": [{"severity": "info"|"warning"|"error", '
        '"location": "<file section>", "message": "<what is wrong>"}]}. '
        'If nothing is wrong, respond {"findings": []}.'
    )


def check_self_critique(
    pack: IndustryPack,
    kpi_defs: KpiDefsFile,
    generated_texts: dict[str, str],
    provider: LLMProvider | None,
) -> ValidationCheckResult:
    if provider is None:
        return ValidationCheckResult(
            check="self_critique",
            status=CheckStatus.SKIPPED,
            skipped_reason="no LLM provider configured for self-critique",
        )

    prompt = _build_prompt(pack, kpi_defs, generated_texts)
    try:
        response: Any = provider.generate_json(prompt)
    except LLMError as exc:
        return ValidationCheckResult(
            check="self_critique",
            status=CheckStatus.WARN,
            skipped_reason=f"self-critique LLM call failed: {exc}",
        )

    raw_findings = response.get("findings", []) if isinstance(response, dict) else []
    issues: list[ValidationIssue] = []
    for finding in raw_findings:
        severity = finding.get("severity", "warning")
        if severity not in _VALID_SEVERITIES:
            severity = "warning"
        issues.append(
            ValidationIssue(
                severity=severity,
                location=finding.get("location", "generated_content"),
                message=finding.get("message", "unspecified finding"),
            )
        )

    if any(i.severity == "error" for i in issues):
        status = CheckStatus.FAIL
    elif issues:
        status = CheckStatus.WARN
    else:
        status = CheckStatus.PASS
    return ValidationCheckResult(check="self_critique", status=status, issues=issues)
