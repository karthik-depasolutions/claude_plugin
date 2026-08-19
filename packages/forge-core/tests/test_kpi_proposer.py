from __future__ import annotations

from pathlib import Path

from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.compiler.kpi_proposer import MAX_PROPOSED_KPIS, propose_kpis
from forge_core.ingestion.registry import ingest
from forge_core.llm.provider import LLMError
from forge_core.models.schema_profile import SchemaProfile
from forge_core.profiling import build_structural_only

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"


def _bindings_for(source_path: Path, pack_slug: str):
    ds = ingest(source_path)
    structural = build_structural_only(ds)
    profile = SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)
    pack = load_pack(PACKS_ROOT / pack_slug)
    return pack, resolve_bindings(profile, pack)


def _candidate(kpi_id: str = "new_kpi") -> dict:
    return {
        "id": kpi_id,
        "label": "New KPI",
        "description": "A proposed KPI.",
        "formula_plain_english": "Count of rows.",
        "grain": "one row per record",
        "requires": {"measures": [], "dimensions": [], "filters": [], "entities": []},
        "sql": "SELECT COUNT(*) AS total FROM {{fact}}",
        "unit": "count",
        "assertions": ["total >= 0"],
    }


class _StubProvider:
    def __init__(self, response: dict):
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


def test_propose_kpis_returns_a_grounded_candidate(bookings_csv: Path):
    pack, bindings = _bindings_for(bookings_csv, "healthcare-diagnostics")
    provider = _StubProvider({"kpis": [_candidate()]})

    candidates = propose_kpis(pack, bindings, provider)

    assert len(candidates) == 1
    assert candidates[0].id == "new_kpi"


def test_propose_kpis_drops_malformed_candidates(bookings_csv: Path):
    pack, bindings = _bindings_for(bookings_csv, "healthcare-diagnostics")
    malformed = {"id": "broken"}  # missing every other required CanonicalKpi field
    provider = _StubProvider({"kpis": [_candidate("good_one"), malformed]})

    candidates = propose_kpis(pack, bindings, provider)

    assert [c.id for c in candidates] == ["good_one"]


def test_propose_kpis_skips_ids_that_collide_with_the_pack_catalog(bookings_csv: Path):
    pack, bindings = _bindings_for(bookings_csv, "healthcare-diagnostics")
    existing_id = pack.kpis[0].id
    provider = _StubProvider({"kpis": [_candidate(existing_id), _candidate("genuinely_new")]})

    candidates = propose_kpis(pack, bindings, provider)

    assert [c.id for c in candidates] == ["genuinely_new"]


def test_propose_kpis_caps_at_max_proposed(bookings_csv: Path):
    pack, bindings = _bindings_for(bookings_csv, "healthcare-diagnostics")
    many = [_candidate(f"kpi_{i}") for i in range(MAX_PROPOSED_KPIS + 5)]
    provider = _StubProvider({"kpis": many})

    candidates = propose_kpis(pack, bindings, provider)

    assert len(candidates) == MAX_PROPOSED_KPIS


def test_propose_kpis_degrades_to_empty_on_llm_error(bookings_csv: Path):
    pack, bindings = _bindings_for(bookings_csv, "healthcare-diagnostics")

    assert propose_kpis(pack, bindings, _RaisingProvider()) == []
