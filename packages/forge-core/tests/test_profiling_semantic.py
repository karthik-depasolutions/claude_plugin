"""Semantic profiling test — runs against a recorded cassette by default so
CI never needs a live Gemini key. Re-record with:

    $env:FORGE_LLM_CASSETTE_MODE = "record"
    uv run pytest packages/forge-core/tests/test_profiling_semantic.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from forge_core.ingestion.registry import ingest
from forge_core.llm import get_provider
from forge_core.llm.provider import LLMProvider
from forge_core.profiling import build_structural_only
from forge_core.profiling.semantic import REDACTED, run_semantic_profile


@pytest.fixture(autouse=True)
def _cassette_mode(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_CASSETTE_MODE", os.environ.get("FORGE_LLM_CASSETTE_MODE", "replay"))
    monkeypatch.setenv("FORGE_LLM_CASSETTE_DIR", "fixtures/cassettes")


def test_semantic_profile_proposes_grounded_insights(bookings_csv: Path):
    ds = ingest(bookings_csv)
    structural = build_structural_only(ds)
    provider = get_provider(role="profiling")

    semantic = run_semantic_profile(ds, structural, provider)

    valid_columns = {c.name.lower() for c in structural.columns}
    for insight in semantic.candidate_insights:
        for col in insight.columns:
            assert col.lower() in valid_columns, f"hallucinated column {col!r} not in schema"

    assert len(semantic.candidate_insights) >= 1


class _CapturingProvider(LLMProvider):
    """Wraps a real provider only to assert on the outgoing prompt text —
    used to prove the privacy boundary holds regardless of cassette mode."""

    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner
        self.last_prompt: str = ""

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
        self.last_prompt = prompt
        return self.inner.generate_json(prompt, system=system)

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        self.last_prompt = prompt
        return self.inner.generate_text(prompt, system=system)


def test_semantic_profile_never_leaks_raw_pii_values(bookings_csv: Path):
    ds = ingest(bookings_csv)
    structural = build_structural_only(ds)
    provider = _CapturingProvider(get_provider(role="profiling"))

    run_semantic_profile(ds, structural, provider)

    pii_columns = {c.name for c in structural.columns if c.is_likely_pii}
    assert pii_columns, "fixture should have at least one PII-flagged column"

    for row in ds.table("bookings").sample_rows:
        for col in pii_columns:
            raw_value = row.get(col)
            if raw_value not in (None, ""):
                assert str(raw_value) not in provider.last_prompt

    assert REDACTED in provider.last_prompt
