"""Table allow-list enforcement — a parsed query may only reference tables
named in `schema_bindings.json`'s `allowed_tables`. Fails closed: an empty
allow-list (which `config.py` already refuses to start with) would reject
everything.
"""

from __future__ import annotations

from sqlglot import exp


class AllowlistError(ValueError):
    pass


def check_tables_allowed(statement: exp.Expression, allowed_tables: list[str]) -> None:
    allowed_normalized = {_normalize(t) for t in allowed_tables}
    referenced = {_normalize(t.sql(dialect="duckdb")) for t in statement.find_all(exp.Table)}

    disallowed = referenced - allowed_normalized
    if disallowed:
        raise AllowlistError(
            f"Query references table(s) not in the allow-list: {sorted(disallowed)}. "
            f"Allowed: {sorted(allowed_normalized)}"
        )


def _normalize(table_ref: str) -> str:
    return table_ref.strip().lower().replace('"', "").replace("'", "")
