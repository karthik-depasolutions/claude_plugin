"""Typed state schema for the LangGraph pipeline execution graph.

Carries all intermediate artifacts between stages with strong typing and Pydantic
schema validation. Bridges seamlessly to/from `RunRecord` so existing callers
(FastAPI backend, background threads, SSE streaming, CLI) remain 100% compatible.
"""

from __future__ import annotations

from typing import Any, TypedDict

from forge_core.generation import GeneratedPlugin
from forge_core.models.bindings import SchemaBindings
from forge_core.models.claims import ColumnClaim
from forge_core.models.common import RunStage, RunStatus
from forge_core.models.data_map import DataMap
from forge_core.models.datasource import DataSource
from forge_core.models.industry_pack import IndustryMatch, IndustryPack
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.metrics import MetricDefinition
from forge_core.models.quality import DataReview
from forge_core.models.run import RunRecord
from forge_core.models.schema_profile import SchemaProfile, StructuralProfile
from forge_core.models.validation import ValidationReport


class ForgeState(TypedDict, total=False):
    """The canonical state object passed between nodes in the Forge LangGraph workflow.
    
    All state modifications are explicit, immutable dictionary updates returned by nodes.
    """

    # --- Run metadata & identity ---
    run_id: str
    tenant_id: str
    status: RunStatus
    current_stage: RunStage | None
    error: str | None

    # --- Ingestion & profiling ---
    source_path: str
    output_dir: str
    data_source: DataSource
    structural: StructuralProfile
    data_map: DataMap
    profile: SchemaProfile
    data_review: DataReview | None
    data_understanding: dict[str, Any] | None
    business_context: dict[str, Any] | None

    # --- Industry classification ---
    industry_override: str | None
    ranked_matches: list[IndustryMatch]
    suggested_industry: dict[str, Any] | None
    selected_pack: IndustryPack | None

    # --- Review answers & overrides ---
    data_answers: dict[str, str]
    binding_overrides: dict[str, str]
    binding_confirmations: dict[str, str]

    # --- Schema binding ---
    bindings: SchemaBindings
    binding_questions: list[dict[str, Any]]
    column_claims: list[ColumnClaim]
    denied_by_table: dict[str, list[str]]

    # --- KPI compilation & metrics ---
    kpi_defs: KpiDefsFile
    metric_defs: list[MetricDefinition]
    agent_proposed_metrics: list[str]

    # --- Generation & packaging ---
    generated_content: GeneratedPlugin | None
    plugin_dir: str | None

    # --- Validation ---
    validation_report: ValidationReport | None

    # --- Operational flags & providers ---
    use_llm: bool
    use_agent: bool
    label: str | None


def state_from_record(record: RunRecord, *, use_agent: bool = True) -> ForgeState:
    """Initialize a ForgeState from an existing RunRecord instance."""
    state: ForgeState = {
        "run_id": record.run_id,
        "tenant_id": getattr(record, "tenant_id", "_local"),
        "status": record.status,
        "current_stage": record.current_stage,
        "error": record.error,
        "source_path": record.source_path,
        "output_dir": record.output_dir,
        "industry_override": record.industry_override,
        "data_answers": dict(record.data_answers or {}),
        "binding_confirmations": dict(record.binding_confirmations or {}),
        "binding_overrides": {},
        "use_llm": True,
        "use_agent": use_agent,
        "label": record.label,
    }
    if record.data_understanding:
        state["data_understanding"] = record.data_understanding
    if record.business_context:
        state["business_context"] = record.business_context
    return state


def sync_state_to_record(state: ForgeState, record: RunRecord) -> None:
    """Sync values from the graph state back into the live RunRecord."""
    if "status" in state:
        record.status = state["status"]
    if "current_stage" in state and state["current_stage"]:
        record.current_stage = state["current_stage"]
    if "error" in state:
        record.error = state["error"]
    if "industry_override" in state:
        record.industry_override = state["industry_override"]
    if "data_answers" in state:
        record.data_answers = state["data_answers"]
    if "binding_confirmations" in state:
        record.binding_confirmations = state["binding_confirmations"]
    if "data_understanding" in state and state["data_understanding"]:
        record.data_understanding = state["data_understanding"]
    if "business_context" in state and state["business_context"]:
        record.business_context = state["business_context"]


__all__ = ["ForgeState", "state_from_record", "sync_state_to_record"]
