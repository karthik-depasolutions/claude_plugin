"""forge packs new|lint|test - the pack authoring tooling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from forge_core.classification.loader import load_pack
from forge_core.packs import lint_pack, run_pack_test, scaffold_pack

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKS_ROOT = REPO_ROOT / "industry-packs"
FIXTURES = REPO_ROOT / "fixtures" / "datasets"


_SHIPPED = ["edtech", "finance", "generic-analytics", "healthcare-diagnostics", "retail-ecommerce"]


@pytest.mark.parametrize("slug", _SHIPPED)
def test_every_shipped_pack_lints_clean(slug: str):
    errors = [f for f in lint_pack(PACKS_ROOT / slug) if f.level == "error"]
    assert errors == [], errors


def test_lint_flags_a_typoed_role_in_a_kpi(tmp_path: Path):
    pack_dir = scaffold_pack("lint-broken", tmp_path)
    kpi = json.loads((pack_dir / "kpis" / "total_revenue.json").read_text())
    kpi["requires"]["measures"] = ["revenu_amount"]  # typo
    (pack_dir / "kpis" / "total_revenue.json").write_text(json.dumps(kpi))

    findings = lint_pack(pack_dir)
    assert any(f.level == "error" and "revenu_amount" in f.message for f in findings)


def test_lint_flags_an_unknown_template_token(tmp_path: Path):
    pack_dir = scaffold_pack("lint-token", tmp_path)
    kpi = json.loads((pack_dir / "kpis" / "total_revenue.json").read_text())
    kpi["sql"] = "SELECT SUM({{revenue_amount}}) AS total_revenue FROM {{fact}} WHERE {{mystery_role}} = 1"
    (pack_dir / "kpis" / "total_revenue.json").write_text(json.dumps(kpi))

    findings = lint_pack(pack_dir)
    assert any("mystery_role" in f.message for f in findings)


def test_scaffold_creates_a_loadable_pack(tmp_path: Path):
    pack_dir = scaffold_pack("demo-vertical", tmp_path)
    assert (pack_dir / "pack.json").is_file()
    assert (pack_dir / "kpis" / "total_revenue.json").is_file()
    for d in ("skills", "agents", "recipes"):
        assert (pack_dir / d).is_dir()
    pack = load_pack(pack_dir)  # pydantic-valid
    assert pack.slug == "demo-vertical"
    assert lint_pack(pack_dir) == [] or all(f.level == "warn" for f in lint_pack(pack_dir))


def test_scaffold_rejects_a_bad_slug_and_an_existing_dir(tmp_path: Path):
    with pytest.raises(ValueError):
        scaffold_pack("Bad Slug", tmp_path)
    scaffold_pack("dupe", tmp_path)
    with pytest.raises(FileExistsError):
        scaffold_pack("dupe", tmp_path)


def test_pack_test_reports_bind_rate_and_dry_run_for_retail():
    report = run_pack_test(PACKS_ROOT / "retail-ecommerce", FIXTURES / "retail_orders")
    assert report.pack_slug == "retail-ecommerce"
    assert report.bind_rate == 1.0
    assert report.unresolved_roles == []
    assert report.skipped_kpis == []
    assert len(report.compiled_kpis) == 8
    assert all("row(s)" in outcome for _, outcome in report.dry_run)
    # the cross-table bind is visible in the per-role report
    tier = next(rb for rb in report.role_binds if rb.role == "customer_tier")
    assert tier.source == "cross_table" and tier.table_alias == "customers"
