from __future__ import annotations

from pathlib import Path

from forge_core.classification import classify, load_all_packs
from forge_core.ingestion.registry import ingest
from forge_core.models.schema_profile import SchemaProfile
from forge_core.profiling import build_structural_only

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"


def _profile_for(source_path: Path) -> SchemaProfile:
    ds = ingest(source_path)
    structural = build_structural_only(ds)
    return SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)


def test_all_five_packs_load():
    packs = load_all_packs(PACKS_ROOT)
    slugs = {p.slug for p in packs}
    assert slugs == {"healthcare-diagnostics", "retail-ecommerce", "edtech", "finance", "generic-analytics"}
    for pack in packs:
        assert len(pack.kpis) >= 4, f"{pack.slug} should have a real KPI library, not a stub"


def test_bookings_classifies_as_healthcare(bookings_csv: Path):
    packs = load_all_packs(PACKS_ROOT)
    profile = _profile_for(bookings_csv)
    result = classify(profile, packs)
    assert result.primary_pack_slug == "healthcare-diagnostics"
    assert not result.requires_customer_confirmation


def test_retail_orders_classifies_as_retail(retail_orders_dir: Path):
    packs = load_all_packs(PACKS_ROOT)
    profile = _profile_for(retail_orders_dir)
    result = classify(profile, packs)
    assert result.primary_pack_slug == "retail-ecommerce"


def test_edtech_sqlite_classifies_as_edtech(edtech_sqlite: Path):
    packs = load_all_packs(PACKS_ROOT)
    profile = _profile_for(edtech_sqlite)
    result = classify(profile, packs)
    assert result.primary_pack_slug == "edtech"


def test_ranked_matches_cover_every_pack(bookings_csv: Path):
    packs = load_all_packs(PACKS_ROOT)
    profile = _profile_for(bookings_csv)
    result = classify(profile, packs)
    assert len(result.ranked_matches) == len(packs)
    confidences = [m.confidence for m in result.ranked_matches]
    assert confidences == sorted(confidences, reverse=True)
