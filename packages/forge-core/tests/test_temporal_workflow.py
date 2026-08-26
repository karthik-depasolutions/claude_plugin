"""Unit and activity tests for Temporal workflow definitions."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_core.ingestion.registry import ingest
from workers.temporal_worker.activities.activities import (
    GraphActivityInput,
    IngestActivityInput,
    ProfileActivityInput,
    ingest_activity,
    profile_activity,
    run_forge_graph_activity,
)
from workers.temporal_worker.workflows.forge_generation import (
    ForgeWorkflowInput,
    ForgeWorkflowOutput,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASETS_ROOT = REPO_ROOT / "fixtures" / "datasets"


@pytest.mark.asyncio
async def test_ingest_activity():
    input_data = IngestActivityInput(source_path=str(DATASETS_ROOT / "bookings.csv"))
    output = await ingest_activity(input_data)
    assert output.table_count == 1
    assert output.total_rows > 0
    assert "tables" in output.data_source_dict


@pytest.mark.asyncio
async def test_profile_activity():
    ds = ingest(DATASETS_ROOT / "bookings.csv")
    input_data = ProfileActivityInput(data_source_dict=ds.model_dump(mode="json"))
    output = await profile_activity(input_data)
    assert output.table_count == 1
    assert output.column_count > 0


@pytest.mark.asyncio
async def test_run_forge_graph_activity(tmp_path: Path):
    input_data = GraphActivityInput(
        run_id="test-temporal-run",
        source_path=str(DATASETS_ROOT / "bookings.csv"),
        output_dir=str(tmp_path / "output"),
        industry_override="healthcare-diagnostics",
        data_answers={"biz:bookings.status": "Completed"},
        use_agent=False,
    )
    output = await run_forge_graph_activity(input_data)
    assert output.status == "succeeded", f"Failed with error: {output.error}"
    assert output.plugin_dir is not None
    assert output.validation_status in ("pass", "warn")


def test_workflow_models():
    wf_input = ForgeWorkflowInput(
        run_id="wf-1",
        source_path="/path/to/data.csv",
        output_dir="/path/to/out",
    )
    assert wf_input.run_id == "wf-1"
    assert wf_input.tenant_id == "default"

    wf_output = ForgeWorkflowOutput(
        run_id="wf-1",
        status="succeeded",
        plugin_dir="/path/to/out/plugin",
        validation_status="pass",
    )
    assert wf_output.status == "succeeded"
