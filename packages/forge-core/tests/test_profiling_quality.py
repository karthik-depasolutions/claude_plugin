from __future__ import annotations

from pathlib import Path

from forge_core.ingestion.registry import ingest
from forge_core.llm.provider import LLMError
from forge_core.models.quality import QualityFinding, ValueCount
from forge_core.models.schema_profile import ColumnSemantic, SemanticProfile
from forge_core.profiling import build_structural_only
from forge_core.profiling.quality import (
    GENERAL_NOTES_ID,
    MAX_QUESTIONS,
    analyze_quality,
    build_data_review,
    generate_questions,
)
from forge_core.runtime_session import open_session


def _analyze(source_path: Path):
    ds = ingest(source_path)
    structural = build_structural_only(ds)
    con = open_session(ds)
    try:
        return analyze_quality(ds, structural, con)
    finally:
        con.close()


def test_dirty_leads_reproduces_every_check_code(dirty_leads_csv: Path):
    """dirty_leads.csv is hand-built with one exact-percentage example of
    every check this analyzer implements - the same shape (case-inconsistent
    categoricals, a column mixing labels with raw numbers, a dominant
    campaign/outcome value, a mostly-null column) that motivated this
    feature after a real post-install analysis found them by hand."""
    findings, skipped = _analyze(dirty_leads_csv)

    assert skipped == []
    codes_by_column = {f.column: f.code for f in findings}
    assert codes_by_column["age_group"] == "mixed_types"
    assert codes_by_column["gender"] == "inconsistent_format"
    assert codes_by_column["status_flag"] == "single_value"

    campaign = next(f for f in findings if f.column == "campaign_id")
    assert campaign.code == "dominant_value"
    assert campaign.top_values[0].value == "camp-main"
    assert campaign.top_values[0].percent == 94.0

    outcome = next(f for f in findings if f.column == "outcome_of_the_call")
    assert outcome.code == "dominant_value"
    assert outcome.top_values[0].percent == 85.0

    sentiment_findings = {f.code for f in findings if f.column == "user_sentiment"}
    assert "high_null" in sentiment_findings  # 60% empty
    assert "single_value" in sentiment_findings  # the 40% that isn't is all "neutral"


def test_low_cardinality_id_named_column_is_still_analyzed(dirty_leads_csv: Path):
    """campaign_id matches the "*_id" naming heuristic (is_likely_identifier),
    but with only 2 distinct values it is a dimension, not a row identifier -
    the eligibility gate must key off cardinality, not the name-based flag,
    or this exact finding (the one that motivated the feature) never fires."""
    findings, _ = _analyze(dirty_leads_csv)
    assert any(f.column == "campaign_id" for f in findings)


def test_true_high_cardinality_identifier_is_excluded(dirty_leads_csv: Path):
    """lead_id is 1:1 with rows (cardinality 100) - the cardinality cap alone
    must exclude it without needing the name-based identifier flag."""
    findings, _ = _analyze(dirty_leads_csv)
    assert not any(f.column == "lead_id" for f in findings)


def test_findings_are_deterministic_across_independent_runs(dirty_leads_csv: Path):
    """Finding ids and their order must be byte-identical across two
    independent runs - a pipeline resume must never regenerate DataReview,
    but if it ever did, this is what would keep answers keyed correctly."""
    first, first_skipped = _analyze(dirty_leads_csv)
    second, second_skipped = _analyze(dirty_leads_csv)

    assert [f.model_dump_json() for f in first] == [f.model_dump_json() for f in second]
    assert first_skipped == second_skipped


def test_finding_ids_are_stable_and_never_llm_assigned(dirty_leads_csv: Path):
    """Every id is a pure function of code + table + column - see
    models/quality.py's docstring on why this is what keeps a resumed run's
    answers keyed correctly even if questions were ever regenerated."""
    findings, _ = _analyze(dirty_leads_csv)
    for f in findings:
        assert f.id == f"{f.code}:{f.table}.{f.column}"


def test_clean_dataset_produces_few_findings(bookings_csv: Path):
    """A well-formed, hand-curated fixture shouldn't light up like a
    dashboard - sanity check against over-firing on ordinary data."""
    findings, skipped = _analyze(bookings_csv)
    assert skipped == []
    high_severity = [f for f in findings if f.severity == "high"]
    assert high_severity == []


class _StubQuestionProvider:
    """Mirrors test_validation_harness.py's _StubCritiqueProvider - a fake
    LLMProvider returning a canned generate_json response."""

    def __init__(self, response: dict) -> None:
        self._response = response

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
        return self._response

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return ""


class _RaisingProvider:
    def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
        raise LLMError("boom")

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return ""


def _finding(code: str, column: str, table: str = "t") -> QualityFinding:
    return QualityFinding(
        id=f"{code}:{table}.{column}",
        code=code,
        severity="medium",
        table=table,
        column=column,
        summary=f"deterministic summary for {column}",
        top_values=[ValueCount(value="x", count=1, percent=100.0)],
    )


def test_generate_questions_without_a_provider_uses_deterministic_fallback():
    """replay mode / no GEMINI_API_KEY / --no-llm all reach this same path -
    must produce a grounded, still-useful question, never an error."""
    finding = _finding("dominant_value", "outcome")
    questions = generate_questions([finding], provider=None)

    assert len(questions) == 2  # the finding + general_notes
    assert questions[0].id == finding.id
    assert "outcome" in questions[0].question
    assert questions[0].context == finding.summary
    assert questions[1].id == GENERAL_NOTES_ID


def test_generate_questions_with_no_findings_asks_nothing():
    """A clean dataset must not get a lone 'anything else?' question - that
    would pause a run with nothing wrong, violating 'pause only when there's
    something to say'."""
    assert generate_questions([], provider=None) == []


def test_generate_questions_llm_error_degrades_to_fallback():
    """The same try/except LLMError -> template convention as
    skills.py/agents.py/commands.py - never semantic.py's hard-fail."""
    finding = _finding("high_null", "sentiment")
    questions = generate_questions([finding], provider=_RaisingProvider())
    assert questions[0].id == finding.id
    assert "sentiment" in questions[0].question


def test_generate_questions_grounds_to_real_finding_ids_only():
    """The LLM writes text, never an id - an invented finding_id must be
    dropped, and a finding the model silently skips must still get its
    deterministic fallback rather than being omitted."""
    answered = _finding("dominant_value", "outcome")
    skipped = _finding("high_null", "sentiment")
    provider = _StubQuestionProvider(
        {
            "questions": [
                {"finding_id": answered.id, "question": "What does the dominant outcome value mean?"},
                {"finding_id": "made_up_id_the_model_invented", "question": "Should never appear."},
            ]
        }
    )

    questions = generate_questions([answered, skipped], provider)

    by_id = {q.id: q.question for q in questions}
    assert by_id[answered.id] == "What does the dominant outcome value mean?"
    assert "sentiment" in by_id[skipped.id]  # fell back, not omitted
    assert "made_up_id_the_model_invented" not in by_id


def test_generate_questions_caps_at_max_questions_plus_general_notes():
    findings = [_finding("dominant_value", f"col_{i}") for i in range(MAX_QUESTIONS + 3)]
    questions = generate_questions(findings, provider=None)
    assert len(questions) == MAX_QUESTIONS + 1  # + general_notes
    assert questions[-1].id == GENERAL_NOTES_ID


def test_build_data_review_end_to_end_with_no_provider(dirty_leads_csv: Path):
    """The orchestrator's actual call shape (findings + no provider = no
    LLM questions, but the review still exists and is well-formed)."""
    ds = ingest(dirty_leads_csv)
    structural = build_structural_only(ds)
    con = open_session(ds)
    try:
        review = build_data_review(ds, structural, con, provider=None)
    finally:
        con.close()

    assert review.findings
    assert review.questions == []


def test_low_confidence_column_semantics_becomes_a_finding(dirty_leads_csv: Path):
    """A low-confidence ColumnSemantic - from either the single-shot profiler
    or the data-understanding agent - surfaces as an `unclear_meaning`
    finding through the exact same path a deterministic anomaly does."""
    ds = ingest(dirty_leads_csv)
    structural = build_structural_only(ds)
    semantic = SemanticProfile(
        column_semantics=[
            ColumnSemantic(
                table="dirty_leads", column="user_sentiment", proposed_meaning="unclear", confidence=0.2
            )
        ]
    )
    con = open_session(ds)
    try:
        review = build_data_review(ds, structural, con, provider=None, semantic=semantic)
    finally:
        con.close()

    unclear = [f for f in review.findings if f.code == "unclear_meaning"]
    assert len(unclear) == 1
    assert unclear[0].id == "unclear_meaning:dirty_leads.user_sentiment"
    assert unclear[0].table == "dirty_leads"
    assert unclear[0].column == "user_sentiment"
    assert "unclear" in unclear[0].summary


def test_high_confidence_column_semantics_produces_no_finding(dirty_leads_csv: Path):
    ds = ingest(dirty_leads_csv)
    structural = build_structural_only(ds)
    semantic = SemanticProfile(
        column_semantics=[
            ColumnSemantic(
                table="dirty_leads", column="user_sentiment", proposed_meaning="clearly obvious", confidence=0.95
            )
        ]
    )
    con = open_session(ds)
    try:
        review = build_data_review(ds, structural, con, provider=None, semantic=semantic)
    finally:
        con.close()

    assert not [f for f in review.findings if f.code == "unclear_meaning"]


def test_unclear_meaning_findings_merge_and_stay_severity_sorted(dirty_leads_csv: Path):
    """Findings from the deterministic checks and from low-confidence
    semantics land in one combined, still-sorted list - not two lists."""
    ds = ingest(dirty_leads_csv)
    structural = build_structural_only(ds)
    semantic = SemanticProfile(
        column_semantics=[
            ColumnSemantic(table="dirty_leads", column="gender", proposed_meaning="?", confidence=0.1)
        ]
    )
    con = open_session(ds)
    try:
        with_semantic = build_data_review(ds, structural, con, provider=None, semantic=semantic)
        without_semantic = build_data_review(ds, structural, con, provider=None, semantic=None)
    finally:
        con.close()

    assert len(with_semantic.findings) == len(without_semantic.findings) + 1
    severities = [f.severity for f in with_semantic.findings]
    ranks = {"high": 0, "medium": 1, "low": 2}
    assert [ranks[s] for s in severities] == sorted(ranks[s] for s in severities)
