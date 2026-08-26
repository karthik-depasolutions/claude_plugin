"""Tests for the LangGraph-based Forge pipeline execution graph."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from forge_core.graph import (
    ForgeGraphContext,
    ForgeState,
    create_forge_graph,
    state_from_record,
    sync_state_to_record,
)
from forge_core.models.common import CheckStatus, RunStage, RunStatus
from forge_core.models.quality import DataQuestion, DataReview
from forge_core.models.run import RunRecord


@pytest.fixture(autouse=True)
def _cassette_mode(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_CASSETTE_MODE", os.environ.get("FORGE_LLM_CASSETTE_MODE", "replay"))
    monkeypatch.setenv("FORGE_LLM_CASSETTE_DIR", "fixtures/cassettes")


def test_forge_graph_end_to_end(bookings_csv: Path, tmp_path: Path):
    """Executes the complete LangGraph StateGraph from ingest through validate."""
    record = RunRecord(
        run_id="test-graph-run-1",
        source_path=str(bookings_csv),
        output_dir=str(tmp_path),
        industry_override="healthcare-diagnostics",
        data_answers={"biz:bookings.status": "Completed"},
    )
    ctx = ForgeGraphContext(record=record, packs_root=Path("industry-packs"))
    graph = create_forge_graph(ctx)
    app = graph.compile()

    initial_state = state_from_record(record, use_agent=False)
    final_state = app.invoke(initial_state)

    assert final_state["current_stage"] == RunStage.VALIDATE
    assert final_state["status"] == RunStatus.SUCCEEDED
    assert final_state["selected_pack"] is not None
    assert final_state["selected_pack"].slug == "healthcare-diagnostics"
    assert len(final_state["kpi_defs"].kpis) > 0
    assert len(final_state["metric_defs"]) > 0
    assert final_state["validation_report"] is not None
    assert final_state["validation_report"].overall in (CheckStatus.PASS, CheckStatus.WARN)

    sync_state_to_record(final_state, record)
    assert record.status == RunStatus.SUCCEEDED


def test_forge_graph_pauses_on_unanswered_questions(bookings_csv: Path, tmp_path: Path):
    """StateGraph enters NEEDS_INPUT when data-review questions have no answers."""
    record = RunRecord(
        run_id="test-graph-run-2",
        source_path=str(bookings_csv),
        output_dir=str(tmp_path),
        data_answers=None,
    )
    record.data_review = DataReview(
        generated_at="2026-08-25T12:00:00Z",
        questions=[
            DataQuestion(
                id="q1",
                question="Which status values represent completed appointments?",
                context="Observed values: Completed, Cancelled, No-Show",
            )
        ],
    )
    ctx = ForgeGraphContext(record=record, packs_root=Path("industry-packs"))
    graph = create_forge_graph(ctx)
    app = graph.compile()

    initial_state = state_from_record(record, use_agent=False)
    initial_state["data_review"] = record.data_review
    initial_state["data_answers"] = None
    final_state = app.invoke(initial_state)

    # Must pause at CLASSIFY
    assert final_state["status"] == RunStatus.NEEDS_INPUT
    sync_state_to_record(final_state, record)
    assert record.status == RunStatus.NEEDS_INPUT


def test_forge_graph_resumes_after_answering(bookings_csv: Path, tmp_path: Path):
    """StateGraph continues past pause once user provides answers."""
    record = RunRecord(
        run_id="test-graph-run-3",
        source_path=str(bookings_csv),
        output_dir=str(tmp_path),
        industry_override="healthcare-diagnostics",
        data_answers=None,
    )
    record.data_review = DataReview(
        generated_at="2026-08-25T12:00:00Z",
        questions=[
            DataQuestion(
                id="q1",
                question="Which status values represent completed appointments?",
                context="Observed values: Completed, Cancelled, No-Show",
            )
        ],
    )
    ctx = ForgeGraphContext(record=record, packs_root=Path("industry-packs"))
    graph = create_forge_graph(ctx)
    app = graph.compile()

    # Pass 1: Pauses
    state1 = state_from_record(record, use_agent=False)
    state1["data_review"] = record.data_review
    state1["data_answers"] = None
    res1 = app.invoke(state1)
    assert res1["status"] == RunStatus.NEEDS_INPUT

    # Pass 2: Resume with answers
    record.data_answers = {"q1": "Completed"}
    state2 = state_from_record(record, use_agent=False)
    state2["data_review"] = record.data_review
    state2["data_answers"] = {"q1": "Completed"}
    res2 = app.invoke(state2)

    assert res2["status"] == RunStatus.SUCCEEDED
    assert res2["current_stage"] == RunStage.VALIDATE
