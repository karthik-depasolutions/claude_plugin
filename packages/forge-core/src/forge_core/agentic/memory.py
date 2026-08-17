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


def schema_fingerprint(table_cols: list[ColumnProfile]) -> str:
    """A stable identity for "this exact fact table shape" - same column
    names and dtypes, in a canonical (sorted) order so column order doesn't
    matter. Two different customers with coincidentally identical schemas
    would share a fingerprint, which is fine: an exact-match cache hit only
    ever returns a column name, which the caller re-validates against *that*
    customer's real columns before trusting it either way."""
    signature = "|".join(sorted(f"{c.name}:{c.dtype}" for c in table_cols))
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CachedDecision:
    column: str | None
    confidence: float
    reasoning: str
    schema_fingerprint: str


@contextmanager
def _connect():
    con = sqlite3.connect(_decisions_db_path())
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS binding_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pack_slug TEXT NOT NULL,
                role TEXT NOT NULL,
                schema_fingerprint TEXT NOT NULL,
                column_name TEXT,
                confidence REAL NOT NULL,
                reasoning TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_binding_decisions_lookup "
            "ON binding_decisions (pack_slug, role, schema_fingerprint)"
        )
        yield con
        con.commit()
    finally:
        con.close()


def get_exact_decision(pack_slug: str, role: str, fingerprint: str) -> CachedDecision | None:
    """A fast path, not a guess: only returns a result when this exact
    (pack, role, schema shape) combination was already resolved before -
    e.g. resuming a paused run or re-running the same upload. The caller
    still re-validates the column against the live column list."""
    with _connect() as con:
        row = con.execute(
            "SELECT column_name, confidence, reasoning FROM binding_decisions "
            "WHERE pack_slug = ? AND role = ? AND schema_fingerprint = ? "
            "ORDER BY id DESC LIMIT 1",
            (pack_slug, role, fingerprint),
        ).fetchone()
    if row is None:
        return None
    return CachedDecision(column=row[0], confidence=row[1], reasoning=row[2], schema_fingerprint=fingerprint)


def recent_examples(
    pack_slug: str, role: str, *, exclude_fingerprint: str, limit: int = EXAMPLES_PER_ROLE
) -> list[CachedDecision]:
    """Past *successful* decisions for this role on other customers' schemas
    - shown to the agent as reference examples ("here's how this concept
    tends to get resolved"), never as an answer to copy verbatim."""
    with _connect() as con:
        rows = con.execute(
            "SELECT column_name, confidence, reasoning, schema_fingerprint FROM binding_decisions "
            "WHERE pack_slug = ? AND role = ? AND schema_fingerprint != ? AND column_name IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (pack_slug, role, exclude_fingerprint, limit),
        ).fetchall()
    return [CachedDecision(column=r[0], confidence=r[1], reasoning=r[2], schema_fingerprint=r[3]) for r in rows]


def record_decision(
    pack_slug: str, role: str, fingerprint: str, column: str | None, confidence: float, reasoning: str
) -> None:
    with _connect() as con:
        con.execute(
            "INSERT INTO binding_decisions "
            "(pack_slug, role, schema_fingerprint, column_name, confidence, reasoning, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pack_slug, role, fingerprint, column, confidence, reasoning, datetime.now(UTC).isoformat()),
        )


@contextmanager
def trace_checkpointer():
    """A LangGraph SQLite checkpointer, for full-trace audit/debugging only
    - every message and tool call the agent makes gets persisted under a
    thread_id the caller controls. Never read back by the pipeline itself;
    inspect it directly (e.g. `forge_core.agentic.memory.read_trace`) if you
    need to see why a past decision was made."""
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
]
