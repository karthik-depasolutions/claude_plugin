"""Gemini implementation of LLMProvider. The generation engine's only
production LLM backend today — see forge_core.llm.get_provider for how a
second provider would be added without touching call sites."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, TypeVar

from forge_core.llm.provider import LLMError, LLMProvider

_RETRY_ATTEMPTS = 4
_RETRY_BASE_DELAY_S = 2.0
_RETRYABLE_CODES = {429, 500, 502, 503, 504}

T = TypeVar("T")


def _with_retry(call: Callable[[], T]) -> T:
    """Mandatory agent passes (Part 4) mean every real run now depends on
    this call succeeding - a single transient 429/5xx used to silently
    degrade the whole run to its no-LLM fallback. Only retries genuinely
    transient API errors (rate limit, server error); a bad prompt or an
    auth failure raises immediately, unretried."""
    from google.genai import errors as genai_errors

    last_error: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return call()
        except genai_errors.APIError as exc:
            if exc.code not in _RETRYABLE_CODES or attempt == _RETRY_ATTEMPTS - 1:
                raise
            last_error = exc
            time.sleep(_RETRY_BASE_DELAY_S * (2**attempt))
    assert last_error is not None
    raise last_error


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
        response = _with_retry(
            lambda: client.models.generate_content(model=self.model, contents=prompt, config=config)
        )
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
        response = _with_retry(
            lambda: client.models.generate_content(model=self.model, contents=prompt, config=config)
        )
        text = response.text
        if not text:
            raise LLMError("Gemini returned an empty text response.")
        return text
