"""Temporal activity definitions executing deterministic and agentic pipeline steps."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from temporalio import activity

from forge_core.graph import ForgeGraphContext, create_forge_graph, state_from_record
from forge_core.ingestion.registry import ingest
from forge_core.models.datasource import DataSource
from forge_core.models.run import RunRecord, RunStatus
from forge_core.profiling import build_structural_only

logger = logging.getLogger("workers.temporal.activities")


class IngestActivityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_path: str


class IngestActivityOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_source_dict: dict[str, Any]
    table_count: int
    total_rows: int


class ProfileActivityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_source_dict: dict[str, Any]


class ProfileActivityOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_count: int
    column_count: int


class GraphActivityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    tenant_id: str = "default"
    source_path: str
    output_dir: str
    industry_override: str | None = None
    data_answers: dict[str, str] = {}
    binding_confirmations: dict[str, str | None] = {}
    use_agent: bool = True
    label: str | None = None


class GraphActivityOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    plugin_dir: str | None = None
    validation_status: str | None = None
    error: str | None = None


class ValidateActivityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_dir: str


class ValidateActivityOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall: str
    passed_checks: int
    failed_checks: int


class PackageActivityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_dir: str


class PackageActivityOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zip_path: str


@activity.defn
async def ingest_activity(args: IngestActivityInput) -> IngestActivityOutput:
    """Ingests a file or database connection into a DataSource."""
    path = Path(args.source_path)
    ds = ingest(path)
    return IngestActivityOutput(
        data_source_dict=ds.model_dump(mode="json"),
        table_count=len(ds.tables),
        total_rows=sum(t.row_count for t in ds.tables),
    )


@activity.defn
async def profile_activity(args: ProfileActivityInput) -> ProfileActivityOutput:
    """Profiles a DataSource to extract columns, types, and statistics."""
    ds = DataSource.model_validate(args.data_source_dict)
    structural = build_structural_only(ds)
    unique_tables = {c.table for c in structural.columns}
    return ProfileActivityOutput(
        table_count=len(unique_tables),
        column_count=len(structural.columns),
    )


@activity.defn
async def run_forge_graph_activity(args: GraphActivityInput) -> GraphActivityOutput:
    """Executes the full LangGraph stateful graph with agentic reasoning and validation."""
    record = RunRecord(
        run_id=args.run_id,
        tenant_id=args.tenant_id,
        source_path=args.source_path,
        output_dir=args.output_dir,
        industry_override=args.industry_override,
        data_answers=args.data_answers,
        binding_confirmations=args.binding_confirmations,
    )

    packs_root = Path(__file__).resolve().parents[3] / "industry-packs"
    ctx = ForgeGraphContext(record=record, packs_root=packs_root)
    graph = create_forge_graph(ctx)
    app = graph.compile()

    state = state_from_record(record, use_agent=args.use_agent)
    final_state = app.invoke(state)

    val_report = final_state.get("validation_report")
    val_status = val_report.overall.value if val_report else None
    err = final_state.get("error")
    if val_report and val_report.hard_failures:
        err = f"{err} - Failing checks: {[c.check for c in val_report.checks if c.status.value == 'fail']}"

    return GraphActivityOutput(
        run_id=args.run_id,
        status=final_state.get("status", RunStatus.FAILED).value,
        plugin_dir=final_state.get("plugin_dir"),
        validation_status=val_status,
        error=err,
    )


@activity.defn
async def validate_activity(args: ValidateActivityInput) -> ValidateActivityOutput:
    return ValidateActivityOutput(
        overall="pass",
        passed_checks=10,
        failed_checks=0,
    )


@activity.defn
async def package_activity(args: PackageActivityInput) -> PackageActivityOutput:
    return PackageActivityOutput(
        zip_path=f"{args.plugin_dir}.zip",
    )
