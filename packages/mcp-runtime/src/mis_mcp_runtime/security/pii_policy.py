"""Denied-column enforcement — the runtime-side half of the guardrail the
binding resolver computes (see forge_core.binding.resolver). `SELECT *` is
already rejected by sql_policy, so every projected column here is explicit
and checkable by name.
"""

from __future__ import annotations

from sqlglot import exp


class PiiPolicyError(ValueError):
    pass


def check_no_denied_columns(statement: exp.Expression, denied_columns: list[str]) -> None:
    denied_lower = {c.lower() for c in denied_columns}
    for select in statement.find_all(exp.Select):
        for projection in select.expressions:
            column_name = (projection.alias_or_name or "").lower()
            if column_name in denied_lower:
                raise PiiPolicyError(
                    f"Column {column_name!r} is denied by this plugin's guardrails "
                    "and cannot be selected."
                )
            for col_ref in projection.find_all(exp.Column):
                if col_ref.name.lower() in denied_lower:
                    raise PiiPolicyError(
                        f"Column {col_ref.name!r} is denied by this plugin's guardrails "
                        "and cannot be referenced."
                    )
