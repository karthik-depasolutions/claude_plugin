"""LLM provider abstraction.

Every place in the pipeline that calls an LLM (semantic profiling, binding
proposals, generation, self-critique) goes through this interface, never
through `google-genai` directly. That keeps the door open for a second
provider and — more importantly — makes cassette record/replay (see
cassette.py) a drop-in wrapper for deterministic tests and CI.
"""

from __future__ import annotations

from typing import Any, Protocol


class LLMProvider(Protocol):
    def generate_json(self, prompt: str, *, system: str | None = None) -> dict[str, Any]:
        """Return a parsed JSON object. Implementations must enforce JSON-mode
        generation at the API level where the provider supports it, not just
        hope the model returns valid JSON."""
        ...

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        """Return raw text (used for artifact HTML and free-form prose)."""
        ...


class LLMError(RuntimeError):
    """Raised for any provider failure — missing key, bad response, etc."""
