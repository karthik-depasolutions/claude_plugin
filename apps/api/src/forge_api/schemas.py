"""Request/response contracts for the HTTP API. Deliberately thin wrappers
around `forge_core.models.run.RunRecord` — the API never invents its own
notion of run state."""

from __future__ import annotations

from forge_core.models.run import RunRecord
from pydantic import BaseModel, Field


class CreateRunFromPathRequest(BaseModel):
    source_path: str = Field(
        ...,
        description=(
            "A path readable by the API process (dev/CLI-parity use case), or a "
            "postgresql:// connection string for a live database. Never echoed back "
            "or persisted verbatim if it's a connection string - see "
            "forge_core.ingestion.registry.prepare_source_for_persistence."
        ),
    )
    industry: str | None = Field(None, description="Force a pack slug, skipping auto-classification.")
    use_llm: bool = Field(True, description="Use Gemini for semantic profiling, generation, and critique.")


class RunSummary(BaseModel):
    run_id: str
    status: str
    current_stage: str | None
    error: str | None


class RunDetail(RunRecord):
    """`RunRecord` plus anything the API layer tracks that the core model
    doesn't (currently nothing extra — kept as a distinct type so API
    consumers have a stable response contract even if that changes)."""


class ConfirmIndustryRequest(BaseModel):
    industry: str = Field(..., description="Pack slug chosen from the classify stage's ranked_matches.")


class BindingOverridesRequest(BaseModel):
    overrides: dict[str, str] = Field(
        ..., description="canonical_role -> physical 'table.column', forcing the deterministic guess."
    )


class PackSummary(BaseModel):
    slug: str
    name: str
    version: str
    description: str
    kpi_count: int
