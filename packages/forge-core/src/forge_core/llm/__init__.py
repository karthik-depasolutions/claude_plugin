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
from forge_core.llm.gemini import GeminiProvider
from forge_core.llm.provider import LLMError, LLMProvider

Role = Literal["profiling", "generation", "critique", "agent"]

_ROLE_ENV_VAR = {
    "profiling": "FORGE_LLM_PROFILING_MODEL",
    "generation": "FORGE_LLM_GENERATION_MODEL",
    "critique": "FORGE_LLM_CRITIQUE_MODEL",
    "agent": "FORGE_LLM_AGENT_MODEL",
}
_DEFAULT_MODEL = "gemini-3.7-flash"


def resolve_model(role: Role) -> str:
    """The one place that knows the default model and the env var per role.
    `agentic/*.py`'s LangChain agents call this directly (they bypass
    `get_provider` - a different interface, `ChatGoogleGenerativeAI` vs the
    `LLMProvider` protocol) so there is exactly one default to update, not
    one per agent file."""
    load_dotenv()
    return os.environ.get(_ROLE_ENV_VAR[role], _DEFAULT_MODEL)


def get_provider(role: Role = "generation") -> LLMProvider:
    # The single choke point every entry point (CLI, API background task,
    # tests) goes through to touch an LLM, so this is the one place that
    # needs to load `.env` - covers processes (like the API's uvicorn
    # reloader subprocess) that don't inherit a shell's exported vars.
    # Never overrides a var the environment already set explicitly.
    model = resolve_model(role)
    cassette_mode = os.environ.get("FORGE_LLM_CASSETTE_MODE", "off").lower()
    cassette_dir = Path(os.environ.get("FORGE_LLM_CASSETTE_DIR", "fixtures/cassettes"))

    if cassette_mode == "replay":
        # Avoid requiring an API key at all when purely replaying fixtures.
        wrapped: LLMProvider = _NullProvider()
    else:
        wrapped = GeminiProvider(model=model)

    if cassette_mode not in ("off", "record", "replay"):
        raise LLMError(f"Invalid FORGE_LLM_CASSETTE_MODE: {cassette_mode!r}")

    return CassetteProvider(wrapped, mode=cassette_mode, cassette_dir=cassette_dir)  # type: ignore[arg-type]


class _NullProvider:
    """Placeholder wrapped provider for pure-replay mode — never actually invoked."""

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
        raise LLMError("Attempted a live LLM call while FORGE_LLM_CASSETTE_MODE=replay.")

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        raise LLMError("Attempted a live LLM call while FORGE_LLM_CASSETTE_MODE=replay.")


__all__ = ["LLMError", "LLMProvider", "get_provider", "resolve_model"]
