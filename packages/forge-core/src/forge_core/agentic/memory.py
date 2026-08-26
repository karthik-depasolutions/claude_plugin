"""Two independent kinds of memory for the binding agent, both file-backed
SQLite so they survive process restarts (unlike the agent itself, which is
otherwise stateless — see `binding_agent.py`):

1. Decision cache (`get_exact_decision`/`recent_examples`/`record_decision`)
   — a small, structured table of past (pack, role) -> column decisions.
   Never reused blindly across *different* customers (a column name is
   customer-specific) - only ever used two safe ways: as an exact-match fast
   path when the fact table's shape hasn't changed at all (e.g. re-running
   or resuming the same upload), and as few-shot reference examples ("here's
   how this concept was resolved for other schemas") to help the agent
   reason faster and more consistently, never as a literal answer it can
   skip validating.

2. Reasoning-trace checkpointer (`trace_checkpointer`) — LangGraph's own
   SQLite-backed state persistence, storing every message and tool call for
   one invocation under a unique thread_id, purely for audit/debugging.
   Never read back by the pipeline itself.

Both live under `generated/agent_memory/` by default - override with
`FORGE_AGENT_MEMORY_DIR` (e.g. to keep it out of a read-only deployment).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from forge_core.models.schema_profile import ColumnProfile

EXAMPLES_PER_ROLE = 3


def _memory_dir() -> Path:
    path = Path(os.environ.get("FORGE_AGENT_MEMORY_DIR", "generated/agent_memory"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _decisions_db_path() -> Path:
    return _memory_dir() / "binding_decisions.sqlite"


def _traces_db_path() -> Path:
    return _memory_dir() / "reasoning_traces.sqlite"


def value_shape_of(col: ColumnProfile) -> str:
    """A coarse, non-identifying description of a column's *values* (e.g.
    "numeric, 4-figure, no negatives"), derived deterministically from its
    profile. Used for few-shot prompts instead of the verbatim column name -
    a same-tenant example should still never hand a raw column name to the
    LLM prompt, because the prompt (and its traces) outlive the schema."""
    role = col.guessed_role.value
    if isinstance(col.min_value, (int, float)) and isinstance(col.max_value, (int, float)):
        try:
            width = len(str(abs(int(col.max_value))))
        except (ValueError, OverflowError):
            width = 0
        sign = "no negatives" if col.min_value >= 0 else "can be negative"
        return f"{role}, {width}-figure, {sign}"
    return role


def schema_fingerprint(table_cols: list[ColumnProfile], extra: str = "") -> str:
    """A stable identity for "this exact fact table shape" - same column
    names and dtypes, in a canonical (sorted) order so column order doesn't
    matter. Two different customers with coincidentally identical schemas
    would share a fingerprint, which is fine: an exact-match cache hit only
    ever returns a column name, which the caller re-validates against *that*
    customer's real columns before trusting it either way.

    `extra` folds caller-supplied context (e.g. data-review answers) into
    the fingerprint so a cached decision is invalidated when the user has
    told us something new about the data. The `if extra:` guard is what
    keeps every *existing* cache row valid: a call with no extra produces
    byte-identical fingerprints to before this parameter existed."""
    signature = "|".join(sorted(f"{c.name}:{c.dtype}" for c in table_cols))
    if extra:
        signature += f"||extra:{extra}"
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CachedDecision:
    column: str | None
    confidence: float
    reasoning: str
    schema_fingerprint: str
    value_shape: str = ""


def _ensure_schema(con: sqlite3.Connection) -> None:
    """Create (or upgrade) the `binding_decisions` table. Runs inside
    `_connect` on every open so an on-disk DB created by an older build
    transparently gains the tenant/value_shape columns (a column name on one
    customer's schema must never be reused on another's, and - since the
    migration is a plain ALTER TABLE - existing rows land in `_local`,
    i.e. treated as single-tenant history)."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS binding_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL DEFAULT '_local',
            pack_slug TEXT NOT NULL,
            role TEXT NOT NULL,
            schema_fingerprint TEXT NOT NULL,
            column_name TEXT,
            confidence REAL NOT NULL,
            reasoning TEXT NOT NULL,
            value_shape TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    cols = {row[1] for row in con.execute("PRAGMA table_info(binding_decisions)")}
    if "tenant_id" not in cols:
        con.execute("ALTER TABLE binding_decisions ADD COLUMN tenant_id TEXT NOT NULL DEFAULT '_local'")
    if "value_shape" not in cols:
        con.execute("ALTER TABLE binding_decisions ADD COLUMN value_shape TEXT NOT NULL DEFAULT ''")
    index = con.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_binding_decisions_lookup'"
    ).fetchone()
    if index is None or "tenant_id" not in (index[0] or ""):
        con.execute("DROP INDEX IF EXISTS idx_binding_decisions_lookup")
        con.execute(
            "CREATE INDEX idx_binding_decisions_lookup "
            "ON binding_decisions (tenant_id, pack_slug, role, schema_fingerprint)"
        )


@contextmanager
def _connect():
    con = sqlite3.connect(_decisions_db_path())
    try:
        _ensure_schema(con)
        yield con
        con.commit()
    finally:
        con.close()


def get_exact_decision(
    pack_slug: str, role: str, fingerprint: str, tenant_id: str
) -> CachedDecision | None:
    """A fast path, not a guess: only returns a result when this exact
    (tenant, pack, role, schema shape) combination was already resolved
    before - e.g. resuming a paused run or re-running the same upload. The
    caller still re-validates the column against the live column list."""
    with _connect() as con:
        row = con.execute(
            "SELECT column_name, confidence, reasoning, value_shape FROM binding_decisions "
            "WHERE tenant_id = ? AND pack_slug = ? AND role = ? AND schema_fingerprint = ? "
            "ORDER BY id DESC LIMIT 1",
            (tenant_id, pack_slug, role, fingerprint),
        ).fetchone()
    if row is None:
        return None
    return CachedDecision(
        column=row[0], confidence=row[1], reasoning=row[2], schema_fingerprint=fingerprint, value_shape=row[3] or ""
    )


def recent_examples(
    pack_slug: str,
    role: str,
    *,
    tenant_id: str,
    exclude_fingerprint: str,
    limit: int = EXAMPLES_PER_ROLE,
    allow_cross_tenant: bool = False,
) -> list[CachedDecision]:
    """Past *successful* decisions for this role on *this tenant's own*
    schemas - shown to the agent as reference examples ("here's how this
    concept tends to get resolved"), never as an answer to copy verbatim.
    Cross-tenant rows are never returned unless `allow_cross_tenant` is
    explicitly set True (nothing in the codebase does today)."""
    clauses = [
        "pack_slug = ?",
        "role = ?",
        "schema_fingerprint != ?",
        "column_name IS NOT NULL",
    ]
    params: list[object] = [pack_slug, role, exclude_fingerprint]
    if not allow_cross_tenant:
        clauses.append("tenant_id = ?")
        params.append(tenant_id)
    params.append(limit)
    with _connect() as con:
        rows = con.execute(
            "SELECT column_name, confidence, reasoning, schema_fingerprint, value_shape "
            f"FROM binding_decisions WHERE {' AND '.join(clauses)} "
            "ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
    return [
        CachedDecision(column=r[0], confidence=r[1], reasoning=r[2], schema_fingerprint=r[3], value_shape=r[4] or "")
        for r in rows
    ]


def record_decision(
    pack_slug: str,
    role: str,
    fingerprint: str,
    column: str | None,
    confidence: float,
    reasoning: str,
    tenant_id: str,
    value_shape: str = "",
) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO binding_decisions "
            "(tenant_id, pack_slug, role, schema_fingerprint, column_name, confidence, reasoning, "
            "value_shape, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tenant_id,
                pack_slug,
                role,
                fingerprint,
                column,
                confidence,
                reasoning,
                value_shape,
                datetime.now(UTC).isoformat(),
            ),
        )


_PG_CHECKPOINTER_SETUP_DONE = False


def _get_postgres_checkpointer_url() -> str | None:
    return (
        os.environ.get("FORGE_POSTGRES_CHECKPOINTER_URL")
        or os.environ.get("FORGE_CLIENT_WAREHOUSE_URL")
    )


@contextmanager
def trace_checkpointer():
    """A LangGraph checkpointer for full-trace audit and debugging.

    Uses `PostgresSaver` connected to PostgreSQL (e.g. Supabase) when
    `FORGE_POSTGRES_CHECKPOINTER_URL` or `FORGE_CLIENT_WAREHOUSE_URL` is set,
    and falls back cleanly to local SQLite (`SqliteSaver`) if running offline
    or in a lightweight environment.
    """
    global _PG_CHECKPOINTER_SETUP_DONE
    pg_url = _get_postgres_checkpointer_url()
    if pg_url:
        saver = None
        pg_cm = None
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            pg_cm = PostgresSaver.from_conn_string(pg_url)
            saver = pg_cm.__enter__()
            if not _PG_CHECKPOINTER_SETUP_DONE:
                saver.setup()
                _PG_CHECKPOINTER_SETUP_DONE = True
        except Exception:
            if pg_cm is not None:
                try:
                    pg_cm.__exit__(None, None, None)
                except Exception:
                    pass
            saver = None
            pg_cm = None

        if saver is not None:
            try:
                yield saver
            finally:
                pg_cm.__exit__(None, None, None)
            return

    from langgraph.checkpoint.sqlite import SqliteSaver

    with SqliteSaver.from_conn_string(str(_traces_db_path())) as saver:
        yield saver


def read_trace(thread_id: str) -> list[dict]:
    """Replays a past invocation's full message/tool-call history from the
    reasoning-trace checkpointer, most recent state last - for debugging a
    specific decision after the fact, e.g. in a notebook or `forge` shell."""
    with trace_checkpointer() as checkpointer:
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        state = checkpointer.get(config)
    if state is None:
        return []
    messages = state.get("channel_values", {}).get("messages", [])
    return [
        {"type": type(m).__name__, "content": getattr(m, "content", str(m))}
        for m in messages
    ]


__all__ = [
    "CachedDecision",
    "get_exact_decision",
    "read_trace",
    "record_decision",
    "recent_examples",
    "schema_fingerprint",
    "trace_checkpointer",
    "value_shape_of",
]
