"""Spec §22's handoff contract: downstream stages consume the Context
Discovery Agent's findings instead of re-deriving business context.

Before this, `BusinessContext` was produced, persisted, rendered - and read
by nothing. Its questions were never shown to anyone, its domain claim was
thrown away, and classification/binding/generation each re-derived what the
agent had already investigated. These tests pin the wires so that can't
silently regress into a dead end again.
"""

from __future__ import annotations

from pathlib import Path

from forge_core.agentic.agents.context_discovery import (
    answers_to_business_answers,
    merge_context_questions_into_review,
    run_context_discovery_agent,
)
from forge_core.agentic.schemas.business_context import (
    BusinessContext,
    BusinessQuestion,
    Evidence,
    Hypothesis,
    SuccessDefinition,
)
from forge_core.classification import load_all_packs
from forge_core.classification.matcher import classify
from forge_core.ingestion.registry import ingest
from forge_core.models.quality import DataQuestion, DataReview, render_data_context
from forge_core.models.schema_profile import SchemaProfile
from forge_core.profiling import build_structural_only

PACKS_DIR = Path(__file__).resolve().parents[3] / "industry-packs"


def _question(qid: str, impact: str, column: str, category: str = "field_semantics", options=None):
    return BusinessQuestion(
        question_id=qid,
        category=category,
        question=f"Question about {column}?",
        context="ctx",
        evidence=[Evidence(type="value_set", source=f"t.{column}", observation="obs")],
        options=options or [],
        impact=impact,
        why_asking="because",
    )


# --- Handoff payload -------------------------------------------------


def test_handoff_keeps_confirmed_facts_and_hypotheses_apart():
    """Collapsing them is the "pretend an inference is confirmed fact"
    failure the spec forbids - a prompt that can't tell "the owner told us"
    from "we suspect" treats both as settled."""
    context = BusinessContext(
        confirmed_facts=[Evidence(type="customer_confirmation", source="answer:q1", observation="Owner said X")],
        inferred_hypotheses=[
            Hypothesis(id="h1", category="record_grain", claim="We suspect Y", confidence=0.8)
        ],
    )

    handoff = context.to_handoff()

    assert handoff["confirmed_facts"] == [{"source": "answer:q1", "observation": "Owner said X"}]
    assert handoff["hypotheses"][0]["claim"] == "We suspect Y"
    assert "We suspect Y" not in str(handoff["confirmed_facts"])


def test_handoff_omits_business_facts_that_were_never_established():
    context = BusinessContext()
    handoff = context.to_handoff()

    assert handoff["domain"] is None
    assert handoff["record_grain"] is None
    assert "business_objective" not in handoff
    assert "success_definition" not in handoff


def test_handoff_carries_a_confirmed_success_definition():
    context = BusinessContext(
        success_definition=SuccessDefinition(
            conversion_event="Enrolled",
            criteria="status = 'enrolled'",
            qualifying_columns=["status"],
            qualifying_values=["enrolled"],
        )
    )
    handoff = context.to_handoff()
    assert handoff["success_definition"]["conversion_event"] == "Enrolled"
    assert handoff["success_definition"]["qualifying_values"] == ["enrolled"]


# --- Rendering into prompts ------------------------------------------


def test_business_context_reaches_the_prompt_with_certainty_preserved():
    """This is the payload binding/generation/packaging actually read. An
    unconfirmed hypothesis must be labelled as one all the way through."""
    context = BusinessContext(
        record_grain="One row per call attempt",
        confirmed_facts=[Evidence(type="customer_confirmation", source="answer:q1", observation="Rows are calls")],
        inferred_hypotheses=[Hypothesis(id="h", category="kpi", claim="score is a rating", confidence=0.6)],
        open_questions=[_question("q_unknown", "critical", "amount")],
    )

    rendered = render_data_context({"business_context": context.to_handoff()}, cap=4000)

    assert "One row per call attempt" in rendered
    assert "CONFIRMED by the owner: Rows are calls" in rendered
    assert "do not state as fact" in rendered
    assert "score is a rating" in rendered
    assert "do not invent an answer" in rendered


def test_render_is_still_empty_without_any_context():
    assert render_data_context({}) == ""
    assert render_data_context(None) == ""


# --- Classification consumes the domain ------------------------------


def _profile(tmp_path: Path) -> SchemaProfile:
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("a,b\n1,x\n2,y\n3,z\n", encoding="utf-8")
    ds = ingest(str(csv_path))
    return SchemaProfile(data_source_id=ds.id, structural=build_structural_only(ds), semantic=None, source=ds)


def test_classification_uses_the_agents_domain_as_evidence(tmp_path: Path):
    profile = _profile(tmp_path)
    packs = load_all_packs(PACKS_DIR)
    target = "edtech"

    baseline = classify(profile, packs)
    baseline_score = next(m.confidence for m in baseline.ranked_matches if m.pack_slug == target)

    boosted = classify(
        profile,
        packs,
        business_context={"domain": target, "domain_confidence": 0.92, "record_grain": "one enrollment"},
    )
    match = next(m for m in boosted.ranked_matches if m.pack_slug == target)

    assert match.confidence > baseline_score
    assert any("Context Discovery Agent" in s for s in match.matched_signals)


def test_classification_ignores_a_domain_that_is_not_a_real_pack(tmp_path: Path):
    profile = _profile(tmp_path)
    packs = load_all_packs(PACKS_DIR)

    result = classify(
        profile, packs, business_context={"domain": "not-a-pack", "domain_confidence": 0.99}
    )

    assert result.primary_pack_slug in {p.slug for p in packs}


def test_classification_never_boosts_on_an_abstained_domain(tmp_path: Path):
    """Offline the agent reports `domain=None`; nothing may be inferred from
    that silence."""
    profile = _profile(tmp_path)
    packs = load_all_packs(PACKS_DIR)

    baseline = classify(profile, packs)
    abstained = classify(profile, packs, business_context={"domain": None, "domain_confidence": 0.0})

    assert [(m.pack_slug, m.confidence) for m in abstained.ranked_matches] == [
        (m.pack_slug, m.confidence) for m in baseline.ranked_matches
    ]


# --- Questions reach the human ---------------------------------------


def test_agent_questions_are_surfaced_on_the_review_page():
    review = DataReview(generated_at="now")
    context = BusinessContext(
        open_questions=[
            _question("q_low", "low", "c1"),
            _question("q_crit", "critical", "c2", category="success_definition", options=["won", "lost"]),
            _question("q_med", "medium", "c3"),
        ]
    )

    added = merge_context_questions_into_review(review, context)

    assert added == 3
    ids = [q.id for q in review.questions]
    # Highest impact first - the interview is ordered by what matters.
    assert ids[0] == "ctx:q_crit"
    critical = review.questions[0]
    assert critical.kind == "business_context"
    assert critical.answer_type == "multi_choice"
    assert critical.choices == ["won", "lost"]


def test_merge_is_capped_and_ordered_by_impact():
    """Spec §13: "Do not ask 20-30 questions at once."""
    review = DataReview(generated_at="now")
    context = BusinessContext(
        open_questions=[_question(f"q{i}", "medium", f"c{i}") for i in range(10)]
        + [_question("q_crit", "critical", "important")]
    )

    added = merge_context_questions_into_review(review, context, max_questions=3)

    assert added == 3
    assert review.questions[0].id == "ctx:q_crit"


def test_merge_does_not_ask_twice_about_the_same_column():
    """`generate_business_context_questions` already ids its questions
    "biz:{column}"; two phrasings of the same question reads as a broken form."""
    review = DataReview(generated_at="now")
    review.questions.append(DataQuestion(id="biz:status", question="Which status means done?"))
    context = BusinessContext(open_questions=[_question("q_outcome_status", "critical", "status")])

    added = merge_context_questions_into_review(review, context)

    assert added == 0
    assert len(review.questions) == 1


def test_merge_is_idempotent_across_a_resume():
    """A resumed run re-executes this stage; the review must not grow a
    duplicate copy of every question each time."""
    review = DataReview(generated_at="now")
    context = BusinessContext(open_questions=[_question("q1", "critical", "c1")])

    assert merge_context_questions_into_review(review, context) == 1
    assert merge_context_questions_into_review(review, context) == 0
    assert len(review.questions) == 1


# --- Answers feed back into the agent --------------------------------


def test_answers_round_trip_back_into_the_agents_own_type():
    answers = {
        "ctx:q_outcome": "won, closed",
        "biz:other": "not this agent's question",
        "ctx:q_empty": "   ",
    }

    business_answers = answers_to_business_answers(answers)

    assert len(business_answers) == 1
    answer = business_answers[0]
    assert answer.question_id == "q_outcome"
    assert answer.selected_options == ["won", "closed"]


def test_answering_a_question_resolves_it_and_raises_confidence(tmp_path: Path):
    """The adaptive loop end to end: an answered question stops being open,
    becomes a confirmed fact, and moves the readiness gate."""
    csv_path = tmp_path / "leads.csv"
    csv_path.write_text(
        "ref,state\n1,Won\n2,Lost\n1,Won\n3,won\n4,Lost\n5,Pending\n",
        encoding="utf-8",
    )
    ds = ingest(str(csv_path))
    structural = build_structural_only(ds)
    packs = load_all_packs(PACKS_DIR)

    before = run_context_discovery_agent(ds, structural, packs)
    assert before.open_questions
    assert before.ready_for_downstream_pipeline is False

    review = DataReview(generated_at="now")
    merge_context_questions_into_review(review, before)
    answered = {q.id: (q.choices[0] if q.choices else "yes") for q in review.questions}

    after = run_context_discovery_agent(
        ds, structural, packs, answers=answers_to_business_answers(answered)
    )

    assert len(after.open_questions) < len(before.open_questions)
    assert after.confirmed_facts
    assert after.overall_confidence > before.overall_confidence


# --- Review page budget ----------------------------------------------


def test_review_page_is_capped_across_every_question_source():
    """Three generators feed this page (anomaly, value-set, and this agent),
    each with its own cap and none aware of the others. On a real 26-column
    upload that summed to thirteen questions - the owner answered all of
    them, and the value of the last few was ~0."""
    from forge_core.agentic.agents.context_discovery import MAX_REVIEW_QUESTIONS
    from forge_core.profiling.quality import GENERAL_NOTES_ID

    review = DataReview(generated_at="now")
    # What the deterministic side produces on a messy dataset.
    review.questions += [
        DataQuestion(id=f"biz:col{i}", question=f"biz {i}", kind="business_context") for i in range(2)
    ]
    review.questions += [
        DataQuestion(id=f"dominant_value:t.col{i}", question=f"anomaly {i}") for i in range(3)
    ]
    review.questions.append(DataQuestion(id=GENERAL_NOTES_ID, question="Anything else?"))

    context = BusinessContext(
        open_questions=[_question(f"q{i}", "critical", f"ctx{i}") for i in range(4)]
    )
    merge_context_questions_into_review(review, context)

    assert len(review.questions) <= MAX_REVIEW_QUESTIONS

    kept = {q.id for q in review.questions}
    # The agent's questions resolve meaning, so they survive the trim...
    assert sum(1 for q in kept if q.startswith("ctx:")) == 4
    # ...and the open-ended catch-all is the first thing dropped.
    assert GENERAL_NOTES_ID not in kept


def test_trim_preserves_display_order():
    """Trimming reorders nothing the owner sees - it only removes."""
    review = DataReview(generated_at="now")
    review.questions += [
        DataQuestion(id=f"biz:col{i}", question=f"biz {i}", kind="business_context") for i in range(6)
    ]
    context = BusinessContext(open_questions=[_question("qa", "critical", "z")])

    merge_context_questions_into_review(review, context)

    ids = [q.id for q in review.questions]
    biz_order = [i for i in ids if i.startswith("biz:")]
    assert biz_order == sorted(biz_order, key=lambda i: int(i.removeprefix("biz:col")))
