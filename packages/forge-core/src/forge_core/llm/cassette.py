"""Record/replay wrapper so tests and CI never need a live Gemini key.

Modes (FORGE_LLM_CASSETTE_MODE):
  off     — passthrough to the wrapped provider (default; normal operation).
  record  — call the wrapped provider for real and save the exchange to
            fixtures/cassettes/<hash>.json.
  replay  — never touch the network; load a previously recorded exchange and
            raise a clear error if one isn't on disk yet.

This is what makes `fixtures/cassettes/` in the repo layout meaningful: it's
the deterministic-test fixture set for every LLM-touching code path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from forge_core.llm.provider import LLMError, LLMProvider

CassetteMode = Literal["off", "record", "replay"]


def _cassette_key(method: str, prompt: str, system: str | None) -> str:
    digest = hashlib.sha256(f"{method}::{system or ''}::{prompt}".encode()).hexdigest()
    return digest[:24]


class CassetteProvider(LLMProvider):
    def __init__(self, wrapped: LLMProvider, mode: CassetteMode, cassette_dir: Path) -> None:
        self._wrapped = wrapped
        self._mode = mode
        self._dir = cassette_dir
        if self._mode == "record":
            self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, method: str, prompt: str, system: str | None) -> Path:
        return self._dir / f"{_cassette_key(method, prompt, system)}.json"

    def drain_usage(self) -> dict[str, int]:
        """Delegate to the wrapped provider so cost telemetry survives the
        wrapper. Replay and the null provider never touch the network, so
        they legitimately report zeros rather than being unmeasurable."""
        drain = getattr(self._wrapped, "drain_usage", None)
        if drain is None:
            return {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "llm_calls": 0}
        return drain()

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict[str, Any]:
        return self._dispatch("generate_json", prompt, system)

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return self._dispatch("generate_text", prompt, system)

    def _dispatch(self, method: str, prompt: str, system: str | None) -> Any:
        if self._mode == "off":
            return getattr(self._wrapped, method)(prompt, system=system)

        path = self._path_for(method, prompt, system)

        if self._mode == "replay":
            if not path.exists():
                raise LLMError(
                    f"No cassette recorded for this exact prompt at {path}. "
                    "Run once with FORGE_LLM_CASSETTE_MODE=record and a live API key, "
                    "or set FORGE_LLM_CASSETTE_MODE=off."
                )
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload["response"]

        # record
        response = getattr(self._wrapped, method)(prompt, system=system)
        path.write_text(
            json.dumps(
                {"method": method, "system": system, "prompt": prompt, "response": response},
                indent=2,
                default=str,
            ),
            encoding="utf-8",
            newline="\n",
        )
        return response
