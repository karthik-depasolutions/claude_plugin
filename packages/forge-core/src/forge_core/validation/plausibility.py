"""Check 9 (agent-judged) — binding plausibility.

The deterministic scorer binds by *name and type* — `score` (0..100) matches
`revenue_amount` because both are numbers. That is the worst defect this
system ships (test scores reported as revenue), and no name-based check can
see it. A fixed per-role lookup table doesn't fix that — it only ever
generalizes to the specific roles whoever wrote the table thought of, which
is the same "name-based, not data-driven" failure mode one level up.

This check instead asks an LLM to judge every bound role's real distribution
— min/max/cardinality/null%/distinct_ratio, computed here, never guessed —
against the role's own declared meaning from the pack (`pack.canonical_roles`).
It never sees the column or role *names* as evidence, only the numbers and
the meaning, and it generalizes to any canonical role any pack ever defines,
not a fixed set of three.

Severity "error" blocks packaging via ValidationReport.compute_overall — a
plausibility failure is a hard stop, not a warning, because the plugin would
ship a wrong number.

Without a provider (`--no-llm`), there's no judgment to run — but leaving
this check fully silent would mean `--no-llm` deployments ship the exact
headline bug (a test score reported as revenue) with zero protection. So a
minimal, explicitly degraded fallback runs instead: a keyword match on the
role's own description text for money-shaped language, checked against a
0-100-bounded numeric column — the one catastrophic, well-known pattern,
not a general rule table. The moment a provider is available the real
judgment above runs instead and this fallback never fires.
"""

from __future__ import annotations

from typing import Any

from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.bindings import ColumnBinding, SchemaBindings
from forge_core.models.common import CheckStatus
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.schema_profile import ColumnProfile, SchemaProfile
from forge_core.models.validation import ValidationCheckResult, ValidationIssue

_PROMPT_TEMPLATE = """You are auditing whether bound columns genuinely represent the business \
concepts they were matched to — using ONLY the real, computed statistics below, never the column \
or role's name. A column literally named "revenue" can still be the wrong data; a column named \
"x3" can still be the right data, if its numbers fit.

BOUND ROLES — each one's declared business meaning, the real column matched to it, and that \
column's actual computed distribution:
{bindings_block}

For each one, judge: does this column's ACTUAL DATA plausibly represent what the role means? \
Examples of real problems: a role meaning a monetary amount bound to a column bounded 0-100 with no \
currency formatting (that's a score or percentage, not money); a role meaning a count or amount \
bound to a column that's always negative; a role needing a time series bound to a date/timestamp \
column with only one distinct value.

Return ONLY JSON: {{"implausible": [{{"role": "<role name, copied exactly from above>", \
"reason": "<one sentence, citing the actual numbers>"}}]}}. Omit any role whose data plausibly fits \
its meaning — only list genuine problems, not every role you reviewed.
"""


def _resolve_column(
    binding: ColumnBinding, bindings: SchemaBindings, profile: SchemaProfile
) -> ColumnProfile | None:
    physical_by_alias = {t.alias: t.physical for t in bindings.tables}
    table_name_by_physical = {t.physical_ref: t.name for t in profile.source.tables}
    physical = physical_by_alias.get(binding.table_alias, binding.table_alias)
    physical_table = table_name_by_physical.get(physical) or binding.table_alias
    return next(
        (c for c in profile.structural.columns if c.table == physical_table and c.name == binding.physical),
        None,
    )


def _stats_line(col: ColumnProfile) -> str:
    return (
        f"dtype={col.dtype}, min={col.min_value!r}, max={col.max_value!r}, "
        f"cardinality={col.cardinality}, distinct_ratio={col.distinct_ratio:.3f}, "
        f"null%={col.null_percent:.1f}"
    )


def _bindings_block(
    bindings: SchemaBindings, profile: SchemaProfile, pack: IndustryPack
) -> tuple[str, dict[str, ColumnBinding]]:
    lines: list[str] = []
    by_role: dict[str, ColumnBinding] = {}
    for binding in bindings.columns:
        col = _resolve_column(binding, bindings, profile)
        if col is None:
            continue
        meaning = pack.canonical_roles.get(binding.role, binding.role)
        lines.append(f'- role "{binding.role}" means "{meaning}" — {_stats_line(col)}')
        by_role[binding.role] = binding
    return "\n".join(lines), by_role


_MONEY_WORDS = ("money", "monetary", "revenue", "price", "amount", "currency", "cost", "payment", "$")


def _fallback_currency_check(
    bindings: SchemaBindings, profile: SchemaProfile, pack: IndustryPack
) -> ValidationCheckResult:
    """Degraded, `--no-llm`-only fallback — see module docstring. Catches
    exactly one pattern via a keyword match on the role's own description,
    never the role or column *name*: a money-meaning role bound to a column
    bounded 0-100, which looks like a score or percentage. Not a substitute
    for the real judgment; only runs when there is no provider to ask."""
    issues: list[ValidationIssue] = []
    for binding in bindings.columns:
        meaning = pack.canonical_roles.get(binding.role, "").lower()
        if not any(word in meaning for word in _MONEY_WORDS):
            continue
        col = _resolve_column(binding, bindings, profile)
        if col is None:
            continue
        if (
            isinstance(col.min_value, (int, float))
            and isinstance(col.max_value, (int, float))
            and col.min_value >= 0
            and col.max_value <= 100
        ):
            issues.append(
                ValidationIssue(
                    severity="error",
                    location=f"binding:{binding.role}",
                    message=(
                        f"'{binding.physical}' is bound to '{binding.role}' but values are bounded "
                        f"0-100, which looks like a score or percentage, not a monetary amount "
                        f"(binding confidence was {binding.confidence:.2f}). This ran as a degraded "
                        f"check with no LLM provider configured — only this one pattern is caught; "
                        f"enable an LLM provider for full plausibility judgment."
                    ),
                )
            )
    status = CheckStatus.FAIL if issues else CheckStatus.PASS
    return ValidationCheckResult(check="binding_plausibility", status=status, issues=issues)


def check_binding_plausibility(
    bindings: SchemaBindings,
    profile: SchemaProfile,
    pack: IndustryPack,
    provider: LLMProvider | None,
) -> ValidationCheckResult:
    if provider is None:
        return _fallback_currency_check(bindings, profile, pack)

    block, by_role = _bindings_block(bindings, profile, pack)
    if not by_role:
        return ValidationCheckResult(check="binding_plausibility", status=CheckStatus.PASS)

    try:
        raw: Any = provider.generate_json(_PROMPT_TEMPLATE.format(bindings_block=block))
    except LLMError:
        # A failed call is not the same as "no provider configured", but the
        # outcome for coverage is the same - fall back rather than leave this
        # run with zero protection against the one known catastrophic pattern.
        return _fallback_currency_check(bindings, profile, pack)
    if not isinstance(raw, dict):
        return _fallback_currency_check(bindings, profile, pack)

    issues: list[ValidationIssue] = []
    for item in raw.get("implausible", []):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        reason = item.get("reason")
        # Grounding: a role the model didn't name in bindings_block is never
        # trusted, same invariant every other LLM call site in this pipeline
        # enforces (binding proposers, the KPI proposer, question generation).
        if role not in by_role or not isinstance(reason, str) or not reason.strip():
            continue
        binding = by_role[role]
        issues.append(
            ValidationIssue(
                severity="error",
                location=f"binding:{role}",
                message=(
                    f"'{binding.physical}' is bound to '{role}' but {reason.strip()} "
                    f"(binding confidence was {binding.confidence:.2f})."
                ),
            )
        )
    status = CheckStatus.FAIL if issues else CheckStatus.PASS
    return ValidationCheckResult(check="binding_plausibility", status=status, issues=issues)


__all__ = ["check_binding_plausibility"]
