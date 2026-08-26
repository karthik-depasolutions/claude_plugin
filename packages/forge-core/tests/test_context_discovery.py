"""Tests for the Business Context Discovery Agent.

Verifies:
- Strongly-typed Pydantic schemas (Evidence, Hypothesis, BusinessQuestion, BusinessContext)
- Context investigation tools with safety & tenant boundaries
- Structural evidence analysis, hypothesis generation, and gap identification
- User answer incorporation into confirmed facts
- Readiness gate evaluation
- Golden evaluation fixtures
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge_core.agentic.agents.context_discovery import run_context_discovery_agent
from forge_core.agentic.graph.context_discovery_graph import (
    _analyze_structural_evidence,
    _build_business_context_model,
)
from forge_core.agentic.schemas.business_context import (
    BusinessAnswer,
    BusinessContext,
    BusinessQuestion,
    Evidence,
    Hypothesis,
)
from forge_core.agentic.context_tools import (
    AllowlistViolation,
    ContextToolkit,
    _detect_inconsistent_categories,
    _get_duplicate_profile,
    _inspect_column,
    _inspect_schema,
    _run_safe_duckdb_query,
    build_context_discovery_tools,
)
from forge_core.classification import load_all_packs
from forge_core.ingestion.registry import ingest
from forge_core.profiling import build_structural_only

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"
DATASETS_DIR = FIXTURES_DIR / "datasets"
PACKS_DIR = Path(__file__).resolve().parents[3] / "industry-packs"
EVAL_DIR = FIXTURES_DIR / "evaluation" / "context_discovery"


@pytest.fixture
def sparda_datasource():
    csv_path = DATASETS_DIR / "sparda_leads.csv"
    return ingest(str(csv_path))


@pytest.fixture
def sparda_structural(sparda_datasource):
    return build_structural_only(sparda_datasource)


def test_business_context_schemas():
    """Verify all schema models serialize and validate correctly."""
    ev = Evidence(
        type="statistics",
        source="sparda_leads.phone_number",
        observation="5,530 rows with 2,260 unique phone numbers",
    )
    assert ev.type == "statistics"
    assert "2,260" in ev.observation

    hyp = Hypothesis(
        id="hyp_grain",
        category="record_grain",
        claim="Each row represents a lead interaction attempt.",
        confidence=0.9,
        evidence=[ev],
    )
    assert hyp.status == "proposed"
    assert len(hyp.evidence) == 1

    bq = BusinessQuestion(
        question_id="q_grain",
        category="record_grain",
        question="What does one row represent?",
        context="5,530 rows with 2,260 unique phone numbers",
        evidence=[ev],
        options=["One row = one call", "One row = one lead"],
        impact="critical",
    )
    assert bq.impact == "critical"
    assert bq.required is True


def test_discovery_is_industry_and_language_agnostic(tmp_path: Path):
    """The same dataset with non-English, domain-free column names must
    produce the same structural findings. Every selection rule the old
    implementation used ("id"/"phone"/"outcome"/"status"/"agent" substrings)
    fails this test by construction; measured uniqueness and cardinality
    pass it."""
    csv_path = tmp_path / "kayitlar.csv"
    csv_path.write_text(
        "kayit,musteri,durum,tutar\n"
        "1,AAA,Tamamlandi,100\n"
        "2,BBB,Iptal,200\n"
        "3,AAA,Tamamlandi,300\n"
        "4,CCC,tamamlandi,400\n"
        "5,BBB,Beklemede,500\n"
        "6,DDD,Iptal,600\n",
        encoding="utf-8",
    )
    ds = ingest(str(csv_path))
    structural = build_structural_only(ds)
    context = run_context_discovery_agent(ds, structural, load_all_packs(PACKS_DIR))

    # `musteri` repeats (4 distinct / 6 rows) -> the grain ambiguity is found
    # even though nothing in its name says "customer" or "id".
    grain_q = next(q for q in context.open_questions if q.category == "record_grain")
    assert "musteri" in grain_q.question
    assert grain_q.impact == "critical"

    # `durum` is enum-shaped -> a success-definition question is asked, and
    # its options are the real observed values.
    outcome_q = next(q for q in context.open_questions if q.question_id == "q_outcome_durum")
    assert "Tamamlandi" in outcome_q.options

    # "Tamamlandi" vs "tamamlandi" is caught from real DISTINCT values.
    assert any(dq.code == "CATEGORY_CASING_VARIATIONS" for dq in context.data_quality_issues)


def test_context_tools_safety(sparda_datasource: DataSource, sparda_structural):
    """Verify context tools enforce allowlist and block malicious SQL."""
    toolkit = ContextToolkit(
        data_source=sparda_datasource,
        structural=sparda_structural,
        denied_columns={"phone_number"},
    )

    # Denied column cannot be accessed
    with pytest.raises(AllowlistViolation, match="denied by security/PII"):
        toolkit.column("sparda_leads", "phone_number")

    # Non-existent table raises AllowlistViolation
    with pytest.raises(AllowlistViolation, match="not a valid table"):
        toolkit.table_ref("fake_table")

    # Safe SELECT works
    res = _run_safe_duckdb_query(toolkit, 'SELECT "outcome_of_the_call" FROM "sparda_leads" LIMIT 3')
    assert len(res) <= 3

    # DDL / DML is blocked
    with pytest.raises(AllowlistViolation, match="Only read-only SELECT"):
        _run_safe_duckdb_query(toolkit, "DROP TABLE sparda_leads")

    # A table outside this tenant's dataset is rejected rather than passed
    # through un-rewritten to DuckDB, which would happily resolve another
    # attached database or an internal catalog view.
    with pytest.raises(AllowlistViolation, match="not a valid table"):
        _run_safe_duckdb_query(toolkit, "SELECT * FROM duckdb_settings()")
    with pytest.raises(AllowlistViolation, match="not a valid table"):
        _run_safe_duckdb_query(toolkit, 'SELECT * FROM "some_other_tenant_table"')

    # A CTE the query defines itself is a legal reference, not a foreign table.
    rows = _run_safe_duckdb_query(
        toolkit,
        'WITH c AS (SELECT "category" FROM "sparda_leads") SELECT "category" FROM c LIMIT 2',
    )
    assert len(rows) <= 2


def test_evidence_mining_and_hypotheses(sparda_datasource: DataSource, sparda_structural):
    """Verify the discovery engine extracts correct evidence and questions from sparda_leads."""
    packs = load_all_packs(PACKS_DIR)
    evidence, hypotheses, questions, dq_issues = _analyze_structural_evidence(
        sparda_datasource,
        sparda_structural,
        packs=packs,
    )

    # Check that repeated identifiers trigger grain hypothesis and question
    grain_hyp = next((h for h in hypotheses if h.id == "hyp_record_grain_interaction"), None)
    assert grain_hyp is not None
    assert "interaction" in grain_hyp.claim.lower()

    grain_q = next((q for q in questions if q.category == "record_grain"), None)
    assert grain_q is not None
    assert grain_q.impact == "critical"
    assert len(grain_q.evidence) > 0

    # A success-definition question is asked, offering the column's real
    # observed values as choices.
    #
    # Deliberately NOT asserting it lands on `outcome_of_the_call`: this
    # dataset has several structurally identical label columns (sentiment,
    # agent_id, category, outcome_of_the_call all repeat a short value set),
    # and nothing separates them without reading the column *name*. The old
    # version of this assertion passed only because the implementation
    # matched the substring "outcome" - it was testing the name heuristic
    # this agent exists to replace. Picking the semantically right column is
    # the LLM pass's job; see fixtures/evaluation/context_discovery/_README.md.
    outcome_q = next((q for q in questions if q.category == "success_definition"), None)
    assert outcome_q is not None
    assert outcome_q.impact == "critical"
    assert outcome_q.options, "choices must come from real observed values"
    assert outcome_q.evidence

    # Check that staging/test values in agent_id trigger data quality question
    test_dq = next((dq for dq in dq_issues if dq.code == "TEST_ENVIRONMENT_VALUES_PRESENT"), None)
    assert test_dq is not None

    filter_q = next((q for q in questions if "agent_id" in q.question_id), None)
    assert filter_q is not None
    assert any("staging" in opt.lower() or "asd" in opt.lower() for opt in filter_q.options)

    # Check casing variation discovery
    casing_dq = next((dq for dq in dq_issues if dq.code == "CATEGORY_CASING_VARIATIONS"), None)
    assert casing_dq is not None


def test_readiness_gate_and_answers(sparda_datasource: DataSource, sparda_structural):
    """Verify that un-answered critical questions leave the model not ready, while answers achieve readiness."""
    packs = load_all_packs(PACKS_DIR)
    
    # 1. Unanswered state -> Not Ready
    context_unanswered = run_context_discovery_agent(
        sparda_datasource,
        sparda_structural,
        packs=packs,
    )
    assert context_unanswered.ready_for_downstream_pipeline is False
    assert len(context_unanswered.open_questions) > 0

    # 2. Answer all critical questions -> Ready
    answers = [
        BusinessAnswer(
            question_id=q.question_id,
            answer_text="Confirmed business rule",
            selected_options=[q.options[0]] if q.options else [],
        )
        for q in context_unanswered.open_questions
        if q.impact == "critical"
    ]

    context_answered = run_context_discovery_agent(
        sparda_datasource,
        sparda_structural,
        packs=packs,
        answers=answers,
    )
    assert context_answered.ready_for_downstream_pipeline is True
    assert len(context_answered.confirmed_facts) >= len(answers)

    # Confidence tracks how much of what we asked actually got answered, so
    # resolving only the critical questions must NOT report full confidence
    # while medium-impact ambiguities are still open.
    assert 0.55 < context_answered.overall_confidence < 0.95

    # Answering everything is what earns the ceiling.
    all_answers = [
        BusinessAnswer(
            question_id=q.question_id,
            answer_text="Confirmed business rule",
            selected_options=[q.options[0]] if q.options else [],
        )
        for q in context_unanswered.open_questions
    ]
    context_fully_answered = run_context_discovery_agent(
        sparda_datasource, sparda_structural, packs=packs, answers=all_answers
    )
    assert context_fully_answered.overall_confidence == 0.95


def test_golden_context_evaluation(sparda_datasource: DataSource, sparda_structural):
    """The deterministic half of the golden fixture - everything reachable
    without an LLM. `domain` and `business_process` are deliberately NOT
    asserted here: naming an industry is a semantic judgement, and the only
    reason the old version of this test passed offline was a hardcoded
    `slug == "edtech"` keyword fallback, which made it tautological rather
    than a measurement. See test_domain_is_abstained_without_an_agent."""
    packs = load_all_packs(PACKS_DIR)
    context = run_context_discovery_agent(sparda_datasource, sparda_structural, packs)

    golden_file = EVAL_DIR / "edtech" / "golden_context.json"
    assert golden_file.exists()
    golden = json.loads(golden_file.read_text(encoding="utf-8"))["deterministic"]

    found_codes = {dq.code for dq in context.data_quality_issues}
    assert set(golden["expected_quality_issues"]) <= found_codes

    # The entity key is the column measured to be *unique* (lead_id), while
    # the grain question is raised against the column measured to *repeat*
    # (phone_number - 8 distinct across 10 rows). Both come from cardinality,
    # not from one being named "*_id" and the other "phone_number", which is
    # what makes this work on an arbitrary upload from an unseen industry.
    entity = next(e for e in context.primary_entities if e.table == "sparda_leads")
    assert entity.identifier_column == golden["entities"]["sparda_leads"]["identifier_column"]
    assert entity.is_unique_key is True

    grain_q = next(q for q in context.open_questions if q.category == "record_grain")
    assert "phone_number" in grain_q.question


def test_domain_is_abstained_without_an_agent(sparda_datasource: DataSource, sparda_structural):
    """With no LLM available the agent must report an honest "I don't know"
    for industry rather than pattern-matching column names to a pack slug.
    A confidently wrong domain is worse than None: CLASSIFY treats it as
    advisory evidence, so a fabricated one biases real downstream decisions."""
    packs = load_all_packs(PACKS_DIR)
    context = run_context_discovery_agent(sparda_datasource, sparda_structural, packs)

    assert context.domain is None
    assert context.domain_confidence == 0.0
    # Nor may it invent the business facts only a customer can supply.
    assert context.business_objective is None
    assert context.business_process is None
    assert context.success_definition is None
    assert context.candidate_kpis == []


def test_numeric_columns_are_never_assumed_additive(sparda_datasource: DataSource, sparda_structural):
    """`lead_score` is numeric, but summing scores is meaningless. Additivity
    is a gate-verified semantic claim (see compiler/metric_generator.py's
    default-deny SUM); being numeric is not evidence of it."""
    packs = load_all_packs(PACKS_DIR)
    context = run_context_discovery_agent(sparda_datasource, sparda_structural, packs)

    measures = {m.column: m for m in context.important_measures}
    assert "lead_score" in measures
    assert all(not m.is_additive for m in context.important_measures)
