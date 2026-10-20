"""The acceptance test for "we built a generator, not a plugin": feed the
pipeline a dataset shaped nothing like the golden curelo-bookings fixture -
three joined tables, retail vocabulary, different column names - with zero
customer-specific code, and assert it still produces an installable,
fully-validated plugin with every KPI computing. Also covers a second,
independently-shaped source (SQLite, edtech vocabulary) for good measure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from forge_core.models.common import CheckStatus, RunStatus
from forge_core.models.run import RunRecord
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline


def _run(source: str, run_id: str, out: Path, fake_llm, industry: str | None = None) -> RunRecord:
    record = RunRecord(run_id=run_id, source_path=source, output_dir=str(out), industry_override=industry)
    record.data_answers = {}  # skip the pre-synthesis clarification pause
    return run_pipeline(
        record,
        packs_root=DEFAULT_PACKS_ROOT,
        profiling_provider=fake_llm,
        generation_provider=fake_llm,
        critique_provider=fake_llm,
    )


def _assert_bind_rate(result: RunRecord, pack_slug: str, *, minimum: float) -> None:
    """bound canonical roles / total canonical roles >= minimum. A stronger
    signal than `unresolved_roles == []` alone once a pack marks roles optional."""
    from forge_core.classification import load_pack

    pack = load_pack(DEFAULT_PACKS_ROOT / pack_slug)
    total = len(pack.canonical_roles)
    bind_event = next(e for e in reversed(result.events) if e.stage.value == "bind")
    unresolved = len(bind_event.data.get("unresolved_roles", []))
    rate = (total - unresolved) / total if total else 1.0
    assert rate >= minimum, (
        f"{pack_slug}: bind rate {rate:.0%} < {minimum:.0%} ({unresolved}/{total} unresolved)"
    )


@pytest.mark.parametrize(
    ("source_fixture", "expected_pack", "run_id"),
    [
        ("retail_orders_dir", "retail-ecommerce", "genericity-retail"),
        ("edtech_sqlite", "edtech", "genericity-edtech"),
    ],
)
def test_generator_produces_a_valid_plugin_for_an_unseen_shape(
    source_fixture: str, expected_pack: str, run_id: str, tmp_path: Path, request: pytest.FixtureRequest, fake_llm
):
    source_path: Path = request.getfixturevalue(source_fixture)
    result = _run(str(source_path), run_id, tmp_path, fake_llm)

    assert result.status == RunStatus.SUCCEEDED, result.error

    classify_event = next(
        e for e in result.events if e.stage.value == "classify" and "ranked_matches" in e.data
    )
    assert classify_event.data["ranked_matches"][0]["pack_slug"] == expected_pack

    bind_event = next(e for e in reversed(result.events) if e.stage.value == "bind")
    assert bind_event.data["unresolved_roles"] == []
    _assert_bind_rate(result, expected_pack, minimum=0.8)

    compile_event = next(e for e in result.events if e.stage.value == "compile_kpis" and "skipped" in e.data)
    assert compile_event.data["skipped"] == [], "every KPI must compute for the acceptance criterion to hold"

    validate_event = next(e for e in reversed(result.events) if e.stage.value == "validate")
    report = validate_event.data["report"]
    assert report["overall"] in (CheckStatus.PASS.value, CheckStatus.WARN.value)
    checks_by_name = {c["check"]: c for c in report["checks"]}
    assert checks_by_name["cli_validate"]["status"] == CheckStatus.PASS.value, "claude plugin validate"
    assert checks_by_name["dry_run"]["status"] == CheckStatus.PASS.value, "every KPI must execute for real"

    package_event = next(e for e in reversed(result.events) if e.stage.value == "package")
    plugin_dir = Path(package_event.data["plugin_dir"])
    assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file()

    # The knowledge pack must ship, populated with a doc per table.
    model = json.loads((plugin_dir / "config" / "schema_model.json").read_text(encoding="utf-8"))
    assert model["schema_hash"].startswith("sha256:")
    real_tables = {t.name for t in result.schema_model.tables} if result.schema_model else set()
    assert {t["name"] for t in model["tables"]} == real_tables
    assert not (plugin_dir / "config" / "business_context.json").exists()


def test_multi_table_source_with_real_relationships_carries_them_through(
    saas_subscriptions_dir: Path, tmp_path: Path, fake_llm
):
    """A third structurally distinct shape: 3 tables joined by real FKs, SaaS
    vocabulary, no dedicated pack. The verified relationships must survive into
    the shipped bindings, every table must stay queryable, and every KPI must
    still compile and dry-run - proving the generator is not tuned to the two
    packed shapes."""
    result = _run(
        str(saas_subscriptions_dir), "genericity-saas", tmp_path / "out", fake_llm,
        industry="generic-analytics",
    )
    assert result.status == RunStatus.SUCCEEDED, result.error

    pkg = next(e for e in reversed(result.events) if e.stage.value == "package")
    plugin_dir = Path(pkg.data["plugin_dir"])
    bindings = json.loads((plugin_dir / "config" / "schema_bindings.json").read_text(encoding="utf-8"))
    assert len(bindings["allowed_tables"]) == 3
    assert len(bindings["relationships"]) >= 2, "both FK relationships must survive into the bindings"

    _assert_bind_rate(result, "generic-analytics", minimum=0.8)
    compile_event = next(e for e in result.events if e.stage.value == "compile_kpis" and "skipped" in e.data)
    assert compile_event.data["skipped"] == []
    validate_event = next(e for e in reversed(result.events) if e.stage.value == "validate")
    checks = {c["check"]: c for c in validate_event.data["report"]["checks"]}
    assert checks["dry_run"]["status"] == CheckStatus.PASS.value


def test_unrelated_multi_table_source_still_produces_a_working_plugin(tmp_path: Path, fake_llm):
    src = tmp_path / "src"
    src.mkdir()
    (src / "sales.csv").write_text(
        "sale_id,amount,sold_on\n" + "\n".join(f"{i},{i * 10}.0,2025-0{i % 9 + 1}-15" for i in range(1, 40)) + "\n",
        encoding="utf-8",
    )
    (src / "inventory.csv").write_text("sku,on_hand\nA,10\nB,0\nC,5\n", encoding="utf-8")
    (src / "staff.csv").write_text("staff_id,role\n1,clerk\n2,manager\n", encoding="utf-8")

    result = _run(str(src), "genericity-unrelated", tmp_path / "out", fake_llm, industry="generic-analytics")
    assert result.status == RunStatus.SUCCEEDED, result.error

    bindings = json.loads(
        (Path(next(e for e in reversed(result.events) if e.stage.value == "package").data["plugin_dir"])
         / "config" / "schema_bindings.json").read_text(encoding="utf-8")
    )
    assert len(bindings["allowed_tables"]) == 3  # every table queryable
    assert bindings["relationships"] == []  # no shared keys -> not an error
