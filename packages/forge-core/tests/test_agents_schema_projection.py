"""build_agents_schema_sql - the pure SQL projection of a knowledge pack into
the dbt Agents Schema (AGENTS.*) shape. The live publish needs a warehouse and
is covered by ops tooling, not here."""

from __future__ import annotations

import json
import re

from forge_core.publishing.agents_schema import build_agents_schema_sql

# Columns declared JSONB in _DDL - their INSERT value must parse as JSON or
# Postgres rejects the row (regression: OSI_FIELD.ai_context was a bare string).
_JSONB_COLS = {
    "OSI_DATASET": {"primary_key", "synonyms", "ai_context"},
    "OSI_FIELD": {"synonyms", "ai_context", "custom_extensions"},
    "OSI_METRIC": {"expressions", "synonyms", "ai_context"},
    "OSI_RELATIONSHIP": {"from_columns", "to_columns", "ai_context"},
}
_INSERT = re.compile(r'INSERT INTO "AGENTS"\."(\w+)" \(([^)]+)\) VALUES \((.*)\)$', re.DOTALL)

# $<tag>$ ... $<tag>$  -  a Postgres dollar-quoted literal with a matching tag
_DOLLAR = re.compile(r"(\$[A-Za-z_][A-Za-z0-9_]*\$).*?\1", re.DOTALL)


def _literals(stmt: str) -> list[str]:
    """The bodies of every dollar-quoted literal in one statement."""
    return [m.group(0)[len(m.group(1)) : -len(m.group(1))] for m in _DOLLAR.finditer(stmt)]

_MODEL = {
    "overview": "A retail database.",
    "caveats": ["orders.total excludes tax"],
    "tables": [
        {
            "name": "orders",
            "purpose": "one row per order",
            "role": "fact",
            "grain_prose": "one row per order",
            "synonyms": ["sales", "purchases"],
            "columns": [
                {"name": "id", "meaning": "order id", "confidence": "high", "synonyms": []},
                {"name": "total", "meaning": "order total", "confidence": "medium",
                 "enum": None, "synonyms": ["amount"]},
            ],
        },
        {"name": "customers", "purpose": "one row per customer", "role": "dimension",
         "columns": [{"name": "id", "meaning": "customer id", "confidence": "high"}]},
    ],
    "relationships": [
        {"from_ref": "orders.customer_id", "to_ref": "customers.id",
         "strength": "strong", "cardinality": "N:1"}
    ],
    "cookbook": [{"question": "revenue?", "sql": "SELECT SUM(total) FROM orders"}],
}
_KPIS = {"kpis": [{"id": "revenue", "description": "total revenue", "sql": "SELECT SUM(total) FROM orders"}]}


def test_projection_covers_every_agents_table() -> None:
    stmts = build_agents_schema_sql(
        _MODEL, model_name="client_acme_r1", kpi_defs=_KPIS,
        skill_uses=[("skill/retail-analyst", "table", "client_acme_r1.orders")],
    )
    joined = "\n".join(stmts)

    assert 'CREATE SCHEMA IF NOT EXISTS "AGENTS"' in joined
    for tbl in ("ROOT", "OSI_DATASET", "OSI_FIELD", "OSI_METRIC", "OSI_RELATIONSHIP", "SKILL_USE"):
        assert f'CREATE TABLE IF NOT EXISTS "AGENTS"."{tbl}"' in joined

    # idempotency: this run's rows are deleted before re-insert
    del_root = next(s for s in stmts if s.startswith('DELETE FROM "AGENTS"."ROOT"'))
    assert _literals(del_root) == ["forge"]
    del_ds = next(s for s in stmts if s.startswith('DELETE FROM "AGENTS"."OSI_DATASET"'))
    assert _literals(del_ds) == ["client_acme_r1"]
    # OSI_FIELD has no model_name column (PK is dataset_name, name) - it must be
    # cleared via its parent datasets, not `WHERE model_name = ...`.
    del_field = next(s for s in stmts if s.startswith('DELETE FROM "AGENTS"."OSI_FIELD"'))
    assert "model_name" not in del_field.split("SELECT")[0]
    assert 'SELECT name FROM "AGENTS"."OSI_DATASET" WHERE model_name' in del_field
    assert stmts.index(del_field) < stmts.index(del_ds)  # child before parent

    # content
    assert '"AGENTS"."ROOT"' in joined and "A retail database." in joined
    assert "## Caveats" in joined and "orders.total excludes tax" in joined
    assert 'INSERT INTO "AGENTS"."OSI_DATASET"' in joined and "one row per order" in joined
    assert joined.count('INSERT INTO "AGENTS"."OSI_FIELD"') == 3  # 2 + 1 columns
    assert 'INSERT INTO "AGENTS"."OSI_RELATIONSHIP"' in joined and "orders" in joined
    assert 'INSERT INTO "AGENTS"."OSI_METRIC"' in joined and "ANSI_SQL" in joined
    assert 'INSERT INTO "AGENTS"."SKILL_USE"' in joined and "skill/retail-analyst" in joined
    assert "cookbook/000" in joined


def test_dollar_quoting_is_injection_proof_for_adversarial_values() -> None:
    # a value crafted to break a fixed-tag scheme: the exact old tag, the tag
    # minus its trailing '$' (the real escape), a terminator, and a comment.
    poison = "x'; DROP TABLE users; -- $fdoc$ $fdoc $$ end"
    model = {
        "overview": poison,
        "tables": [{"name": poison, "purpose": poison, "role": "fact",
                    "synonyms": [poison],
                    "columns": [{"name": poison, "meaning": poison, "synonyms": []}]}],
        "relationships": [], "cookbook": [{"question": poison, "sql": poison}],
    }
    for stmt in build_agents_schema_sql(model, model_name=poison):
        # strip every dollar-quoted literal; the poison must live ONLY inside one
        outside_literals = _DOLLAR.sub("''", stmt)
        assert "DROP TABLE" not in outside_literals
        assert "-- " not in outside_literals
        # each literal's own tag provably does not occur in its body
        for m in _DOLLAR.finditer(stmt):
            tag = m.group(1)
            body = m.group(0)[len(tag) : -len(tag)]
            assert tag not in body


def test_projection_is_empty_safe() -> None:
    stmts = build_agents_schema_sql(
        {"tables": [], "relationships": [], "cookbook": [], "overview": ""}, model_name="m"
    )
    # still emits DDL + the overview row + the deletes, never raises
    assert any("CREATE SCHEMA" in s for s in stmts)
    assert any('INSERT INTO "AGENTS"."ROOT"' in s for s in stmts)


def test_every_jsonb_column_value_is_valid_json() -> None:
    """Regression: OSI_FIELD.ai_context was fed the bare `meaning` string,
    which Postgres rejects for a JSONB column ('invalid input syntax for type
    json'). Guard the whole class - every JSONB-column INSERT value must parse.
    """
    stmts = build_agents_schema_sql(
        _MODEL, model_name="m", kpi_defs=_KPIS,
        skill_uses=[("skill/x", "table", "m.orders")],
    )
    checked = 0
    for stmt in stmts:
        m = _INSERT.match(stmt.strip())
        if not m:
            continue
        table, cols_str, _ = m.groups()
        jsonb = _JSONB_COLS.get(table, set())
        if not jsonb:
            continue
        cols = [c.strip() for c in cols_str.split(",")]
        non_null_cols = [c for c in cols if c != "is_time_dimension"]  # the only NULL value
        vals = _literals(stmt)
        assert len(non_null_cols) == len(vals), (table, non_null_cols, vals)
        for col, val in zip(non_null_cols, vals):
            if col in jsonb:
                json.loads(val)  # raises on the bug
                checked += 1
    assert checked >= 6  # OSI_DATASET + OSI_FIELD (x2 cols) + RELATIONSHIP + METRIC
