"""Renders a canonical KPI's `{{token}}` template against a customer's
SchemaBindings into a concrete SQL string. Pure string substitution plus
sqlglot parsing for a syntax safety net — no LLM involvement (see
compiler/kpi_compiler.py for how this fits into the compile pipeline).
"""

from __future__ import annotations

import re

from forge_core.models.bindings import SchemaBindings

_TOKEN_PATTERN = re.compile(r"\{\{(\w+)\}\}")


class UnresolvedTokenError(ValueError):
    def __init__(self, token: str) -> None:
        super().__init__(f"Template token {{{{{token}}}}} could not be resolved from bindings.")
        self.token = token


def _sql_string_list(values: list[str]) -> str:
    if not values:
        return "(NULL)"  # a condition that can never match, rather than invalid SQL
    escaped = [v.replace("'", "''") for v in values]
    return "(" + ", ".join(f"'{v}'" for v in escaped) + ")"


def render_sql(template: str, bindings: SchemaBindings) -> str:
    table_by_alias = {t.alias: t.physical for t in bindings.tables}
    # A binding may carry a SQL expression instead of a bare column - today
    # that is STRPTIME(col, fmt) for a date stored as non-ISO text, which
    # DuckDB's CAST would raise on rather than returning NULL.
    column_by_role = {
        c.role: (c.sql_expression or f'"{c.physical}"') for c in bindings.columns
    }
    value_set_by_name = {v.name: v.values for v in bindings.value_sets}

    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token in table_by_alias:
            return table_by_alias[token]
        if token in column_by_role:
            return column_by_role[token]
        if token in value_set_by_name:
            return _sql_string_list(value_set_by_name[token])
        raise UnresolvedTokenError(token)

    return _TOKEN_PATTERN.sub(_replace, template)


def required_tokens(template: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(template))
