"""Token accounting: the per-run accumulator and the Gemini provider hook."""

from __future__ import annotations

import threading
import types

from forge_core.llm.gemini import GeminiProvider
from forge_core.llm.usage import TokenUsage


def test_record_accumulates_scalars_and_breakdowns():
    u = TokenUsage()
    u.record(model="gemini-2.5-flash", role="profiling", input_tokens=100, output_tokens=20, total_tokens=125)
    u.record(model="gemini-2.5-flash", role="generation", input_tokens=50, output_tokens=10)

    s = u.snapshot()
    assert s["input_tokens"] == 150
    assert s["output_tokens"] == 30
    assert s["total_tokens"] == 125 + 60  # reported total, then input+output fallback
    assert s["calls"] == 2
    assert s["by_role"]["profiling"] == {
        "input_tokens": 100, "output_tokens": 20, "total_tokens": 125, "calls": 1
    }
    assert s["by_model"]["gemini-2.5-flash"]["calls"] == 2


def test_record_is_thread_safe():
    u = TokenUsage()

    def hammer() -> None:
        for _ in range(500):
            u.record(model="m", role="generation", input_tokens=2, output_tokens=1)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    s = u.snapshot()
    assert s["calls"] == 8 * 500
    assert s["input_tokens"] == 8 * 500 * 2
    assert s["output_tokens"] == 8 * 500 * 1


def test_gemini_records_reported_usage_metadata():
    u = TokenUsage()
    provider = GeminiProvider(model="gemini-2.5-flash", api_key="unused", role="critique", usage=u)

    response = types.SimpleNamespace(
        usage_metadata=types.SimpleNamespace(
            prompt_token_count=1234, candidates_token_count=567, total_token_count=1801
        )
    )
    provider._record_usage(response)

    s = u.snapshot()
    assert (s["input_tokens"], s["output_tokens"], s["total_tokens"], s["calls"]) == (1234, 567, 1801, 1)
    assert s["by_role"]["critique"]["input_tokens"] == 1234


def test_gemini_record_usage_is_a_noop_without_metadata_or_sink():
    GeminiProvider(model="m", api_key="x")._record_usage(types.SimpleNamespace())  # no sink -> no error

    u = TokenUsage()
    GeminiProvider(model="m", api_key="x", usage=u)._record_usage(types.SimpleNamespace(usage_metadata=None))
    assert u.snapshot()["calls"] == 0
