from __future__ import annotations

from pathlib import Path

from forge_core.agentic.investigation_tools import Coverage, RelationshipFact
from forge_core.ingestion.registry import ingest
from forge_core.models.claims import ColumnClaim, RelationClaim
from forge_core.models.entity_graph import JoinEdge
from forge_core.models.metrics import AggOp
from forge_core.models.schema_profile import ColumnProfile
from forge_core.profiling import build_structural_only
from forge_core.validation.gates import (
    ClaimOutcome,
    GateVerdict,
    route,
    verify_aggregation_validity,
    verify_column_claim,
    verify_distribution_plausibility,
    verify_evidence_exists,
    verify_fan_out_safety,
    verify_relation,
    verify_relation_claim,
    verify_value_set_coverage,
)

DATASETS_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "datasets"


def _score_col() -> ColumnProfile:
    return ColumnProfile(
        table="enrollments", name="score", dtype="DOUBLE", null_percent=0.0, cardinality=9,
        distinct_ratio=0.9, guessed_role="numeric", min_value=67.5, max_value=95.5,
    )


# --- V1 evidence -------------------------------------------------------------


def test_v1_fabricated_evidence_fails():
    result = verify_evidence_exists(["score is bounded 0-100"], real_evidence_log=["min=67.5, max=95.5"])
    assert result.verdict == GateVerdict.FAILED


def test_v1_real_evidence_passes():
    result = verify_evidence_exists(
        ["min=67.5, max=95.5"], real_evidence_log=["inspect_column returned: min=67.5, max=95.5"]
    )
    assert result.verdict == GateVerdict.VERIFIED


def test_v1_no_evidence_at_all_fails():
    result = verify_evidence_exists([], real_evidence_log=["something"])
    assert result.verdict == GateVerdict.FAILED


# --- V2 distribution plausibility --------------------------------------------


def test_v2_score_claimed_as_currency_fails():
    """The exact scenario the whole gate exists for."""
    claim = ColumnClaim(
        table="enrollments", column="score", meaning="test score", kind="measure",
        unit="currency", confidence=0.9, evidence=["real"],
    )
    result = verify_distribution_plausibility(claim, _score_col())
    assert result.verdict == GateVerdict.FAILED
    assert "score" in result.reasons[0] or "money" in result.reasons[0]


def test_v2_score_claimed_as_score_passes():
    claim = ColumnClaim(
        table="enrollments", column="score", meaning="test score", kind="measure",
        unit="score", confidence=0.9, evidence=["real"],
    )
    result = verify_distribution_plausibility(claim, _score_col())
    assert result.verdict == GateVerdict.VERIFIED


def test_v2_percent_over_100_fails():
    col = ColumnProfile(
        table="t", name="c", dtype="DOUBLE", null_percent=0.0, cardinality=5,
        distinct_ratio=1.0, guessed_role="numeric", min_value=0, max_value=150,
    )
    claim = ColumnClaim(table="t", column="c", meaning="x", kind="measure", unit="percent",
                        confidence=0.5, evidence=["real"])
    assert verify_distribution_plausibility(claim, col).verdict == GateVerdict.FAILED


def test_v2_time_unit_with_one_distinct_value_fails():
    col = ColumnProfile(
        table="t", name="d", dtype="DATE", null_percent=0.0, cardinality=1,
        distinct_ratio=0.01, guessed_role="date",
    )
    claim = ColumnClaim(table="t", column="d", meaning="x", kind="time", unit="time",
                        confidence=0.5, evidence=["real"])
    assert verify_distribution_plausibility(claim, col).verdict == GateVerdict.FAILED


# --- V3 aggregation validity --------------------------------------------------


def test_v3_sum_on_a_percentage_fails():
    claim = ColumnClaim(
        table="t", column="rate", meaning="completion rate", kind="measure", unit="percent",
        valid_aggregations=[AggOp.SUM], confidence=0.9, evidence=["real"],
    )
    assert verify_aggregation_validity(claim).verdict == GateVerdict.FAILED


def test_v3_mean_on_a_percentage_passes():
    claim = ColumnClaim(
        table="t", column="rate", meaning="completion rate", kind="measure", unit="percent",
        valid_aggregations=[AggOp.MEAN], confidence=0.9, evidence=["real"],
    )
    assert verify_aggregation_validity(claim).verdict == GateVerdict.VERIFIED


def test_v3_sum_on_currency_passes():
    claim = ColumnClaim(
        table="t", column="amount", meaning="revenue", kind="measure", unit="currency",
        valid_aggregations=[AggOp.SUM], confidence=0.9, evidence=["real"],
    )
    assert verify_aggregation_validity(claim).verdict == GateVerdict.VERIFIED


# --- V4 relation verification -------------------------------------------------


def test_v4_high_overlap_is_verified():
    fact = RelationshipFact(
        from_table="enrollments", from_column="course_id", to_table="courses", to_column="course_id",
        overlap_ratio=1.0, cardinality="N:1", orphan_ratio=0.0,
    )
    assert verify_relation(fact).verdict == GateVerdict.VERIFIED


def test_v4_low_overlap_is_rejected():
    fact = RelationshipFact(
        from_table="a", from_column="x", to_table="b", to_column="y",
        overlap_ratio=0.05, cardinality="N:1", orphan_ratio=0.9,
    )
    assert verify_relation(fact).verdict == GateVerdict.FAILED


def test_v4_end_to_end_against_real_data():
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    real_edge = next(e for e in structural.entity_graph.edges if e.to_table == "courses")
    fact = RelationshipFact(
        from_table=real_edge.from_table, from_column=real_edge.from_column,
        to_table=real_edge.to_table, to_column=real_edge.to_column,
        overlap_ratio=real_edge.overlap_ratio, cardinality=real_edge.cardinality,
        orphan_ratio=real_edge.orphan_ratio,
    )
    assert verify_relation(fact).verdict == GateVerdict.VERIFIED


# --- V5 value-set coverage -----------------------------------------------------


def test_v5_unmatched_candidate_is_rejected():
    coverage = Coverage(
        table="t", column="status", matched=["completed"], unmatched_candidates=["invented"],
        coverage_ratio=0.5, real_distinct_values=["completed", "dropped", "active"],
    )
    assert verify_value_set_coverage(coverage).verdict == GateVerdict.FAILED


def test_v5_low_coverage_warns_but_still_verifies():
    coverage = Coverage(
        table="t", column="status", matched=["completed"], unmatched_candidates=[],
        coverage_ratio=1.0, real_distinct_values=["completed", "dropped", "active", "cancelled", "pending"],
    )
    result = verify_value_set_coverage(coverage)
    assert result.verdict == GateVerdict.VERIFIED
    assert result.reasons  # warned, not blocked


def test_v5_full_coverage_no_warning():
    coverage = Coverage(
        table="t", column="status", matched=["completed", "dropped"], unmatched_candidates=[],
        coverage_ratio=1.0, real_distinct_values=["completed", "dropped"],
    )
    result = verify_value_set_coverage(coverage)
    assert result.verdict == GateVerdict.VERIFIED
    assert result.reasons == []


# --- V6 fan-out safety ---------------------------------------------------------


def _edge(cardinality: str, fan_out: bool) -> JoinEdge:
    return JoinEdge(
        from_table="a", from_column="id", to_table="b", to_column="id", cardinality=cardinality,
        overlap_ratio=1.0, orphan_ratio=0.0, confidence=1.0, origin="declared_fk", verified=True,
        evidence="test", fan_out_risk=fan_out,
    )


def test_v6_fan_out_edge_fails():
    assert verify_fan_out_safety([_edge("1:N", True)]).verdict == GateVerdict.FAILED


def test_v6_safe_path_passes():
    assert verify_fan_out_safety([_edge("N:1", False)]).verdict == GateVerdict.VERIFIED


def test_v6_end_to_end_real_retail_orders_path():
    ds = ingest(DATASETS_ROOT / "retail_orders")
    structural = build_structural_only(ds)
    graph = structural.entity_graph
    unsafe_path = graph.join_path("orders", "order_items")
    safe_path = graph.join_path("order_items", "orders")
    assert verify_fan_out_safety(unsafe_path).verdict == GateVerdict.FAILED
    assert verify_fan_out_safety(safe_path).verdict == GateVerdict.VERIFIED


# --- Orchestrators + routing --------------------------------------------------


def test_verify_column_claim_combines_all_three_gates():
    claim = ColumnClaim(
        table="enrollments", column="score", meaning="revenue", kind="measure", unit="currency",
        confidence=0.9, evidence=["min=67.5, max=95.5"],
    )
    result = verify_column_claim(claim, _score_col(), real_evidence_log=["min=67.5, max=95.5"])
    assert result.verdict == GateVerdict.FAILED  # V2 catches it even though V1 passed


def test_verify_relation_claim_combines_evidence_and_relation():
    claim = RelationClaim(
        from_table="a", from_column="x", to_table="b", to_column="y", confidence=0.5,
        evidence=["overlap looked high"],
    )
    fact = RelationshipFact(
        from_table="a", from_column="x", to_table="b", to_column="y",
        overlap_ratio=1.0, cardinality="N:1", orphan_ratio=0.0,
    )
    # fabricated evidence -> V1 fails even though the relation itself verifies
    result = verify_relation_claim(claim, fact, real_evidence_log=["something else entirely"])
    assert result.verdict == GateVerdict.FAILED


def test_route_verified_is_accepted():
    assert route(GateVerdict.VERIFIED, attempts=0) == ClaimOutcome.ACCEPTED


def test_route_failed_under_max_attempts_retries():
    assert route(GateVerdict.FAILED, attempts=0) == ClaimOutcome.RETRY
    assert route(GateVerdict.FAILED, attempts=1) == ClaimOutcome.RETRY


def test_route_failed_at_max_attempts_escalates():
    assert route(GateVerdict.FAILED, attempts=2) == ClaimOutcome.ESCALATED


def test_route_unverifiable_always_escalates():
    assert route(GateVerdict.UNVERIFIABLE, attempts=0) == ClaimOutcome.ESCALATED
