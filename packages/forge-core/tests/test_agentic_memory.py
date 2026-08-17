"""Unit tests for the binding agent's two memory mechanisms
(`forge_core.agentic.memory`): the decision cache and the reasoning-trace
checkpointer. No live LLM calls here - see `test_agentic_binding.py` for
those; this file only exercises the SQLite-backed plumbing."""

from __future__ import annotations

from pathlib import Path

import pytest
from forge_core.agentic import memory
from forge_core.models.common import ColumnRole
from forge_core.models.schema_profile import ColumnProfile


def _col(name: str, dtype: str = "VARCHAR") -> ColumnProfile:
    return ColumnProfile(
        table="fact",
        name=name,
        dtype=dtype,
        guessed_role=ColumnRole.UNKNOWN,
        null_percent=0.0,
        cardinality=1,
        distinct_ratio=1.0,
        sample_values=[],
    )


@pytest.fixture(autouse=True)
def _isolated_memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGE_AGENT_MEMORY_DIR", str(tmp_path / "agent_memory"))


def test_schema_fingerprint_is_stable_regardless_of_column_order():
    fp_a = memory.schema_fingerprint([_col("revenue"), _col("region")])
    fp_b = memory.schema_fingerprint([_col("region"), _col("revenue")])
    assert fp_a == fp_b


def test_schema_fingerprint_differs_for_different_schemas():
    fp_a = memory.schema_fingerprint([_col("revenue")])
    fp_b = memory.schema_fingerprint([_col("revenue"), _col("region")])
    assert fp_a != fp_b


def test_get_exact_decision_misses_when_never_recorded():
    fingerprint = memory.schema_fingerprint([_col("revenue")])
    assert memory.get_exact_decision("healthcare-diagnostics", "revenue_amount", fingerprint) is None


def test_get_exact_decision_hits_after_record_decision():
    fingerprint = memory.schema_fingerprint([_col("revenue")])
    memory.record_decision(
        "healthcare-diagnostics", "revenue_amount", fingerprint, "revenue", 0.9, "looked numeric and monetary"
    )

    cached = memory.get_exact_decision("healthcare-diagnostics", "revenue_amount", fingerprint)

    assert cached is not None
    assert cached.column == "revenue"
    assert cached.confidence == 0.9


def test_get_exact_decision_only_matches_same_pack_role_and_fingerprint():
    fingerprint = memory.schema_fingerprint([_col("revenue")])
    memory.record_decision("healthcare-diagnostics", "revenue_amount", fingerprint, "revenue", 0.9, "reason")

    assert memory.get_exact_decision("retail", "revenue_amount", fingerprint) is None
    assert memory.get_exact_decision("healthcare-diagnostics", "order_count", fingerprint) is None
    other_fingerprint = memory.schema_fingerprint([_col("revenue"), _col("region")])
    assert memory.get_exact_decision("healthcare-diagnostics", "revenue_amount", other_fingerprint) is None


def test_get_exact_decision_returns_most_recent_when_recorded_twice():
    fingerprint = memory.schema_fingerprint([_col("revenue")])
    memory.record_decision("pack", "role", fingerprint, "old_column", 0.5, "first pass")
    memory.record_decision("pack", "role", fingerprint, "new_column", 0.95, "corrected pass")

    cached = memory.get_exact_decision("pack", "role", fingerprint)

    assert cached is not None
    assert cached.column == "new_column"


def test_recent_examples_excludes_the_current_fingerprint():
    same_fingerprint = memory.schema_fingerprint([_col("a")])
    other_fingerprint = memory.schema_fingerprint([_col("a"), _col("b")])
    memory.record_decision("pack", "role", same_fingerprint, "col_same", 0.9, "same schema")
    memory.record_decision("pack", "role", other_fingerprint, "col_other", 0.9, "other schema")

    examples = memory.recent_examples("pack", "role", exclude_fingerprint=same_fingerprint)

    assert [e.column for e in examples] == ["col_other"]


def test_recent_examples_excludes_failed_none_decisions():
    fingerprint_a = memory.schema_fingerprint([_col("a")])
    fingerprint_b = memory.schema_fingerprint([_col("b")])
    memory.record_decision("pack", "role", fingerprint_a, None, 0.0, "nothing fit")
    memory.record_decision("pack", "role", fingerprint_b, "col_b", 0.9, "fit")

    examples = memory.recent_examples("pack", "role", exclude_fingerprint="irrelevant")

    assert [e.column for e in examples] == ["col_b"]


def test_recent_examples_respects_limit():
    for i in range(5):
        fingerprint = memory.schema_fingerprint([_col(f"col_{i}")])
        memory.record_decision("pack", "role", fingerprint, f"col_{i}", 0.9, "reason")

    examples = memory.recent_examples("pack", "role", exclude_fingerprint="irrelevant", limit=2)

    assert len(examples) == 2


def test_trace_checkpointer_persists_and_is_readable_across_calls():
    with memory.trace_checkpointer() as checkpointer:
        config = {"configurable": {"thread_id": "test-thread-1", "checkpoint_ns": ""}}
        checkpoint = {
            "v": 4,
            "ts": "2024-07-31T20:14:19.804150+00:00",
            "id": "1ef4f797-8335-6428-8001-8a1503f9b875",
            "channel_values": {"messages": []},
            "channel_versions": {},
            "versions_seen": {},
        }
        checkpointer.put(config, checkpoint, {}, {})

    with memory.trace_checkpointer() as checkpointer:
        stored = checkpointer.get({"configurable": {"thread_id": "test-thread-1", "checkpoint_ns": ""}})

    assert stored is not None
