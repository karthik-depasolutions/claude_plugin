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
    # exp.Table.name is the bare table identifier; t.sql() renders the whole
    # table reference including "AS alias" when the query aliases it (e.g.
    # a JOIN), which never matched anything in allowed_tables and rejected
    # every aliased multi-table query outright - latent until P2-01 made a
    # second table reachable at all, since a single-table query never
    # needed an alias to begin with.
    referenced = {_normalize(t.name) for t in statement.find_all(exp.Table)}

    disallowed = referenced - allowed_normalized
    if disallowed:
        raise AllowlistError(
            f"Query references table(s) not in the allow-list: {sorted(disallowed)}. "
            f"Allowed: {sorted(allowed_normalized)}"
        )


def _normalize(table_ref: str) -> str:
    return table_ref.strip().lower().replace('"', "").replace("'", "")
