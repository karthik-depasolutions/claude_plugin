"""Gemini implementation of LLMProvider. The generation engine's only
production LLM backend today — see forge_core.llm.get_provider for how a
second provider would be added without touching call sites."""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.llm.usage import TokenUsage


class GeminiProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.2,
        *,
        role: str = "generation",
        usage: TokenUsage | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self._role = role
        self._usage = usage
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            raise LLMError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and set it, "
                "or set FORGE_LLM_CASSETTE_MODE=replay to use recorded fixtures."
            )
        self._client = None  # lazy — keeps import cost out of code paths that use cassettes only
        self._client_lock = threading.Lock()

    def _record_usage(self, response: Any) -> None:
        """Add this response's reported token counts to the run accumulator.
        Best-effort: a response without usage_metadata is simply not counted."""
        if self._usage is None:
            return
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return
        prompt = int(getattr(meta, "prompt_token_count", 0) or 0)
        candidates = int(getattr(meta, "candidates_token_count", 0) or 0)
        total = int(getattr(meta, "total_token_count", 0) or 0)
        self._usage.record(
            model=self.model,
            role=self._role,
            input_tokens=prompt,
            output_tokens=candidates,
            total_tokens=total or None,
        )

    def _get_client(self):
        # Double-checked lock: synthesis fans the per-table calls across a
        # thread pool, so the lazy init must not race.
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    from google import genai

                    self._client = genai.Client(api_key=self._api_key)
        return self._client

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict[str, Any]:
        from google.genai import types

        client = self._get_client()
        config = types.GenerateContentConfig(
            temperature=self.temperature,
            response_mime_type="application/json",
            system_instruction=system,
        )
        response = client.models.generate_content(model=self.model, contents=prompt, config=config)
        self._record_usage(response)
        text = response.text
        if not text:
            raise LLMError("Gemini returned an empty response for a JSON-mode request.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Gemini returned invalid JSON: {exc}\n---\n{text[:2000]}") from exc

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        from google.genai import types

        client = self._get_client()
        config = types.GenerateContentConfig(temperature=self.temperature, system_instruction=system)
        response = client.models.generate_content(model=self.model, contents=prompt, config=config)
        self._record_usage(response)
        text = response.text
        if not text:
            raise LLMError("Gemini returned an empty text response.")
        return text
