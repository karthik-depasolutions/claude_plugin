"""Unit tests for agentic structured schemas and Evidence models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge_core.agentic.schemas import (
    BindingProposal,
    Evidence,
    IndustryAssessment,
    IndustryCandidateAssessment,
    KPIProposal,
    ValidationDiagnosis,
)


def test_evidence_model_validation():
    ev = Evidence(
        type="schema",
        source="column_profile",
        observation="Found decimal currency column amount_inr",
    )
    assert ev.type == "schema"
    assert ev.source == "column_profile"
    assert "amount_inr" in ev.observation

    with pytest.raises(ValidationError):
        Evidence(type="invalid_type", source="foo", observation="bar")  # type: ignore


def test_binding_proposal_with_evidence():
    proposal = BindingProposal(
        canonical_role="revenue_amount",
        physical_column="amount_inr",
        confidence=0.95,
        evidence=[
            Evidence(type="schema", source="profile", observation="numeric currency column"),
            Evidence(type="statistics", source="duckdb", observation="positive monetary values"),
        ],
        alternatives=["total_price"],
        needs_human_confirmation=False,
    )
    assert proposal.canonical_role == "revenue_amount"
    assert len(proposal.evidence) == 2
    assert proposal.confidence == 0.95
    assert not proposal.needs_human_confirmation


def test_industry_assessment_schema():
    assessment = IndustryAssessment(
        selected_industry="healthcare-diagnostics",
        confidence=0.92,
        evidence=[
            Evidence(type="industry_pack", source="signature_scorer", observation="Matched booking_id and lab_partner"),
        ],
        alternatives=[
            IndustryCandidateAssessment(slug="retail-ecommerce", name="Retail / E-Commerce", signature_score=0.42),
        ],
        needs_human_confirmation=False,
    )
    assert assessment.selected_industry == "healthcare-diagnostics"
    assert len(assessment.alternatives) == 1
    assert assessment.alternatives[0].slug == "retail-ecommerce"


def test_validation_diagnosis_schema():
    diag = ValidationDiagnosis(
        failed_check="binding_plausibility",
        category="plausibility",
        root_cause="Student ID was bound to revenue_amount with values 0-100",
        evidence=[
            Evidence(type="validation", source="harness", observation="0-100 values are score/pct, not revenue"),
        ],
        repairable=True,
        recommended_action="prune_binding",
        confidence=0.99,
    )
    assert diag.failed_check == "binding_plausibility"
    assert diag.repairable is True
    assert diag.recommended_action == "prune_binding"
