"""RunRecord — the orchestrator's state machine contract. Used identically by
the CLI (in-process) and the API (persisted to the jobs table), so a run
looks the same whether started from `forge run` or a POST to /runs.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from forge_core.models.common import RunStage, RunStatus
from forge_core.models.quality import DataReview
from forge_core.models.schema_model import SchemaModel


class StageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: RunStage
    message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = Field(default_factory=dict)


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: RunStatus = RunStatus.PENDING
    current_stage: RunStage | None = None
    source_path: str
    industry_override: str | None = None
    output_dir: str
    label: str | None = None
    """Optional human-chosen project/business name (e.g. "Sparda Music
    Academy") - feeds the packaged plugin's displayName, its on-disk/repo
    name, and (for a warehouse-backed upload) the Postgres schema name.
    Purely cosmetic; every uniqueness guarantee still comes from run_id."""
    data_review: DataReview | None = None
    """Computed once during PROFILE and reused on every resume - regenerating
    it would drift finding/question ids and orphan answers keyed to them
    (resume re-runs the whole pipeline from ingest, see orchestrator.py).
    None = not computed yet. Set = never recomputed, even on replay."""
    schema_model: SchemaModel | None = None
    """The LLM-synthesized knowledge pack (overview, per-table docs, pattern
    notes, verified cookbook). Computed once during PROFILE, reused on resume.
    Shipped as config/schema_model.json and served by the MCP runtime."""
    data_answers: dict[str, str] | None = None
    """question id -> answer. None = never asked (or asked and not yet
    resumed). {} = asked, caller supplied no answers (or opted out) - this
    distinction, not just truthiness, is what stops the pause from re-firing
    on resume, mirroring how `industry_override` already works above."""
    events: list[StageEvent] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Not a field: never persisted/serialized. Lets a synchronous caller (the
    # CLI) render progress live as `run_pipeline` executes, instead of only
    # after it returns. The API doesn't need this - it already gets
    # real-time progress by polling `events` from a background thread.
    _listener: Callable[[StageEvent], None] | None = PrivateAttr(default=None)

    def on_event(self, listener: Callable[[StageEvent], None] | None) -> None:
        self._listener = listener

    def log(self, stage: RunStage, message: str, **data: Any) -> StageEvent:
        event = StageEvent(stage=stage, message=message, data=data)
        self.events.append(event)
        self.current_stage = stage
        self.updated_at = datetime.now(UTC)
        if self._listener is not None:
            self._listener(event)
        return event
