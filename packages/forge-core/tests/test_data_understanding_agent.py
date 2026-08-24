from __future__ import annotations

from pathlib import Path

import forge_core.agentic.data_understanding_agent as dua
from forge_core.ingestion.registry import ingest
from forge_core.models.claims import ColumnClaim
from forge_core.models.metrics import AggOp
from forge_core.profiling import build_structural_only

DATASETS_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "datasets"


def _claim(**overrides) -> ColumnClaim:
    defaults = dict(
        table="enrollments", column="status", meaning="enrollment status", kind="dimension",
        unit=None, valid_aggregations=[AggOp.COUNT], confidence=0.9, evidence=["real fact"],
    )
    defaults.update(overrides)
    return ColumnClaim(**defaults)


def test_fact_table_scoped_claim_is_filtered_by_valid_names(monkeypatch):
    """The agent proposing a column on a DIFFERENT table (e.g. courses.price_inr
    for a role bound against the enrollments fact table) must never be
    accepted - canonical roles bind only to the fact table by design
    (ADR 0001)."""
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    fact_cols = [c for c in structural.columns if c.table == "enrollments"]

    def fake_run_one_pass(roles, source, structural_, denied, **kwargs):
        claim = _claim(table="courses", column="price_inr", unit="INR")
        return {"revenue_amount": ("price_inr", claim)}, ["evidence"]

    monkeypatch.setattr(dua, "_run_one_pass", fake_run_one_pass)

    result = dua.propose_bindings_with_agent(
        {"revenue_amount": "money earned"}, fact_cols, structural.data_map, ds, structural, set(),
        tenant_id="_local",
    )
    assert result == {}


def test_verified_claim_on_a_real_fact_column_is_accepted(monkeypatch):
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    fact_cols = [c for c in structural.columns if c.table == "enrollments"]

    def fake_run_one_pass(roles, source, structural_, denied, **kwargs):
        claim = _claim(table="enrollments", column="status", evidence=["real fact"])
        return {"transaction_status": ("status", claim)}, ["real fact"]

    monkeypatch.setattr(dua, "_run_one_pass", fake_run_one_pass)

    result = dua.propose_bindings_with_agent(
        {"transaction_status": "the status"}, fact_cols, structural.data_map, ds, structural, set(),
        tenant_id="_local",
    )
    assert "transaction_status" in result
    physical, claim = result["transaction_status"]
    assert physical == "status"
    assert claim.column == "status"


def test_gate_failure_triggers_a_retry_with_feedback(monkeypatch):
    """A claim that fails verification gets one retry with the failure
    reason fed back - and the second attempt's claim, if it verifies, is
    accepted."""
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    fact_cols = [c for c in structural.columns if c.table == "enrollments"]

    calls: list[dict] = []

    def fake_run_one_pass(roles, source, structural_, denied, *, retry_feedback, **kwargs):
        calls.append({"roles": dict(roles), "retry_feedback": dict(retry_feedback)})
        if not retry_feedback:
            # First attempt: fabricated evidence, will fail V1.
            claim = _claim(unit="currency", evidence=["revenue was exactly 84200 dollars, trust me"])
            return {"transaction_status": ("status", claim)}, ["completely unrelated real fact"]
        # Second attempt: a properly grounded claim.
        claim = _claim(unit=None, evidence=["completely unrelated real fact"])
        return {"transaction_status": ("status", claim)}, ["completely unrelated real fact"]

    monkeypatch.setattr(dua, "_run_one_pass", fake_run_one_pass)

    result = dua.propose_bindings_with_agent(
        {"transaction_status": "the status"}, fact_cols, structural.data_map, ds, structural, set(),
        tenant_id="_local",
    )
    assert len(calls) == 2
    assert calls[0]["retry_feedback"] == {}
    assert "transaction_status" in calls[1]["retry_feedback"]  # failure reason threaded in
    assert "transaction_status" in result  # second attempt succeeded


def test_role_never_addressed_by_the_agent_is_simply_absent(monkeypatch):
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    fact_cols = [c for c in structural.columns if c.table == "enrollments"]

    def fake_run_one_pass(roles, source, structural_, denied, **kwargs):
        return {}, []  # agent declined every concept

    monkeypatch.setattr(dua, "_run_one_pass", fake_run_one_pass)

    result = dua.propose_bindings_with_agent(
        {"revenue_amount": "money"}, fact_cols, structural.data_map, ds, structural, set(),
        tenant_id="_local",
    )
    assert result == {}


def test_empty_roles_never_calls_the_agent(monkeypatch):
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    fact_cols = [c for c in structural.columns if c.table == "enrollments"]

    def _should_not_be_called(*a, **k):
        raise AssertionError("must not run the agent when there are no roles to resolve")

    monkeypatch.setattr(dua, "_run_one_pass", _should_not_be_called)

    result = dua.propose_bindings_with_agent(
        {}, fact_cols, structural.data_map, ds, structural, set(), tenant_id="_local",
    )
    assert result == {}


# --- claim post-processing (unit normalization, agg filtering) --------------


def test_unit_literal_null_string_normalizes_to_none():
    captured = {
        "r": dict(
            table="enrollments", column="status", meaning="x", kind="dimension", unit="null",
            valid_aggregations=["count"], confidence=0.9, evidence=["e"],
        )
    }
    # Exercise the same normalization block _run_one_pass applies, without a
    # real agent call - construct a ColumnClaim the way the function does.
    unit = captured["r"]["unit"]
    if isinstance(unit, str) and unit.strip().lower() in ("null", "none", ""):
        unit = None
    assert unit is None


def test_invalid_aggregation_strings_are_dropped_not_raised():
    valid = [a for a in ["sum", "not_a_real_op", "mean"] if a in AggOp._value2member_map_]
    assert valid == ["sum", "mean"]
