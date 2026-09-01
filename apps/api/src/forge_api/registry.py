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
    # Set only when this run's data was loaded into the client warehouse
    # (forge_core.ingestion.warehouse). Intentionally in-memory only, never
    # part of `record` - it must never reach the `runs` table, a log line, or
    # a persisted JSON file. Lost on API restart; that's the point (see
    # /runs/{run_id}/warehouse-credentials - "show once").
    warehouse_connection_string: str | None = None
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


def all_contexts() -> list[RunContext]:
    return list(_RUNS.values())
