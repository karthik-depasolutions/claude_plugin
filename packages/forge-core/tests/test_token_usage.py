"""Token accounting — "what did this plugin cost to build?"

Before this existed the only LLM spend visible anywhere was the two agents'
`AgentCallRecorder` summaries, buried in StageEvent payloads and never
totalled; the `LLMProvider` path (profiling, generation, critique) reported
nothing at all. These tests pin the two halves that make the number real:
the provider actually records what the API charged for, and the record
accumulates every component into one auditable total.
"""

from __future__ import annotations

from types import SimpleNamespace

from forge_core.llm.cassette import CassetteProvider
from forge_core.llm.gemini import _record_response_usage
from forge_core.llm.provider import UsageTracker
from forge_core.models.run import RunRecord, TokenUsage


def _usage_meta(prompt: int, candidates: int, thoughts: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt,
            candidates_token_count=candidates,
            thoughts_token_count=thoughts,
        )
    )


def test_tracker_accumulates_then_resets_on_drain():
    tracker = UsageTracker()
    tracker.record_usage(input_tokens=100, output_tokens=50)
    tracker.record_usage(input_tokens=10, output_tokens=5)

    drained = tracker.drain_usage()
    assert drained == {
        "input_tokens": 110,
        "output_tokens": 55,
        "thinking_tokens": 0,
        "llm_calls": 2,
    }
    # Draining resets, so the next stage is not billed for the previous
    # stage's spend - that's what makes per-component attribution honest.
    assert tracker.drain_usage()["llm_calls"] == 0


def test_thinking_tokens_are_counted_as_billed_output_exactly_once():
    """Gemini bills reasoning tokens as output but reports them outside
    `candidates_token_count`. Adding them in is what makes the total match
    the invoice; `thinking_tokens` is a breakdown of output, not an extra."""
    tracker = UsageTracker()
    _record_response_usage(tracker, _usage_meta(prompt=1000, candidates=200, thoughts=800))

    drained = tracker.drain_usage()
    assert drained["input_tokens"] == 1000
    assert drained["output_tokens"] == 1000  # 200 visible + 800 thinking
    assert drained["thinking_tokens"] == 800

    usage = TokenUsage()
    usage.add("generation", drained)
    assert usage.total_tokens == 2000  # thinking counted once, via output


def test_usage_extraction_never_raises_on_an_unexpected_response_shape():
    """A blocked response or an older SDK can omit usage_metadata entirely.
    Cost telemetry must degrade to zero, never fail the run that produced it."""
    tracker = UsageTracker()
    _record_response_usage(tracker, SimpleNamespace())
    _record_response_usage(tracker, SimpleNamespace(usage_metadata=None))

    drained = tracker.drain_usage()
    assert drained["llm_calls"] == 2
    assert drained["input_tokens"] == 0


def test_record_totals_across_components_and_survives_resume():
    record = RunRecord(run_id="r1", source_path="x.csv", output_dir="out")
    assert record.token_usage.total_tokens == 0

    record.token_usage.add("profiling", {"input_tokens": 100, "output_tokens": 20, "llm_calls": 1})
    record.token_usage.add("generation", {"input_tokens": 300, "output_tokens": 80, "llm_calls": 2})
    # Agents report their call count as "steps" rather than "llm_calls".
    record.token_usage.add(
        "context_discovery",
        {"steps": 4, "tool_calls": 6, "input_tokens": 5000, "output_tokens": 400, "thinking_tokens": 250},
    )

    usage = record.token_usage
    assert usage.input_tokens == 5400
    assert usage.output_tokens == 500
    assert usage.total_tokens == 5900
    assert usage.llm_calls == 7  # 1 + 2 + 4 steps
    assert usage.by_component["context_discovery"]["llm_calls"] == 4
    assert set(usage.by_component) == {"profiling", "generation", "context_discovery"}

    # A resumed run adds to the total rather than restarting it, so the
    # figure shown is the whole build and not just the final pass.
    round_tripped = RunRecord.model_validate(record.model_dump(mode="json"))
    round_tripped.token_usage.add("critique", {"input_tokens": 100, "output_tokens": 0, "llm_calls": 1})
    assert round_tripped.token_usage.total_tokens == 6000
    assert round_tripped.token_usage.llm_calls == 8


def test_cassette_wrapper_does_not_swallow_usage(tmp_path):
    """The orchestrator only ever holds cassette-wrapped providers, so a
    wrapper that didn't delegate would silently report every run as free."""

    class _Wrapped(UsageTracker):
        def generate_json(self, prompt, *, system=None):
            self.record_usage(input_tokens=7, output_tokens=3)
            return {}

        def generate_text(self, prompt, *, system=None):
            return ""

    provider = CassetteProvider(_Wrapped(), mode="off", cassette_dir=tmp_path)
    provider.generate_json("hello")

    assert provider.drain_usage() == {
        "input_tokens": 7,
        "output_tokens": 3,
        "thinking_tokens": 0,
        "llm_calls": 1,
    }


def test_cassette_wrapper_reports_zero_for_a_provider_without_tracking():
    """Replay mode wraps a null provider that never touches the network."""

    class _Untracked:
        def generate_json(self, prompt, *, system=None):
            return {}

        def generate_text(self, prompt, *, system=None):
            return ""

    provider = CassetteProvider(_Untracked(), mode="off", cassette_dir=None)  # type: ignore[arg-type]
    assert provider.drain_usage()["llm_calls"] == 0
