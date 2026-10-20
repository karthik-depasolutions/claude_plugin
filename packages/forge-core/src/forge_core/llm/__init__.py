"""Factory for the configured LLM provider. Every call site should use
`get_provider(role=...)` rather than instantiating GeminiProvider directly,
so cassette mode and per-role model selection stay centralized.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

from forge_core.llm.cassette import CassetteProvider
from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.llm.usage import TokenUsage

Role = Literal["profiling", "generation", "critique"]

_ROLE_ENV_VAR = {
    "profiling": "FORGE_LLM_PROFILING_MODEL",
    "generation": "FORGE_LLM_GENERATION_MODEL",
    "critique": "FORGE_LLM_CRITIQUE_MODEL",
}
_ROLE_PROVIDER_ENV_VAR = {
    "profiling": "FORGE_LLM_PROFILING_PROVIDER",
    "generation": "FORGE_LLM_GENERATION_PROVIDER",
    "critique": "FORGE_LLM_CRITIQUE_PROVIDER",
}
_DEFAULT_MODEL = {"gemini": "gemini-2.5-flash", "anthropic": "claude-sonnet-5"}


def _backend_for(role: Role) -> str:
    return os.environ.get(
        _ROLE_PROVIDER_ENV_VAR[role], os.environ.get("FORGE_LLM_PROVIDER", "gemini")
    ).lower()


def get_provider(role: Role = "generation", *, usage: TokenUsage | None = None) -> LLMProvider:
    # The single choke point every entry point (CLI, API background task,
    # tests) goes through to touch an LLM, so this is the one place that
    # needs to load `.env` - covers processes (like the API's uvicorn
    # reloader subprocess) that don't inherit a shell's exported vars.
    # Never overrides a var the environment already set explicitly.
    # `usage`, when given, is a per-run accumulator every response's reported
    # token counts are added to (see forge_core.llm.usage).
    load_dotenv()
    cassette_mode = os.environ.get("FORGE_LLM_CASSETTE_MODE", "off").lower()
    cassette_dir = Path(os.environ.get("FORGE_LLM_CASSETTE_DIR", "fixtures/cassettes"))
    if cassette_mode not in ("off", "record", "replay"):
        raise LLMError(f"Invalid FORGE_LLM_CASSETTE_MODE: {cassette_mode!r}")

    if cassette_mode == "replay":
        # Avoid requiring an API key at all when purely replaying fixtures.
        return CassetteProvider(_NullProvider(), mode=cassette_mode, cassette_dir=cassette_dir)  # type: ignore[arg-type]

    backend = _backend_for(role)
    model = os.environ.get(_ROLE_ENV_VAR[role]) or _DEFAULT_MODEL.get(backend, _DEFAULT_MODEL["gemini"])

    wrapped: LLMProvider
    if backend == "anthropic":
        from forge_core.llm.anthropic import AnthropicProvider

        wrapped = AnthropicProvider(model=model, role=role, usage=usage)
    elif backend == "gemini":
        from forge_core.llm.gemini import GeminiProvider

        wrapped = GeminiProvider(model=model, role=role, usage=usage)
    else:
        raise LLMError(
            f"Unknown LLM backend {backend!r}. Set FORGE_LLM_PROVIDER (or "
            f"FORGE_LLM_{role.upper()}_PROVIDER) to 'gemini' or 'anthropic'."
        )

    return CassetteProvider(wrapped, mode=cassette_mode, cassette_dir=cassette_dir)  # type: ignore[arg-type]


class _NullProvider:
    """Placeholder wrapped provider for pure-replay mode — never actually invoked."""

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
        raise LLMError("Attempted a live LLM call while FORGE_LLM_CASSETTE_MODE=replay.")

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        raise LLMError("Attempted a live LLM call while FORGE_LLM_CASSETTE_MODE=replay.")


__all__ = ["LLMError", "LLMProvider", "TokenUsage", "get_provider"]
