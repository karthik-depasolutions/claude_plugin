"""Gemini implementation of LLMProvider. The generation engine's only
production LLM backend today — see forge_core.llm.get_provider for how a
second provider would be added without touching call sites."""

from __future__ import annotations

import json
import os
from typing import Any

from forge_core.llm.provider import LLMError, LLMProvider


class GeminiProvider(LLMProvider):
    def __init__(self, model: str, api_key: str | None = None, temperature: float = 0.2) -> None:
        self.model = model
        self.temperature = temperature
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            raise LLMError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and set it, "
                "or set FORGE_LLM_CASSETTE_MODE=replay to use recorded fixtures."
            )
        self._client = None  # lazy — keeps import cost out of code paths that use cassettes only

    def _get_client(self):
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
        text = response.text
        if not text:
            raise LLMError("Gemini returned an empty text response.")
        return text
