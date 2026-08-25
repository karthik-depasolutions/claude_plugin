from __future__ import annotations

from pathlib import Path

from forge_core.classification import load_pack
from forge_core.compiler.metric_generator import generate_metrics
from forge_core.compiler.metric_proposer import MAX_PROPOSED_METRICS, propose_metrics
from forge_core.ingestion.registry import ingest
from forge_core.llm.provider import LLMError
from forge_core.models.metrics import Provenance
from forge_core.profiling import build_structural_only

DATASETS_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "datasets"
PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"


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


def _edtech_setup():
    from forge_core.binding import resolve_bindings
    from forge_core.models.schema_profile import SchemaProfile

    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    profile = SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)
    pack = load_pack(PACKS_ROOT / "edtech")
    bindings = resolve_bindings(profile, pack)
    base_metrics = generate_metrics("enrollments", structural, denied_columns=set())
    physical_ref = {t.name: t.physical_ref for t in ds.tables}
    return pack, bindings, base_metrics, physical_ref


def test_valid_proposal_referencing_a_real_base_metric_and_value_set_is_accepted():
    pack, bindings, base_metrics, physical_ref = _edtech_setup()
    assert any(vs.name == "completed_values" for vs in bindings.value_sets), (
        "fixture assumption: edtech pack resolves a completed_values value set"
    )
    base_id = base_metrics[0].id
    provider = _StubProvider(
        {
            "proposals": [
                {
                    "id": "completed_only_metric",
                    "label": "Completed Only",
                    "description": "Base metric restricted to completed enrollments.",
                    "base_metric_id": base_id,
                    "filter_value_set": "completed_values",
                    "confidence": 0.8,
                    "reasoning": "Completed-only is the more useful cut for this business.",
                }
            ]
        }
    )
    result = propose_metrics(pack, bindings, base_metrics, physical_ref, provider, data_context=None)
    assert len(result) == 1
    m = result[0]
    assert m.id == "completed_only_metric"
    assert m.source == "agent_proposed"
    assert m.measure_column == base_metrics[0].measure_column  # copied verbatim, not agent-authored
    resolved = bindings.value_set("completed_values")
    assert m.default_filters and m.default_filters[0].values == resolved.values
    assert isinstance(m.prov, Provenance)
    assert m.prov.origin == "inferred_llm"
    assert m.prov.confidence == 0.8


def test_proposal_referencing_a_fabricated_base_metric_id_is_dropped():
    pack, bindings, base_metrics, physical_ref = _edtech_setup()
    provider = _StubProvider(
        {"proposals": [{"id": "x", "label": "X", "description": "x", "base_metric_id": "not_a_real_metric"}]}
    )
    assert propose_metrics(pack, bindings, base_metrics, physical_ref, provider, data_context=None) == []


def test_proposal_referencing_a_fabricated_value_set_is_dropped():
    pack, bindings, base_metrics, physical_ref = _edtech_setup()
    provider = _StubProvider(
        {
            "proposals": [
                {
                    "id": "x",
                    "label": "X",
                    "description": "x",
                    "base_metric_id": base_metrics[0].id,
                    "filter_value_set": "invented_value_set_name",
                }
            ]
        }
    )
    assert propose_metrics(pack, bindings, base_metrics, physical_ref, provider, data_context=None) == []


def test_proposal_id_colliding_with_an_existing_metric_is_dropped():
    pack, bindings, base_metrics, physical_ref = _edtech_setup()
    provider = _StubProvider(
        {
            "proposals": [
                {"id": base_metrics[0].id, "label": "Dup", "description": "dup", "base_metric_id": base_metrics[0].id}
            ]
        }
    )
    assert propose_metrics(pack, bindings, base_metrics, physical_ref, provider, data_context=None) == []


def test_provider_raising_degrades_to_empty_list():
    pack, bindings, base_metrics, physical_ref = _edtech_setup()
    assert propose_metrics(pack, bindings, base_metrics, physical_ref, _RaisingProvider(), data_context=None) == []


def test_no_base_metrics_short_circuits_without_calling_the_provider():
    pack, bindings, _base_metrics, physical_ref = _edtech_setup()

    class _ExplodingProvider:
        def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
            raise AssertionError("should never be called when there are no base metrics")

    assert propose_metrics(pack, bindings, [], physical_ref, _ExplodingProvider(), data_context=None) == []


def test_result_capped_at_max_proposed_metrics():
    pack, bindings, base_metrics, physical_ref = _edtech_setup()
    proposals = [
        {"id": f"metric_{i}", "label": f"M{i}", "description": "d", "base_metric_id": base_metrics[0].id}
        for i in range(MAX_PROPOSED_METRICS + 5)
    ]
    provider = _StubProvider({"proposals": proposals})
    result = propose_metrics(pack, bindings, base_metrics, physical_ref, provider, data_context=None)
    assert len(result) <= MAX_PROPOSED_METRICS


def test_unfiltered_proposal_is_a_valid_way_to_just_relabel_a_base_metric():
    pack, bindings, base_metrics, physical_ref = _edtech_setup()
    provider = _StubProvider(
        {
            "proposals": [
                {
                    "id": "renamed_metric",
                    "label": "Business Framed Label",
                    "description": "Same metric, better framing.",
                    "base_metric_id": base_metrics[0].id,
                }
            ]
        }
    )
    result = propose_metrics(pack, bindings, base_metrics, physical_ref, provider, data_context=None)
    assert len(result) == 1
    assert result[0].default_filters == []
