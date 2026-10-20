"""Project a run's knowledge pack into the open **dbt Agents Schema** shape
(`AGENTS.*` tables) inside the Forge data warehouse.

This is the interoperability surface: once these rows exist, *any* SQL-capable
agent - not just the plugin Forge generated - can discover the customer's
tables, columns, relationships and metrics by querying `AGENTS.*`, exactly as
the dbt-labs/agents_schema spec intends.

Only ever written to the **Forge-owned** warehouse (Forge has read-only
access to a customer's own database and can never write there). Enabled only
for an `agent_schema` run whose data is warehouse-backed (upload-to-warehouse
or `clone: true`).

The `provider` for every row Forge writes is `"forge"`; a re-publish deletes
and rewrites exactly those rows, leaving other providers' rows untouched -
matching the spec's "each source owns its rows" rule.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

import duckdb

_PROVIDER = "forge"

_DDL = [
    'CREATE SCHEMA IF NOT EXISTS "AGENTS"',
    """CREATE TABLE IF NOT EXISTS "AGENTS"."ROOT" (
        provider VARCHAR NOT NULL, key VARCHAR NOT NULL, content TEXT NOT NULL,
        PRIMARY KEY (provider, key))""",
    """CREATE TABLE IF NOT EXISTS "AGENTS"."OSI_DATASET" (
        model_name VARCHAR NOT NULL, name VARCHAR NOT NULL, source VARCHAR NOT NULL,
        primary_key JSONB, description TEXT, synonyms JSONB, ai_context JSONB,
        PRIMARY KEY (model_name, name))""",
    """CREATE TABLE IF NOT EXISTS "AGENTS"."OSI_FIELD" (
        dataset_name VARCHAR NOT NULL, name VARCHAR NOT NULL, label VARCHAR,
        description TEXT, is_time_dimension BOOLEAN, synonyms JSONB, ai_context JSONB,
        custom_extensions JSONB, PRIMARY KEY (dataset_name, name))""",
    """CREATE TABLE IF NOT EXISTS "AGENTS"."OSI_METRIC" (
        model_name VARCHAR NOT NULL, name VARCHAR NOT NULL, description TEXT,
        expressions JSONB, synonyms JSONB, ai_context JSONB,
        PRIMARY KEY (model_name, name))""",
    """CREATE TABLE IF NOT EXISTS "AGENTS"."OSI_RELATIONSHIP" (
        model_name VARCHAR NOT NULL, name VARCHAR NOT NULL,
        from_dataset VARCHAR NOT NULL, to_dataset VARCHAR NOT NULL,
        from_columns JSONB NOT NULL, to_columns JSONB NOT NULL, ai_context JSONB,
        PRIMARY KEY (model_name, name))""",
    """CREATE TABLE IF NOT EXISTS "AGENTS"."SKILL_USE" (
        provider VARCHAR NOT NULL, skill_key VARCHAR NOT NULL, use_kind VARCHAR NOT NULL,
        object_ref VARCHAR NOT NULL, PRIMARY KEY (provider, skill_key, use_kind, object_ref))""",
]

# OSI tables keyed directly by model_name. OSI_FIELD is NOT one of them - it
# hangs off OSI_DATASET (PK dataset_name, name) and has no model_name column,
# so it's cleared via a subquery against its parent datasets, before them.
_MODEL_SCOPED_OSI_TABLES = ["OSI_DATASET", "OSI_METRIC", "OSI_RELATIONSHIP"]


def _q(value: Any) -> str:
    """A Postgres dollar-quoted string literal for an arbitrary string/JSON
    value. `postgres_execute` takes one SQL string with no bind-parameter API,
    so we compose - safely: the dollar tag is random per call and re-rolled
    until it provably does not occur in `value`, so the value is inert (a
    dollar-quoted body has no escape sequences; only the exact matching tag
    can close it). Customer-controlled table/column/enum names pass through
    here, so this must hold for adversarial input."""
    if not isinstance(value, str):
        value = json.dumps(value, default=str)
    tag = f"$f{secrets.token_hex(8)}$"
    while tag in value:
        tag = f"$f{secrets.token_hex(8)}$"
    return f"{tag}{value}{tag}"


def build_agents_schema_sql(
    schema_model: dict[str, Any],
    *,
    model_name: str,
    kpi_defs: dict[str, Any] | None = None,
    skill_uses: list[tuple[str, str, str]] | None = None,
) -> list[str]:
    """Every statement needed to (re)publish this run's `forge` rows. Pure -
    no connection, fully unit-testable. `model_name` is the warehouse schema
    the customer's data lives in (also the OSI model name).

    `skill_uses` is a list of (skill_key, use_kind, object_ref) - use_kind is
    'schema' or 'table'.
    """
    stmts = list(_DDL)

    # Idempotency: drop this provider's / this model's rows, then re-insert.
    stmts.append(f'DELETE FROM "AGENTS"."ROOT" WHERE provider = {_q(_PROVIDER)}')
    stmts.append(
        f'DELETE FROM "AGENTS"."OSI_FIELD" WHERE dataset_name IN '
        f'(SELECT name FROM "AGENTS"."OSI_DATASET" WHERE model_name = {_q(model_name)})'
    )
    for t in _MODEL_SCOPED_OSI_TABLES:
        stmts.append(f'DELETE FROM "AGENTS"."{t}" WHERE model_name = {_q(model_name)}')
    stmts.append(f'DELETE FROM "AGENTS"."SKILL_USE" WHERE provider = {_q(_PROVIDER)}')

    # ROOT: overview + caveats + cookbook
    overview = str(schema_model.get("overview") or "")
    caveats = schema_model.get("caveats") or []
    if caveats:
        overview += "\n\n## Caveats\n" + "\n".join(f"- {c}" for c in caveats)
    overview += (
        "\n\n_Published by MIS Plugin Forge. `SKILL_USE` rows are advisory; actual table "
        "access is enforced by the generated plugin's allow-list._"
    )
    stmts.append(
        f'INSERT INTO "AGENTS"."ROOT" (provider, key, content) '
        f"VALUES ({_q(_PROVIDER)}, {_q('overview')}, {_q(overview)})"
    )
    for i, entry in enumerate(schema_model.get("cookbook") or []):
        body = f"-- {entry.get('question', '')}\n{entry.get('sql', '')}"
        stmts.append(
            f'INSERT INTO "AGENTS"."ROOT" (provider, key, content) '
            f"VALUES ({_q(_PROVIDER)}, {_q(f'cookbook/{i:03d}')}, {_q(body)})"
        )

    # OSI_DATASET + OSI_FIELD
    for tdoc in schema_model.get("tables") or []:
        tname = tdoc.get("name", "")
        ai = {
            "role": tdoc.get("role"),
            "grain": tdoc.get("grain_prose"),
        }
        stmts.append(
            f'INSERT INTO "AGENTS"."OSI_DATASET" '
            f"(model_name, name, source, description, synonyms, ai_context) VALUES ("
            f"{_q(model_name)}, {_q(tname)}, {_q(f'{model_name}.{tname}')}, "
            f"{_q(tdoc.get('purpose', ''))}, {_q(tdoc.get('synonyms') or [])}, {_q(ai)})"
        )
        for col in tdoc.get("columns") or []:
            ext = {"confidence": col.get("confidence"), "enum": col.get("enum")}
            # ai_context is a JSONB column - it must be a JSON value, not the
            # bare `meaning` string (which Postgres rejects as invalid JSON).
            field_ai = {"meaning": col.get("meaning", "")}
            is_time = None  # dtype not carried on ColumnDoc; left null
            stmts.append(
                f'INSERT INTO "AGENTS"."OSI_FIELD" '
                f"(dataset_name, name, description, is_time_dimension, synonyms, "
                f"ai_context, custom_extensions) VALUES ("
                f"{_q(tname)}, {_q(col.get('name', ''))}, {_q(col.get('meaning', ''))}, "
                f"{'NULL' if is_time is None else str(is_time).upper()}, "
                f"{_q(col.get('synonyms') or [])}, {_q(field_ai)}, {_q(ext)})"
            )

    # OSI_RELATIONSHIP
    for i, rel in enumerate(schema_model.get("relationships") or []):
        frm = str(rel.get("from_ref", "")).split(".", 1)
        to = str(rel.get("to_ref", "")).split(".", 1)
        if len(frm) != 2 or len(to) != 2:
            continue
        ai = {"strength": rel.get("strength"), "cardinality": rel.get("cardinality")}
        stmts.append(
            f'INSERT INTO "AGENTS"."OSI_RELATIONSHIP" '
            f"(model_name, name, from_dataset, to_dataset, from_columns, to_columns, "
            f"ai_context) VALUES ("
            f"{_q(model_name)}, {_q(f'rel_{i:03d}')}, {_q(frm[0])}, {_q(to[0])}, "
            f"{_q([frm[1]])}, {_q([to[1]])}, {_q(ai)})"
        )

    # OSI_METRIC (compiled KPIs)
    for kpi in (kpi_defs or {}).get("kpis") or []:
        expr = [{"dialect": "ANSI_SQL", "expression": kpi.get("sql", "")}]
        stmts.append(
            f'INSERT INTO "AGENTS"."OSI_METRIC" '
            f"(model_name, name, description, expressions) VALUES ("
            f"{_q(model_name)}, {_q(kpi.get('id', ''))}, {_q(kpi.get('description', ''))}, "
            f"{_q(expr)})"
        )

    # SKILL_USE
    for skill_key, use_kind, object_ref in skill_uses or []:
        stmts.append(
            f'INSERT INTO "AGENTS"."SKILL_USE" (provider, skill_key, use_kind, object_ref) '
            f"VALUES ({_q(_PROVIDER)}, {_q(skill_key)}, {_q(use_kind)}, {_q(object_ref)})"
        )

    return stmts


def publish_agents_schema(
    admin_connection_string: str,
    *,
    model_name: str,
    schema_model: dict[str, Any],
    kpi_defs: dict[str, Any] | None = None,
    skill_uses: list[tuple[str, str, str]] | None = None,
    catalog: str = "whadmin",
) -> int:
    """Attach the warehouse admin URL and run every statement from
    `build_agents_schema_sql` via `postgres_execute`. Returns the statement
    count. Raises on a connection / SQL error - the caller decides whether that
    is fatal (it should be non-fatal: the file catalog is the source of truth)."""
    stmts = build_agents_schema_sql(
        schema_model, model_name=model_name, kpi_defs=kpi_defs, skill_uses=skill_uses
    )
    con = duckdb.connect(":memory:")
    try:
        con.execute("INSTALL postgres; LOAD postgres;")
        con.execute(f"ATTACH '{admin_connection_string}' AS {catalog} (TYPE POSTGRES)")
        for s in stmts:
            con.execute(f"CALL postgres_execute('{catalog}', {_q(s)})")
    finally:
        con.close()
    return len(stmts)


__all__ = ["build_agents_schema_sql", "publish_agents_schema"]
