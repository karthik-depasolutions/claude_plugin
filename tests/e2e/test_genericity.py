"""The acceptance test for "we built a generator, not a plugin": feed the
pipeline a dataset shaped nothing like the golden curelo-bookings fixture -
three joined tables, retail vocabulary, different column names - with zero
customer-specific code, and assert it still produces an installable,
fully-validated plugin with every KPI computing. Also covers a second,
independently-shaped source (SQLite, edtech vocabulary) for good measure.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from forge_core.models.common import CheckStatus, RunStatus
from forge_core.models.run import RunRecord
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline


@pytest.mark.parametrize(
    ("source_fixture", "expected_pack", "run_id"),
    [
        ("retail_orders_dir", "retail-ecommerce", "genericity-retail"),
        ("edtech_sqlite", "edtech", "genericity-edtech"),
    ],
)
def test_generator_produces_a_valid_plugin_for_an_unseen_shape(
    source_fixture: str, expected_pack: str, run_id: str, tmp_path: Path, request: pytest.FixtureRequest
):
    source_path: Path = request.getfixturevalue(source_fixture)
    record = RunRecord(run_id=run_id, source_path=str(source_path), output_dir=str(tmp_path))

    result = run_pipeline(record, packs_root=DEFAULT_PACKS_ROOT)

    assert result.status == RunStatus.SUCCEEDED, result.error

    classify_event = next(
        e for e in result.events if e.stage.value == "classify" and "ranked_matches" in e.data
    )
    assert classify_event.data["ranked_matches"][0]["pack_slug"] == expected_pack

    bind_event = next(e for e in reversed(result.events) if e.stage.value == "bind")
    assert bind_event.data["unresolved_roles"] == []

    compile_event = next(e for e in result.events if e.stage.value == "compile_kpis" and "skipped" in e.data)
    assert compile_event.data["skipped"] == {}, "every KPI must compute for the acceptance criterion to hold"

    validate_event = next(e for e in reversed(result.events) if e.stage.value == "validate")
    report = validate_event.data["report"]
    assert report["overall"] in (CheckStatus.PASS.value, CheckStatus.WARN.value)
    checks_by_name = {c["check"]: c for c in report["checks"]}
    assert checks_by_name["cli_validate"]["status"] == CheckStatus.PASS.value, "claude plugin validate"
    assert checks_by_name["dry_run"]["status"] == CheckStatus.PASS.value, "every KPI must execute for real"

    package_event = next(e for e in reversed(result.events) if e.stage.value == "package")
    plugin_dir = Path(package_event.data["plugin_dir"])
    assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file()
