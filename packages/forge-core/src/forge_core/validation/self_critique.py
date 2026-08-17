"""Check 8 - self-critique.

A second, independent LLM pass reviews the *already-generated* prose
(skills/agents/commands/dashboard) against the compiled KPI catalog and
guardrails, looking for hallucinated facts, invented numbers, or guardrail
violations that slipped past the deterministic checks. `error` severity
findings block packaging; `warning` findings surface but don't block.
"""

from __future__ import annotations

import re
from typing import Any

from forge_core.generation.constants import TOOL_NAMES
from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.common import CheckStatus
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.validation import ValidationCheckResult, ValidationIssue

_VALID_SEVERITIES = {"info", "warning", "error"}
_BACKTICK_RE = re.compile(r"`([^`]+)`")
# Findings that mention PII / fabricated business numbers / invented KPI ids
# must never be dropped, even if they also name an allowed MCP tool.
_KEEP_MARKERS = (
    "pii",
    "personally identifiable",
    "email",
    "phone",
    "fabricated",
    "invented kpi",
    "guardrail violation",
)


def _build_prompt(pack: IndustryPack, kpi_defs: KpiDefsFile, generated_texts: dict[str, str]) -> str:
    kpi_catalog = "\n".join(f"- {k.id}: {k.label} - {k.description}" for k in kpi_defs.kpis)
    guardrails = "\n".join(f"- {n}" for n in pack.guardrails.notes)
    sections = "\n\n".join(f"### {name}\n{text}" for name, text in generated_texts.items())
    allowed_tools = ", ".join(TOOL_NAMES)
    return (
        f"You are reviewing AI-generated content for a Claude plugin covering {pack.name} business data.\n"
        f"The ONLY real, verified KPI ids (for get_kpi) are:\n{kpi_catalog}\n\n"
        f"The plugin's MCP runtime also exposes these tools, and agent/skill/command files "
        f"MUST mention them: {allowed_tools}. "
        "get_data_profile returns column-quality stats, search_records returns row lookups, "
        "describe_schema returns structure, run_safe_query runs a guarded SELECT. "
        "Those are not KPIs. Do NOT flag their presence, their allow-list, or the fact that "
        "they can return numbers outside the KPI catalog.\n\n"
        f"The guardrails that must never be violated:\n{guardrails or '(none declared)'}\n\n"
        "Review the following generated files for: (1) a KPI id that is NOT in the catalog "
        "above, (2) a specific numeric claim about the business that is not framed as coming "
        "from a tool call (fabricated data, e.g. 'revenue is $4.2M'), (3) any guardrail "
        "violation, (4) any personally identifiable information.\n\n"
        f"{sections}\n\n"
        'Respond with strict JSON: {"findings": [{"severity": "info"|"warning"|"error", '
        '"location": "<file section>", "message": "<what is wrong>"}]}. '
        'If nothing is wrong, respond {"findings": []}.'
    )


def _quoted_tokens(message: str) -> set[str]:
    return {m.group(1).strip() for m in _BACKTICK_RE.finditer(message) if m.group(1).strip()}


def _tool_basename(token: str) -> str:
    """`mcp__mis-mcp-runtime__get_kpi` and `get_kpi` are the same tool."""
    if token.startswith("mcp__") and "__" in token[5:]:
        return token.rsplit("__", 1)[-1]
    return token


def _is_allowed_tool_false_positive(message: str, known_kpi_ids: set[str]) -> bool:
    """The critic sometimes treats the runtime's legitimate MCP tools as
    invented metrics because they return numbers that aren't in the KPI
    catalog. Those tools are supposed to exist — drop that class of finding,
    but keep anything that also looks like PII, a fabricated number, or an
    unknown KPI id."""
    lower = message.lower()
    if any(marker in lower for marker in _KEEP_MARKERS):
        return False
    quoted = _quoted_tokens(message)
    if not quoted:
        return False
    allowed = set(TOOL_NAMES) | known_kpi_ids
    leftover = {_tool_basename(token) for token in quoted} - allowed
    return not leftover and any(_tool_basename(token) in TOOL_NAMES for token in quoted)


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

    known_kpi_ids = {k.id for k in kpi_defs.kpis} | set(kpi_defs.skipped)
    raw_findings = response.get("findings", []) if isinstance(response, dict) else []
    issues: list[ValidationIssue] = []
    for finding in raw_findings:
        message = finding.get("message", "unspecified finding")
        if _is_allowed_tool_false_positive(message, known_kpi_ids):
            continue
        severity = finding.get("severity", "warning")
        if severity not in _VALID_SEVERITIES:
            severity = "warning"
        issues.append(
            ValidationIssue(
                severity=severity,
                location=finding.get("location", "generated_content"),
                message=message,
            )
        )

    if any(i.severity == "error" for i in issues):
        status = CheckStatus.FAIL
    elif issues:
        status = CheckStatus.WARN
    else:
        status = CheckStatus.PASS
    return ValidationCheckResult(check="self_critique", status=status, issues=issues)
