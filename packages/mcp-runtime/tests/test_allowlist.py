from __future__ import annotations

import sqlglot
import pytest

from mis_mcp_runtime.security.allowlist import AllowlistError, check_tables_allowed


def _parse(sql: str):
    return sqlglot.parse_one(sql, read="duckdb")


def test_allowed_table_passes():
    check_tables_allowed(_parse('SELECT * FROM "src_orders"'), ["src_orders"])


def test_disallowed_table_is_rejected():
    with pytest.raises(AllowlistError):
        check_tables_allowed(_parse('SELECT * FROM "secret_table"'), ["src_orders"])


def test_aliased_table_is_matched_by_bare_name_not_the_alias():
    """Regression: check_tables_allowed used to compare each exp.Table's full
    rendered SQL (which includes "AS alias") against allowed_tables, so any
    aliased reference - the normal shape for a JOIN - was rejected even when
    the underlying table was allowed. Caught live via P2-01's first
    multi-table query against a real generated plugin."""
    check_tables_allowed(_parse('SELECT * FROM "src_orders" AS o'), ["src_orders"])


def test_aliased_join_across_two_allowed_tables_passes():
    stmt = _parse(
        'SELECT o."order_id", oi."quantity" FROM "src_orders" AS o '
        'JOIN "src_order_items" AS oi ON o."order_id" = oi."order_id"'
    )
    check_tables_allowed(stmt, ["src_orders", "src_order_items"])


def test_aliased_join_to_a_disallowed_table_is_still_rejected():
    stmt = _parse(
        'SELECT o."order_id" FROM "src_orders" AS o JOIN "secret_table" AS s ON o."order_id" = s."order_id"'
    )
    with pytest.raises(AllowlistError):
        check_tables_allowed(stmt, ["src_orders"])


def test_schema_qualified_allowed_tables_match_the_bare_table_reference():
    """Regression: schema_bindings.json's allowed_tables are schema-qualified
    (srcdb."enrollments"), but a rendered metric query references the bare
    aliased table name - the mismatch rejected every real P2-07 multi-table
    metric query even though the underlying table was genuinely allowed."""
    stmt = _parse(
        'SELECT SUM(c."price_inr") AS value FROM "enrollments" AS "enrollments" '
        'JOIN "courses" AS "courses" ON "enrollments"."course_id" = "courses"."course_id"'
    )
    check_tables_allowed(stmt, ['srcdb."enrollments"', 'srcdb."courses"', 'srcdb."students"'])
