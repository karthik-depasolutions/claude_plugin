"""Check 9 - binding plausibility.

Verifies every bound role's real column distribution against the shape the
role logically implies. The deterministic scorer binds by *name and type* —
`score` (0..100) matches `revenue_amount` because both are numbers. That is
the worst defect this system ships (test scores reported as revenue), and no
name-based check can see it. Distribution can: money is not a 0-100 scale.

Rules are keyed on canonical role and read ONLY from ColumnProfile statistics
(min/max/cardinality/distinct_ratio) — never from the column name. The name is
what got the system here; the numbers are the evidence.

Severity "error" blocks packaging via ValidationReport.compute_overall; a
plausibility failure is a hard stop, not a warning, because the plugin would
ship a wrong number.
"""

from __future__ import annotations

from collections.abc import Callable

from forge_core.models.bindings import SchemaBindings
from forge_core.models.common import CheckStatus
from forge_core.models.schema_profile import ColumnProfile, SchemaProfile
from forge_core.models.validation import ValidationCheckResult, ValidationIssue

# role -> rule. Each rule returns a human-readable problem string or None
# (plausible). Rules are opt-in: an unknown role with no rule always passes —
# never fail by default.
Rule = Callable[[ColumnProfile], str | None]


def _bounded_0_100(col: ColumnProfile) -> bool:
    return (
        isinstance(col.min_value, (int, float))
        and isinstance(col.max_value, (int, float))
        and col.min_value >= 0
        and col.max_value <= 100
    )


def _negative_min(col: ColumnProfile) -> bool:
    return isinstance(col.min_value, (int, float)) and col.min_value < 0


def _max_gt(col: ColumnProfile, threshold: float) -> bool:
    return isinstance(col.max_value, (int, float)) and col.max_value > threshold


def _currency_rule(col: ColumnProfile) -> str | None:
    if _bounded_0_100(col):
        return (
            "values are bounded 0-100, which is characteristic of a score or percentage, "
            "not a monetary amount"
        )
    if _negative_min(col):
        return "monetary amounts should not be negative"
    return None


def _score_rule(col: ColumnProfile) -> str | None:
    if _max_gt(col, 1000):
        return "score values fall outside a plausible 0-100 range"
    return None


def _date_rule(col: ColumnProfile) -> str | None:
    if col.cardinality < 2:
        return "fewer than 2 distinct values — time-series metrics will be meaningless on one date"
    return None


def _rate_rule(col: ColumnProfile) -> str | None:
    if _negative_min(col):
        return "rates/percentages should not be negative"
    if _max_gt(col, 100):
        return "rates/percentages should not exceed 100"
    return None


def _identifier_rule(col: ColumnProfile) -> str | None:
    if col.distinct_ratio < 0.01:
        return (
            f"distinct_ratio is {col.distinct_ratio:.3f} — an identifier should be nearly unique, "
            "this looks like a repeated category value"
        )
    return None


def rule_for_role(role: str) -> Rule | None:
    """The rule for a canonical role, or None (no rule = always plausible).

    Pattern order matters: `score` is checked before the currency patterns so
    a role literally called `score` is never mistaken for money.
    """
    lower = role.lower()

    if lower == "score" or lower.endswith("_score"):
        return _score_rule

    if "transaction_date" == lower or lower.endswith("_date") or lower.endswith("_at"):
        return _date_rule

    if "revenue_amount" in lower or lower.endswith("_amount") or lower.startswith("price"):
        return _currency_rule

    if lower == "rate" or "percent" in lower or lower.endswith("_rate") or "ratio" in lower:
        return _rate_rule

    if lower.endswith("_ref") or lower.endswith("_id"):
        return _identifier_rule

    return None


def check_binding_plausibility(
    bindings: SchemaBindings, profile: SchemaProfile
) -> ValidationCheckResult:
    issues: list[ValidationIssue] = []
    # binding alias (e.g. "fact") -> physical_ref (e.g. srcdb."enrollments"),
    # then physical_ref -> source table name (e.g. "enrollments"), which is
    # what ColumnProfile.table holds.
    physical_by_alias = {t.alias: t.physical for t in bindings.tables}
    table_name_by_physical = {t.physical_ref: t.name for t in profile.source.tables}
    for binding in bindings.columns:
        rule = rule_for_role(binding.role)
        if rule is None:
            continue
        physical = physical_by_alias.get(binding.table_alias, binding.table_alias)
        physical_table = table_name_by_physical.get(physical) or binding.table_alias
        col = next(
            (
                c
                for c in profile.structural.columns
                if c.table == physical_table and c.name == binding.physical
            ),
            None,
        )
        if col is None:
            continue
        problem = rule(col)
        if problem is not None:
            issues.append(
                ValidationIssue(
                    severity="error",
                    location=f"binding:{binding.role}",
                    message=(
                        f"'{binding.physical}' is bound to '{binding.role}' but {problem}. "
                        f"Binding confidence was {binding.confidence:.2f}."
                    ),
                )
            )
    status = CheckStatus.FAIL if issues else CheckStatus.PASS
    return ValidationCheckResult(check="binding_plausibility", status=status, issues=issues)


__all__ = ["check_binding_plausibility", "rule_for_role"]