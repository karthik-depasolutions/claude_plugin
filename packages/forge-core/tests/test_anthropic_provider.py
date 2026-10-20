"""The Anthropic backend and the FORGE_LLM_PROVIDER dispatch in get_provider."""

from __future__ import annotations

import types

import pytest
from forge_core.llm import get_provider
from forge_core.llm.anthropic import AnthropicProvider
from forge_core.llm.provider import LLMError
from forge_core.llm.usage import TokenUsage


def test_get_provider_dispatches_on_forge_llm_provider(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_CASSETTE_MODE", "off")
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unused-in-this-test")
    monkeypatch.delenv("FORGE_LLM_PROFILING_MODEL", raising=False)

    provider = get_provider(role="profiling")._wrapped  # unwrap CassetteProvider
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-sonnet-5"  # provider-appropriate default


def test_get_provider_per_role_override_wins(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_CASSETTE_MODE", "off")
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("FORGE_LLM_CRITIQUE_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unused")
    monkeypatch.setenv("GEMINI_API_KEY", "unused")

    assert isinstance(get_provider(role="critique")._wrapped, AnthropicProvider)
    from forge_core.llm.gemini import GeminiProvider

    assert isinstance(get_provider(role="generation")._wrapped, GeminiProvider)


def test_unknown_backend_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_CASSETTE_MODE", "off")
    monkeypatch.setenv("FORGE_LLM_PROVIDER", "openai")
    with pytest.raises(LLMError, match="Unknown LLM backend"):
        get_provider(role="generation")


def test_anthropic_provider_requires_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider(model="claude-sonnet-5")


def test_anthropic_records_usage_from_the_response():
    u = TokenUsage()
    p = AnthropicProvider(model="claude-sonnet-5", api_key="unused", role="profiling", usage=u)
    response = types.SimpleNamespace(usage=types.SimpleNamespace(input_tokens=1500, output_tokens=320))
    p._record_usage(response)
    s = u.snapshot()
    assert (s["input_tokens"], s["output_tokens"], s["total_tokens"], s["calls"]) == (1500, 320, 1820, 1)
    assert s["by_role"]["profiling"]["input_tokens"] == 1500


def test_anthropic_generate_json_strips_fences_and_parses(monkeypatch):
    p = AnthropicProvider(model="claude-sonnet-5", api_key="unused")
    monkeypatch.setattr(p, "_message", lambda prompt, system: '```json\n{"ok": true, "n": 3}\n```')
    assert p.generate_json("give me json") == {"ok": True, "n": 3}
