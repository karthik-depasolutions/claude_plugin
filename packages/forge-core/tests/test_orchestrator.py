from __future__ import annotations

from pathlib import Path

from forge_core.models.common import RunStatus
from forge_core.models.industry_pack import ClassificationResult, IndustryMatch
from forge_core.models.run import RunRecord
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline


class _QuestionProvider:
    """A stub provider for a run with no real LLM. generate_json returns a
    valid-but-empty semantic profile (so the profiling semantic pass accepts
    it), while the data-review question generator finds no "questions" key
    and falls back to the deterministic per-code templates - which is
    exactly how a real LLM-enabled run produces review questions."""

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
        return {
            "column_semantics": [],
            "candidate_insights": [],
            "data_quality_flags": [],
            "likely_central_entities": [],
        }

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return ""


def _new_record(source_path: Path, output_dir: Path, industry_override: str | None = None) -> RunRecord:
    return RunRecord(
        run_id="test-run",
        source_path=str(source_path),
        output_dir=str(output_dir),
        industry_override=industry_override,
    )


def _auto_accept(profile, packs):
    return ClassificationResult(
        ranked_matches=[IndustryMatch(pack_slug="generic-analytics", confidence=0.9)],
        primary_pack_slug="generic-analytics",
        requires_customer_confirmation=False,
    )


def _require_confirmation(profile, packs):
    return ClassificationResult(
        ranked_matches=[IndustryMatch(pack_slug="generic-analytics", confidence=0.1)],
        primary_pack_slug="generic-analytics",
        requires_customer_confirmation=True,
    )


def _pause_flags(record: RunRecord) -> dict:
    return record.events[-1].data


def test_pipeline_succeeds_end_to_end_for_a_well_matched_dataset(bookings_csv: Path, tmp_path: Path):
    record = _new_record(bookings_csv, tmp_path)

    result = run_pipeline(record)

    assert result.status == RunStatus.SUCCEEDED, result.error
    stages = [e.stage.value for e in result.events]
    assert stages[0] == "ingest"
    assert "package" in stages
    assert "validate" in stages
    plugin_dirs = list(tmp_path.iterdir())
    assert len(plugin_dirs) == 1
    assert (plugin_dirs[0] / ".claude-plugin" / "plugin.json").is_file()


def test_pipeline_pauses_for_low_confidence_classification(bookings_csv: Path, tmp_path: Path, monkeypatch):
    import forge_core.orchestrator as orch

    monkeypatch.setattr(orch, "classify", _require_confirmation)
    record = _new_record(bookings_csv, tmp_path)

    result = run_pipeline(record)

    assert result.status == RunStatus.NEEDS_INPUT
    assert result.current_stage.value == "classify"
    flags = _pause_flags(result)
    assert flags["needs_industry"] is True
    assert flags["needs_answers"] is False
    assert not list(tmp_path.iterdir())  # nothing packaged yet


def test_pipeline_resumes_after_industry_override(bookings_csv: Path, tmp_path: Path, monkeypatch):
    import forge_core.orchestrator as orch

    monkeypatch.setattr(orch, "classify", _require_confirmation)
    record = _new_record(bookings_csv, tmp_path)
    run_pipeline(record)
    assert record.status == RunStatus.NEEDS_INPUT

    record.industry_override = "generic-analytics"
    result = run_pipeline(record)

    assert result.status == RunStatus.SUCCEEDED, result.error


def test_pipeline_pauses_for_data_review_questions(dirty_leads_csv: Path, tmp_path: Path, monkeypatch):
    """A dirty dataset whose industry is auto-accepted must still pause on
    the data review - but only when a question provider is available (the
    LLM-enabled API path). needs_industry stays false."""
    import forge_core.orchestrator as orch

    monkeypatch.setattr(orch, "classify", _auto_accept)
    record = _new_record(dirty_leads_csv, tmp_path)

    result = run_pipeline(record, profiling_provider=_QuestionProvider())

    assert result.status == RunStatus.NEEDS_INPUT
    flags = _pause_flags(result)
    assert flags["needs_answers"] is True
    assert flags["needs_industry"] is False
    assert not list(tmp_path.iterdir())


def test_pipeline_resumes_after_data_review_answers(dirty_leads_csv: Path, tmp_path: Path, monkeypatch):
    """Setting data_answers (even to {}) declares the review answered and
    must stop the pause re-firing - mirroring industry_override."""
    import forge_core.orchestrator as orch

    monkeypatch.setattr(orch, "classify", _auto_accept)
    record = _new_record(dirty_leads_csv, tmp_path)
    run_pipeline(record, profiling_provider=_QuestionProvider())
    assert record.status == RunStatus.NEEDS_INPUT

    record.data_answers = {}
    result = run_pipeline(record, profiling_provider=_QuestionProvider())

    assert result.status == RunStatus.SUCCEEDED, result.error


def test_pipeline_does_not_pause_without_a_question_provider(dirty_leads_csv: Path, tmp_path: Path, monkeypatch):
    """The CLI default (data_answers={}, no LLM provider) computes and prints
    findings but never pauses on them - 'existing scripts unaffected'."""
    import forge_core.orchestrator as orch

    monkeypatch.setattr(orch, "classify", _auto_accept)
    record = _new_record(dirty_leads_csv, tmp_path)

    result = run_pipeline(record)

    assert result.status == RunStatus.SUCCEEDED, result.error
    review_event = next(e for e in result.events if "review" in e.data)
    assert review_event.data["review"]["findings"]


def test_nonempty_answers_thread_notes_with_provider(dirty_leads_csv: Path, tmp_path: Path, monkeypatch):
    """The CLI --answers path re-runs from scratch with a provider available
    (use_llm defaults to on). Questions are regenerated - fallback templates
    are deterministic - so to_context can join each answer back to its
    question text, and the notes land in the packaged schema_summary.json."""
    import json

    import forge_core.orchestrator as orch

    monkeypatch.setattr(orch, "classify", _auto_accept)
    record = _new_record(dirty_leads_csv, tmp_path)
    record.data_answers = {
        "general_notes": "lead-gen attribution data",
        "mixed_types:dirty_leads.age_group": "age buckets are campaign targets",
    }

    result = run_pipeline(record, profiling_provider=_QuestionProvider())

    assert result.status == RunStatus.SUCCEEDED, result.error
    plugin_dir = next(tmp_path.iterdir())
    summary = json.loads(
        (plugin_dir / "config" / "schema_summary.json").read_text(encoding="utf-8")
    )
    notes = summary["data_context"]["notes"]
    assert {"question": "Anything else about this data an analyst should know?", "answer": "lead-gen attribution data"} in notes
    assert any(n["answer"] == "age buckets are campaign targets" for n in notes)


def test_default_packs_root_exists():
    assert DEFAULT_PACKS_ROOT.is_dir()
    assert (DEFAULT_PACKS_ROOT / "healthcare-diagnostics" / "pack.json").is_file()


def test_use_agent_threads_column_semantics_and_industry_guess(bookings_csv: Path, tmp_path: Path, monkeypatch):
    """use_agent=True routes semantic profiling through the data-understanding
    agent (stubbed here - no real LLM/LangChain call) instead of the
    single-shot call. Its output must reach both places it's designed to:
    a low-confidence column becomes an unclear_meaning finding, and the
    industry guess rides the classify event next to the deterministic
    ranking."""
    import forge_core.agentic.data_agent as data_agent_module
    import forge_core.orchestrator as orch
    from forge_core.models.industry_pack import IndustryMatch
    from forge_core.models.schema_profile import ColumnSemantic, IndustryGuess

    def _stub_agent(data_source, structural, packs, **kwargs):
        return (
            [ColumnSemantic(table="bookings", column="status", proposed_meaning="unsure", confidence=0.1)],
            IndustryGuess(pack_slug_guess="generic-analytics", confidence=0.7, reasoning="stubbed reasoning"),
        )

    monkeypatch.setattr(data_agent_module, "run_data_understanding_agent", _stub_agent)
    monkeypatch.setattr(orch, "classify", _auto_accept)

    record = _new_record(bookings_csv, tmp_path)
    result = run_pipeline(
        record,
        profiling_provider=_QuestionProvider(),
        use_agent=True,
    )

    classify_events = [e for e in result.events if e.stage.value == "classify" and "suggested_industry" in e.data]
    assert classify_events, result.events
    assert classify_events[0].data["suggested_industry"] == {
        "pack_slug_guess": "generic-analytics",
        "confidence": 0.7,
        "reasoning": "stubbed reasoning",
    }

    review_event = next(e for e in result.events if "review" in e.data)
    unclear = [f for f in review_event.data["review"]["findings"] if f["code"] == "unclear_meaning"]
    assert unclear == [
        {
            "id": "unclear_meaning:bookings.status",
            "code": "unclear_meaning",
            "severity": "medium",
            "table": "bookings",
            "column": "status",
            "summary": 'Not confident what "status" means - best guess: "unsure" (10% confidence).',
            "top_values": [],
        }
    ]


def test_use_agent_proposed_kpis_compile_or_skip_with_a_reason(bookings_csv: Path, tmp_path: Path, monkeypatch):
    """A good agent-proposed KPI candidate compiles and merges into
    kpi_defs.kpis (tagged agent_proposed); a candidate referencing an
    undefined token fails the exact same compile_kpi gate a bad
    hand-authored pack KPI would, landing in .skipped with a reason -
    never breaking the run."""
    import forge_core.agentic.data_agent as data_agent_module
    import forge_core.orchestrator as orch
    from forge_core.models.industry_pack import CanonicalKpi, RequiredRoles

    # Keeps this test offline - use_agent=True also reaches the (unrelated)
    # data-understanding agent during PROFILE, which otherwise makes a real
    # LangChain/Gemini call.
    monkeypatch.setattr(data_agent_module, "run_data_understanding_agent", lambda *a, **k: ([], None))

    good = CanonicalKpi(
        id="agent_total_rows",
        label="Agent Total Rows",
        description="Row count proposed by the agent.",
        formula_plain_english="Count of rows.",
        grain="one row per record",
        requires=RequiredRoles(),
        sql="SELECT COUNT(*) AS total FROM {{fact}}",
    )
    bad = CanonicalKpi(
        id="agent_bad_kpi",
        label="Agent Bad KPI",
        description="References a role that was never bound.",
        formula_plain_english="Nonsense.",
        grain="n/a",
        requires=RequiredRoles(measures=["totally_made_up_role"]),
        sql="SELECT {{totally_made_up_role}} FROM {{fact}}",
    )

    monkeypatch.setattr(orch, "classify", _auto_accept)
    monkeypatch.setattr(orch, "propose_kpis", lambda *a, **k: [good, bad])

    record = _new_record(bookings_csv, tmp_path)
    record.data_answers = {}  # declares "reviewed, nothing supplied" - skips the review pause
    result = run_pipeline(
        record,
        profiling_provider=_QuestionProvider(),
        generation_provider=_QuestionProvider(),
        use_agent=True,
    )

    compile_event = next(e for e in result.events if "agent_proposed" in e.data)
    assert compile_event.data["agent_proposed"] == ["agent_total_rows"]
    assert "agent_bad_kpi" in compile_event.data["skipped"]
    assert compile_event.data["skipped"]["agent_bad_kpi"]  # non-empty reason string
