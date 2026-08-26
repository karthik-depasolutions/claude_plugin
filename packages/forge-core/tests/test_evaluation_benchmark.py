"""Continuous evaluation benchmark runner for Forge pipeline accuracy and quality."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from forge_core.graph import ForgeGraphContext, create_forge_graph, state_from_record
from forge_core.models.common import CheckStatus, RunStage, RunStatus
from forge_core.models.run import RunRecord

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "fixtures" / "evaluation" / "eval_manifest.json"


@pytest.fixture(autouse=True)
def _cassette_mode(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_CASSETTE_MODE", os.environ.get("FORGE_LLM_CASSETTE_MODE", "replay"))
    monkeypatch.setenv("FORGE_LLM_CASSETTE_DIR", str(REPO_ROOT / "fixtures" / "cassettes"))


def test_golden_evaluation_benchmarks(tmp_path: Path):
    """Iterates through golden dataset benchmarks and validates classification,

    fact table selection, canonical role bindings, KPI compilation, and validation status.
    """
    assert MANIFEST_PATH.exists(), f"Evaluation manifest not found at {MANIFEST_PATH}"
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    results = []
    for bench in manifest["benchmarks"]:
        dataset_path = REPO_ROOT / bench["dataset_path"]
        if not dataset_path.exists():
            continue

        run_id = f"eval-{bench['id']}"
        record = RunRecord(
            run_id=run_id,
            source_path=str(dataset_path),
            output_dir=str(tmp_path / bench["id"]),
            industry_override=bench["expected_pack"],
            data_answers={"biz:bookings.status": "Completed"},
        )

        ctx = ForgeGraphContext(record=record, packs_root=REPO_ROOT / "industry-packs")
        graph = create_forge_graph(ctx)
        app = graph.compile()

        state = state_from_record(record, use_agent=False)
        final_state = app.invoke(state)

        # 1. Verify pipeline completion
        assert final_state["status"] == RunStatus.SUCCEEDED, f"Run failed for {bench['id']}: {final_state.get('error')}"
        assert final_state["current_stage"] == RunStage.VALIDATE

        # 2. Verify industry pack matching
        selected_pack = final_state["selected_pack"]
        assert selected_pack is not None
        assert selected_pack.slug == bench["expected_pack"], (
            f"Expected pack {bench['expected_pack']}, got {selected_pack.slug}"
        )

        # 3. Verify fact table grain
        bindings = final_state["bindings"]
        fact_table = bindings.table("fact")
        assert fact_table.grain == bench["expected_fact_table"], (
            f"Expected fact table {bench['expected_fact_table']}, got {fact_table.grain}"
        )

        # 4. Verify canonical bindings
        bound_columns = {c.role: c.physical for c in bindings.columns}
        for expected_role, expected_col in bench["expected_bindings"].items():
            actual_col = bound_columns.get(expected_role)
            assert actual_col == expected_col, (
                f"Role {expected_role!r} in {bench['id']}: expected {expected_col!r}, got {actual_col!r}"
            )

        # 5. Verify KPI compilation count
        kpi_defs = final_state["kpi_defs"]
        assert len(kpi_defs.kpis) >= bench["expected_min_kpi_count"], (
            f"Expected at least {bench['expected_min_kpi_count']} KPIs for {bench['id']}, got {len(kpi_defs.kpis)}"
        )

        # 6. Verify validation harness overall status
        report = final_state["validation_report"]
        assert report is not None
        assert report.overall in (CheckStatus.PASS, CheckStatus.WARN), (
            f"Validation harness failed for {bench['id']}: {report.overall}"
        )

        results.append({
            "benchmark": bench["id"],
            "pack": selected_pack.slug,
            "fact_table": fact_table.grain,
            "kpi_count": len(kpi_defs.kpis),
            "validation": report.overall.value,
        })

    assert len(results) >= 2, "At least 2 benchmarks must run successfully"
