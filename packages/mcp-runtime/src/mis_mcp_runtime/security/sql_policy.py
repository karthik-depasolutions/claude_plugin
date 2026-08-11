"""SQL safety gate for `run_safe_query` — the one tool that accepts
arbitrary customer-facing SQL text. Every other tool executes only
pre-compiled, pre-validated SQL from kpi_defs.json and never goes through
here. Parses with sqlglot; never regex-matches keywords, which is trivially
bypassable.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

ALLOWED_ROOT_TYPES = (exp.Select, exp.With)
FORBIDDEN_EXPRESSION_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Attach,
    exp.Command,
    exp.Pragma,
    exp.Merge,
    exp.TruncateTable,
    exp.Copy,
)


class SqlPolicyError(ValueError):
    pass


def parse_single_select(sql: str) -> exp.Expression:
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise SqlPolicyError(f"Could not parse SQL: {exc}") from exc

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlPolicyError("Exactly one SQL statement is allowed per call.")

    statement = statements[0]
    if not isinstance(statement, ALLOWED_ROOT_TYPES):
        raise SqlPolicyError("Only SELECT (or WITH ... SELECT) statements are allowed.")

    for forbidden_type in FORBIDDEN_EXPRESSION_TYPES:
        if list(statement.find_all(forbidden_type)):
            raise SqlPolicyError(f"Statement contains a forbidden operation: {forbidden_type.__name__}")

    for select in statement.find_all(exp.Select):
        if any(isinstance(e, exp.Star) for e in select.expressions):
            raise SqlPolicyError(
                "SELECT * is not allowed — list explicit columns so denied columns can be checked."
            )

    return statement
