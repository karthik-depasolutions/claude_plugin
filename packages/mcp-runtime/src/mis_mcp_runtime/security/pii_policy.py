"""Denied-column enforcement — the runtime-side half of the guardrail the
binding resolver computes (see forge_core.binding.resolver). `SELECT *` is
already rejected by sql_policy, so every projected column here is explicit
and checkable by name.

Every clause is covered, not just projections: a denied column in a WHERE
predicate leaks its values by inference (binary search over a LIKE
reconstructs rows) just as surely as projecting it, and GROUP BY/HAVING/
ORDER BY leak the same column's distribution directly. The walk covers the
whole statement tree so a denied column is unreachable in any position.
"""

from __future__ import annotations

from sqlglot import exp


class PiiPolicyError(ValueError):
    pass


def check_no_denied_columns(statement: exp.Expression, denied_columns: list[str]) -> None:
    denied_lower = {c.lower() for c in denied_columns}
    # Walk EVERY column reference in the statement, not just projections —
    # WHERE, GROUP BY, HAVING, ORDER BY, and nested subqueries included.
    for col_ref in statement.find_all(exp.Column):
        if col_ref.name.lower() in denied_lower:
            raise PiiPolicyError(
                f"Column {col_ref.name!r} is denied by this plugin's guardrails "
                "and cannot be referenced — including in WHERE, GROUP BY, HAVING, or ORDER BY."
            )
    # Aliases masking a denied name: `SELECT "customer_name" AS phone ...`
    for select in statement.find_all(exp.Select):
        for projection in select.expressions:
            alias = (projection.alias_or_name or "").lower()
            if alias in denied_lower:
                raise PiiPolicyError(
                    f"Alias {projection.alias_or_name!r} collides with a denied column name."
                )