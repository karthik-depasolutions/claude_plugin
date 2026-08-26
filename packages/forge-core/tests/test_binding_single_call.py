"""Binding resolves in one structured call over the data map, not a tool loop.

The map already states every fact binding needs - each column's type,
cardinality, null rate, range and top values, plus verified joins - so
binding is a judgement over known facts, not an investigation.

Given tools, the agent reliably spent its step budget exploring and hit its
recursion limit without proposing anything: across three identical runs on
bookings.csv it resolved 0/9, 0/9 and 9/9 roles at 62k-84k tokens each. The
single call resolves 9/9 for ~5k and cannot run out of steps.

These tests pin the parsing, which is where it kept silently failing - every
bug here discarded good proposals rather than erroring, so the symptom was
always "the agent resolved nothing" with no clue why.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from forge_core.agentic.data_understanding_agent import (
    _coerce_aggregations,
    _coerce_confidence,
    _coerce_evidence,
    _propose_from_map_single_call,
)
from forge_core.ingestion.registry import ingest
from forge_core.models.metrics import AggOp
from forge_core.profiling import build_structural_only

DATASETS = Path(__file__).resolve().parents[3] / "fixtures" / "datasets"


class _ScriptedProvider:
    def __init__(self, payload):
        self.payload = payload
        self.prompts: list[str] = []

    def generate_json(self, prompt, *, system=None):
        self.prompts.append(prompt)
        return self.payload

    def generate_text(self, prompt, *, system=None):
        return ""


@pytest.fixture
def bookings():
    ds = ingest(DATASETS / "bookings.csv")
    return ds, build_structural_only(ds)


def _run(monkeypatch, bookings, payload):
    ds, structural = bookings
    provider = _ScriptedProvider(payload)
    monkeypatch.setattr(
        "forge_core.llm.get_provider", lambda role="generation": provider
    )
    claims, log = _propose_from_map_single_call(
        {"revenue_amount": "money taken"}, structural, ds, model_name=None, on_stats=None
    )
    return claims, log, provider


def test_a_well_formed_proposal_becomes_a_claim(monkeypatch, bookings):
    claims, log, provider = _run(
        monkeypatch,
        bookings,
        {
            "proposals": [
                {
                    "concept": "revenue_amount",
                    "table": "bookings",
                    "column": "amount_inr",
                    "meaning": "amount charged",
                    "kind": "measure",
                    "unit": "INR",
                    "valid_aggregations": ["sum", "mean"],
                    "confidence": 0.9,
                    "evidence": ["range=[699.0, 2999.0]"],
                }
            ]
        },
    )

    assert "revenue_amount" in claims
    column, claim = claims["revenue_amount"]
    assert column == "amount_inr"
    assert claim.kind == "measure"
    assert AggOp.SUM in claim.valid_aggregations
    # The map is seeded as evidence so a map-cited claim can pass gate V1.
    assert log and "bookings" in log[0]
    # The whole profile must actually reach the model.
    assert "amount_inr" in provider.prompts[0]


def test_the_concept_field_is_not_the_tables_role(monkeypatch, bookings):
    """The data map writes `role=fact` for tables. Asking the model for
    "role" made it return *that* and put the concept name in `meaning`, so
    every proposal was silently dropped and binding resolved nothing."""
    ds, structural = bookings
    provider = _ScriptedProvider({"proposals": []})
    monkeypatch.setattr("forge_core.llm.get_provider", lambda role="generation": provider)
    _propose_from_map_single_call(
        {"revenue_amount": "money"}, structural, ds, model_name=None, on_stats=None
    )
    prompt = provider.prompts[0]
    assert '"concept"' in prompt
    assert "It is NOT the table's role" in prompt


def test_a_proposal_for_an_unrequested_concept_is_ignored(monkeypatch, bookings):
    claims, _log, _p = _run(
        monkeypatch,
        bookings,
        {
            "proposals": [
                {
                    "concept": "not_a_real_concept",
                    "table": "bookings",
                    "column": "amount_inr",
                    "kind": "measure",
                    "confidence": 0.9,
                }
            ]
        },
    )
    assert claims == {}


def test_one_malformed_proposal_does_not_lose_the_others(monkeypatch, bookings):
    claims, _log, _p = _run(
        monkeypatch,
        bookings,
        {
            "proposals": [
                {"concept": "revenue_amount", "kind": "measure"},  # no column
                {
                    "concept": "revenue_amount",
                    "table": "bookings",
                    "column": "amount_inr",
                    "kind": "measure",
                    "confidence": 0.8,
                },
            ]
        },
    )
    assert claims["revenue_amount"][0] == "amount_inr"


def test_an_empty_or_broken_response_is_survivable(monkeypatch, bookings):
    for payload in ({}, {"proposals": None}, {"proposals": []}):
        claims, _log, _p = _run(monkeypatch, bookings, payload)
        assert claims == {}


# --- Coercion of what models actually return -------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0.9, 0.9), ("0.75", 0.75), ("high", 0.85), ("LOW", 0.35), (5, 1.0), (-1, 0.0), (None, 0.6)],
)
def test_confidence_words_are_accepted(raw, expected):
    """Models return "high" as readily as 0.85 however the prompt asks.
    Discarding an otherwise-good proposal over it loses a real binding."""
    assert _coerce_confidence(raw) == expected


def test_a_single_evidence_string_is_not_split_into_characters():
    """`[str(c) for c in "abc"]` yields ['a','b','c'] - which passes the
    "evidence is non-empty" gate while carrying no evidence at all."""
    assert _coerce_evidence("cardinality=20") == ["cardinality=20"]
    assert _coerce_evidence(["a", "b"]) == ["a", "b"]
    assert _coerce_evidence(None) == []


def test_unknown_aggregations_are_dropped_not_fatal():
    assert _coerce_aggregations(["sum", "teleport", "MEAN"]) == [AggOp.SUM, AggOp.MEAN]
    assert _coerce_aggregations(None) == []
