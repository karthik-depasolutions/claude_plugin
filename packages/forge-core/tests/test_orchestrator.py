from __future__ import annotations

from pathlib import Path

from forge_core.models.common import RunStatus
from forge_core.models.industry_pack import ClassificationResult, IndustryMatch
from forge_core.models.run import RunRecord
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline


def _new_record(source_path: Path, output_dir: Path, industry_override: str | None = None) -> RunRecord:
    return RunRecord(
        run_id="test-run",
        source_path=str(source_path),
        output_dir=str(output_dir),
        industry_override=industry_override,
    )


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

    def _fake_classify(profile, packs):
        return ClassificationResult(
            ranked_matches=[IndustryMatch(pack_slug="generic-analytics", confidence=0.1)],
            primary_pack_slug="generic-analytics",
            requires_customer_confirmation=True,
        )

    monkeypatch.setattr(orch, "classify", _fake_classify)
    record = _new_record(bookings_csv, tmp_path)

    result = run_pipeline(record)

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
    run_pipeline(record)
    assert record.status == RunStatus.NEEDS_INPUT

    record.industry_override = "healthcare-diagnostics"
    result = run_pipeline(record)

    assert result.status == RunStatus.SUCCEEDED, result.error


def test_default_packs_root_exists():
    assert DEFAULT_PACKS_ROOT.is_dir()
    assert (DEFAULT_PACKS_ROOT / "healthcare-diagnostics" / "pack.json").is_file()
