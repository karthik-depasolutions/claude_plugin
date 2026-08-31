"""Semantic profiling test.

Runs against `FakeLLMProvider` (deterministic, no key, no cassette). To
exercise the real prompt/response loop against Gemini, set
`FORGE_LLM_CASSETTE_MODE=record` and swap in `get_provider(role="profiling")`.
"""

from __future__ import annotations

from pathlib import Path

from forge_core.ingestion.registry import ingest
from forge_core.profiling import build_structural_only
from forge_core.profiling.semantic import run_semantic_profile
from forge_core.testing import FakeLLMProvider


def test_semantic_profile_proposes_grounded_insights(bookings_csv: Path):
    ds = ingest(bookings_csv)
    structural = build_structural_only(ds)

    semantic = run_semantic_profile(ds, structural, FakeLLMProvider())

    valid_columns = {c.name.lower() for c in structural.columns}
    for insight in semantic.candidate_insights:
        for col in insight.columns:
            assert col.lower() in valid_columns, f"hallucinated column {col!r} not in schema"

    assert len(semantic.candidate_insights) >= 1


def test_semantic_profile_sends_real_sample_values(bookings_csv: Path):
    """Sample rows now reach the model unredacted - the privacy boundary
    that used to mask flagged columns was removed."""
    ds = ingest(bookings_csv)
    structural = build_structural_only(ds)

    seen: dict[str, str] = {}

    class _Capturing(FakeLLMProvider):
        def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
            seen["prompt"] = prompt
            return super().generate_json(prompt, system=system)

    run_semantic_profile(ds, structural, _Capturing())

    row = ds.table("bookings").sample_rows[0]
    a_value = next((str(v) for v in row.values() if v not in (None, "")), None)
    assert a_value is not None
    assert a_value in seen["prompt"]
