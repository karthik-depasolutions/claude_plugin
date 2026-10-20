"""Process-local run registry. A run's `RunRecord` lives here while its
pipeline thread is active so the SSE endpoint can poll `record.events` in
real time; `pipeline_runner` mirrors every terminal/paused state to the
`runs` table so history survives a restart (see forge_api.db)."""

from __future__ import annotations

from dataclasses import dataclass, field

from forge_core.llm import TokenUsage
from forge_core.models.run import RunRecord


@dataclass
class RunContext:
    record: RunRecord
    binding_overrides: dict[str, str] = field(default_factory=dict)
    running: bool = False
    # Carried across a NEEDS_INPUT pause/resume (industry confirmation,
    # binding overrides) since it's a run-level choice, not something the
    # caller re-supplies each time it drives the state machine forward.
    use_agent: bool = False
    # Agent Schema mode: read information_schema, cap profiling budgets, chunk
    # synthesis, and ship a sharded/BM25-indexed config/catalog/ instead of one
    # monolithic schema_model.json. For 50-500 table databases. Run-level.
    agent_schema: bool = False
    # No-LLM fast test run: every LLM role is the deterministic FakeLLMProvider
    # (no API calls, no cost), the clarification pause is skipped, and (with
    # agent_schema) the fast profiling budget is used. Exercises the whole
    # pipeline end to end in minutes for $0 - not a production plugin.
    no_llm: bool = False
    # Opt-in: after a successful live-database run, also project the knowledge
    # pack into an `AGENTS.*` metadata schema *inside the customer's own source
    # database* (not just the Forge warehouse), so their other SQL agents can
    # discover the schema too. Best-effort - needs the source credential to hold
    # CREATE rights; a failure is logged and never fails the run. The generated
    # plugin is unaffected: it stays read-only and reads its bundled
    # config/catalog/. Warehouse-backed runs ignore this (they always project to
    # the Forge warehouse). Run-level.
    publish_agents_to_source: bool = False
    # Set only when this run's data was loaded into the client warehouse
    # (forge_core.ingestion.warehouse). Intentionally in-memory only, never
    # part of `record` - it must never reach the `runs` table, a log line, or
    # a persisted JSON file. Lost on API restart; that's the point (see
    # /runs/{run_id}/warehouse-credentials - "show once").
    warehouse_connection_string: str | None = None
    # The `client_<label>_<run_id>` schema name backing this run, when its data
    # was loaded into (upload) or cloned into (postgresql:// + clone) the
    # warehouse. Unlike `warehouse_connection_string` this one IS persisted
    # (to `RunORM.warehouse_schema`) - it's a bare identifier, not a
    # credential, and lifecycle tooling (DELETE /runs, warehouse_gc) needs it.
    warehouse_schema: str | None = None
    # Env-var -> value for this run's source credential(s), bound for just the
    # pipeline thread via `credentials.set_credentials`. In-memory only; keeps
    # a customer's postgresql:// URL off os.environ so concurrent runs against
    # different databases don't clobber each other.
    source_credentials: dict[str, str] = field(default_factory=dict)
    # One accumulator for the whole run - reused across every pause/resume of
    # `_execute` so a run that stops for clarification still counts all its
    # tokens. `token_usage` is its snapshot, refreshed each time the pipeline
    # thread exits and written to the `runs` table.
    # ponytail: in-memory only; a resume after an API restart (ctx gone) starts
    # a fresh count. Acceptable - the common path (no restart mid-run) is exact.
    token_usage_acc: TokenUsage = field(default_factory=TokenUsage)
    token_usage: dict | None = None


_RUNS: dict[str, RunContext] = {}


def put(run_id: str, ctx: RunContext) -> None:
    _RUNS[run_id] = ctx


def get(run_id: str) -> RunContext | None:
    return _RUNS.get(run_id)


def discard(run_id: str) -> None:
    """Forget a run's live context (used by DELETE /runs/{id}, so a deleted
    run stops being resolvable from memory)."""
    _RUNS.pop(run_id, None)


def all_contexts() -> list[RunContext]:
    return list(_RUNS.values())
