from __future__ import annotations

from pathlib import Path

from forge_core.models.common import RunStatus
from forge_core.models.industry_pack import ClassificationResult, IndustryMatch
from forge_core.models.run import RunRecord
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline
from forge_core.testing import FakeLLMProvider


def _new_record(source_path: Path, output_dir: Path, industry_override: str | None = None) -> RunRecord:
    return RunRecord(
        run_id="test-run",
        source_path=str(source_path),
        output_dir=str(output_dir),
        industry_override=industry_override,
    )


def _run(record: RunRecord, *, answer_questions: bool = True, provider=None, **kwargs) -> RunRecord:
    llm = provider or FakeLLMProvider()
    if answer_questions and record.data_answers is None:
        record.data_answers = {}  # skip the pre-synthesis clarification pause
    return run_pipeline(
        record,
        profiling_provider=llm,
        generation_provider=llm,
        critique_provider=llm,
        **kwargs,
    )


def test_pipeline_succeeds_end_to_end_for_a_well_matched_dataset(bookings_csv: Path, tmp_path: Path):
    record = _new_record(bookings_csv, tmp_path)

    result = _run(record)

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

    def _fake_classify(profile, packs):
        return ClassificationResult(
            ranked_matches=[IndustryMatch(pack_slug="generic-analytics", confidence=0.1)],
            primary_pack_slug="generic-analytics",
            requires_customer_confirmation=True,
        )

    monkeypatch.setattr(orch, "classify", _fake_classify)
    record = _new_record(bookings_csv, tmp_path)

    result = _run(record)

    assert result.status == RunStatus.NEEDS_INPUT
    assert result.current_stage.value == "classify"
    assert not list(tmp_path.iterdir())  # nothing packaged yet


def test_pipeline_resumes_after_industry_override(bookings_csv: Path, tmp_path: Path, monkeypatch):
    import forge_core.orchestrator as orch

    def _fake_classify(profile, packs):
        return ClassificationResult(
            ranked_matches=[IndustryMatch(pack_slug="healthcare-diagnostics", confidence=0.1)],
            primary_pack_slug="healthcare-diagnostics",
            requires_customer_confirmation=True,
        )

    monkeypatch.setattr(orch, "classify", _fake_classify)
    record = _new_record(bookings_csv, tmp_path)
    _run(record)
    assert record.status == RunStatus.NEEDS_INPUT

    record.industry_override = "healthcare-diagnostics"
    result = _run(record)

    assert result.status == RunStatus.SUCCEEDED, result.error


class _QuestioningProvider(FakeLLMProvider):
    """FakeLLMProvider that asks a business-context clarification question."""

    seen_answer: str = ""

    def generate_json(self, prompt, *, system=None):
        low = prompt.lower()
        if '"slug"' in low and "value sets" in low:
            return {"questions": [{"slug": "grain-bookings", "question": "What is one booking row?"}]}
        if "user clarifications" in low:  # a synthesis prompt that carried the answer through
            type(self).seen_answer = prompt
        return super().generate_json(prompt, system=system)


def test_pipeline_pauses_for_data_clarifications_then_resumes(bookings_csv: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGE_SCHEMA_MODEL_CACHE_DIR", str(tmp_path / "smcache"))  # isolate the synthesis cache
    _QuestioningProvider.seen_answer = ""
    record = _new_record(bookings_csv, tmp_path, industry_override="healthcare-diagnostics")

    paused = _run(record, answer_questions=False, provider=_QuestioningProvider())

    assert paused.status == RunStatus.NEEDS_INPUT
    assert paused.current_stage.value == "profile"
    q_event = next(e for e in reversed(paused.events) if e.stage.value == "profile" and e.data.get("questions"))
    assert any(q["kind"] == "business" for q in q_event.data["questions"])
    assert not list(tmp_path.iterdir())  # nothing packaged while paused

    record.data_answers = {"biz:grain-bookings": "one row per completed lab-test booking"}
    result = _run(record, answer_questions=False, provider=_QuestioningProvider())

    assert result.status == RunStatus.SUCCEEDED, result.error
    assert "one row per completed lab-test booking" in _QuestioningProvider.seen_answer


def test_default_packs_root_exists():
    assert DEFAULT_PACKS_ROOT.is_dir()
    assert (DEFAULT_PACKS_ROOT / "healthcare-diagnostics" / "pack.json").is_file()
