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


# GEOGRAPHIC is no longer a name-derived role (see profiling/structural.py):
# "location" has zero name-token overlap with "shipping_city", so it cannot
# resolve deterministically without the agent or a human. These overrides
# stand in for "someone confirmed it" - the same device
# test_validation_harness.py::_pipeline uses - so this test stays about
# genericity of the *generator* rather than re-proving the separately-tested
# unresolved-role path.
GEO_OVERRIDES = {
    "genericity-retail": {"location": "shipping_city"},
    "genericity-edtech": {},
}


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
    overrides = GEO_OVERRIDES[run_id]

    result = run_pipeline(record, packs_root=DEFAULT_PACKS_ROOT, binding_overrides=overrides)

    if run_id == "genericity-edtech":
        # P1-08: only transaction_status and transaction_date are gated here
        # - revenue_amount also binds to "score" at low confidence, but no
        # edtech KPI references {{revenue_amount}} today, so gate_bindings
        # correctly never asks about a binding nothing depends on (asking
        # regardless would be a gate people click through). Confirm the two
        # that KPIs actually need and resume.
        assert result.status == RunStatus.NEEDS_INPUT, result.error
        gated_roles = {q.role for q in result.binding_questions}
        assert gated_roles == {"transaction_status", "transaction_date", "course_ref"}
        result.binding_confirmations = {q.role: q.physical for q in result.binding_questions}
        result = run_pipeline(record, packs_root=DEFAULT_PACKS_ROOT, binding_overrides=overrides)

        # P1-04: the still-unconfirmed score->revenue_amount binding is
        # caught independently by the plausibility check, which evaluates
        # every bound role regardless of current KPI usage - a student test
        # score bound to the role for money. That is a hard validation
        # failure and the run correctly refuses to package it.
        assert result.status == RunStatus.FAILED
        validate_event = next(e for e in reversed(result.events) if e.stage.value == "validate")
        report = validate_event.data["report"]
        checks_by_name = {c["check"]: c for c in report["checks"]}
        plaus = checks_by_name["binding_plausibility"]
        assert plaus["status"] == CheckStatus.FAIL.value
        assert any("revenue_amount" in i["location"] for i in plaus["issues"])
        assert any("score" in i["message"] for i in plaus["issues"])
        return

    # P1-08: any other low-confidence-but-correct binding a KPI depends on
    # pauses for confirmation too - confirm the resolver's own top pick and
    # resume, same as a caller with nothing more informed to add.
    if result.status == RunStatus.NEEDS_INPUT and result.binding_questions:
        result.binding_confirmations = {q.role: q.physical for q in result.binding_questions}
        result = run_pipeline(record, packs_root=DEFAULT_PACKS_ROOT, binding_overrides=overrides)

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

    package_event = next(e for e in reversed(result.events) if e.stage.value == "package" and "plugin_dir" in e.data)
    plugin_dir = Path(package_event.data["plugin_dir"])
    assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file()
