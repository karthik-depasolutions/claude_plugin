"""Cross-industry golden evaluation for the Context Discovery Agent.

Every directory under `fixtures/evaluation/context_discovery/` is a
benchmark, discovered at collection time - dropping in a new dataset and
fixture adds coverage with no change here. Spec §26 requires the golden
suite to run whenever the model, prompt, tools, schema, or routing change.

Only the `deterministic` half of each fixture is asserted. The `semantic`
half (domain, business process) needs a live agent; asserting it offline is
what made the original edtech fixture tautological - it passed because of a
hardcoded `slug == "edtech"` keyword fallback, so it measured the fallback
rather than the agent. See the fixtures' `_README.md`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from forge_core.agentic.agents.context_discovery import (
    MAX_CONTEXT_QUESTIONS,
    merge_context_questions_into_review,
    run_context_discovery_agent,
)
from forge_core.classification import load_all_packs
from forge_core.ingestion.registry import ingest
from forge_core.models.quality import DataReview
from forge_core.profiling import build_structural_only

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKS_DIR = REPO_ROOT / "industry-packs"
EVAL_DIR = REPO_ROOT / "fixtures" / "evaluation" / "context_discovery"

BENCHMARKS = sorted(p.parent.name for p in EVAL_DIR.glob("*/golden_context.json"))


@pytest.fixture(scope="module")
def packs():
    return load_all_packs(PACKS_DIR)


def _load(name: str) -> dict:
    return json.loads((EVAL_DIR / name / "golden_context.json").read_text(encoding="utf-8"))


def _discover(name: str, packs):
    golden = _load(name)
    source = REPO_ROOT / golden["dataset"]
    assert source.exists(), f"{name}: dataset {source} is missing"
    data_source = ingest(str(source))
    structural = build_structural_only(data_source)
    return golden, run_context_discovery_agent(data_source, structural, packs)


def test_every_industry_has_a_benchmark():
    """The suite is only meaningful if it spans more than the one dataset
    the agent was originally tuned against."""
    assert len(BENCHMARKS) >= 5, BENCHMARKS
    assert (EVAL_DIR / "_README.md").exists()


@pytest.mark.parametrize("name", BENCHMARKS)
def test_entities_are_identified_from_measured_uniqueness(name: str, packs):
    golden, context = _discover(name, packs)
    expected = golden["deterministic"]["entities"]

    found = {e.table: e for e in context.primary_entities}
    assert set(found) == set(expected), f"{name}: tables differ"
    for table, spec in expected.items():
        assert found[table].identifier_column == spec["identifier_column"], table
        assert found[table].is_unique_key == spec["is_unique_key"], table


@pytest.mark.parametrize("name", BENCHMARKS)
def test_question_budget_is_respected(name: str, packs):
    """Spec §13: an adaptive handful of high-impact questions, not a
    questionnaire. A review page nobody finishes is worth less than three
    questions they actually answer."""
    golden, context = _discover(name, packs)
    budget = golden["deterministic"]

    assert budget["min_questions"] <= len(context.open_questions) <= budget["max_questions"], [
        q.question_id for q in context.open_questions
    ]
    assert {q.category for q in context.open_questions} <= set(budget["question_categories"])
    # Every question must carry the evidence that provoked it (spec §15).
    for question in context.open_questions:
        assert question.evidence, question.question_id
        assert question.context, question.question_id
        assert question.why_asking, question.question_id


@pytest.mark.parametrize("name", BENCHMARKS)
def test_quality_issues_are_detected(name: str, packs):
    golden, context = _discover(name, packs)
    expected = set(golden["deterministic"]["expected_quality_issues"])
    found = {issue.code for issue in context.data_quality_issues}
    assert expected <= found, f"{name}: missing {expected - found}"


@pytest.mark.parametrize("name", BENCHMARKS)
def test_no_measure_is_assumed_additive(name: str, packs):
    """Additivity is a gate-verified claim (see the default-deny SUM in
    compiler/metric_generator.py). Being numeric is not evidence of it, in
    any industry - summing a lead score or an age is meaningless."""
    golden, context = _discover(name, packs)

    assert {m.column for m in context.important_measures} == set(
        golden["deterministic"]["non_additive_measures"]
    )
    assert all(not m.is_additive for m in context.important_measures)


@pytest.mark.parametrize("name", BENCHMARKS)
def test_no_business_facts_are_fabricated(name: str, packs):
    """Offline, the agent must abstain rather than invent. A fabricated
    domain or funnel is indistinguishable downstream from a discovered one."""
    _golden, context = _discover(name, packs)

    assert context.domain is None
    assert context.domain_confidence == 0.0
    assert context.business_objective is None
    assert context.business_process is None
    assert context.success_definition is None
    assert context.candidate_kpis == []
    assert context.ready_for_downstream_pipeline is False


@pytest.mark.parametrize("name", BENCHMARKS)
def test_findings_reach_the_downstream_handoff(name: str, packs):
    """Spec §22. The whole artifact used to be produced and consumed by
    nothing; this asserts the payload downstream stages actually read is
    populated for every industry, not just the one that was hand-tuned."""
    _golden, context = _discover(name, packs)
    handoff = context.to_handoff()

    assert handoff["primary_entities"], name
    assert handoff["hypotheses"], name
    assert handoff["unresolved_questions"], name
    # Facts and hypotheses stay distinguishable all the way through.
    assert handoff["confirmed_facts"] == []


@pytest.mark.parametrize("name", BENCHMARKS)
def test_questions_reach_the_human_review_page(name: str, packs):
    _golden, context = _discover(name, packs)
    review = DataReview(generated_at="now")

    added = merge_context_questions_into_review(review, context)

    assert 0 < added <= MAX_CONTEXT_QUESTIONS
    for question in review.questions:
        assert question.kind == "business_context"
        # A choice question must offer real observed values, never invented ones.
        if question.answer_type in ("single_choice", "multi_choice"):
            assert question.choices, question.id
