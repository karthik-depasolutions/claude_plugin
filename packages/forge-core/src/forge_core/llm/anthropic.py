"""Anthropic implementation of `LLMProvider`. A second production backend so
an outage or an enterprise "no Google" policy does not stop the pipeline -
see `forge_core.llm.get_provider` for how it is selected (`FORGE_LLM_PROVIDER`).

The Messages API has no `response_mime_type="application/json"` equivalent, so
`generate_json` enforces JSON at the prompt level and retries `json.loads`
once with a corrective follow-up before failing.
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.llm.usage import TokenUsage

_JSON_SYSTEM_SUFFIX = (
    "\n\nRespond with a single valid JSON value and nothing else - no prose, no markdown "
    "fences, no leading or trailing text."
)
_DEFAULT_MODEL = "claude-sonnet-5"
_MAX_TOKENS = 8192
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.2,
        *,
        role: str = "generation",
        usage: TokenUsage | None = None,
    ) -> None:
        self.model = model or _DEFAULT_MODEL
        self.temperature = temperature
        self._role = role
        self._usage = usage
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise LLMError(
                "ANTHROPIC_API_KEY is not set. Set it, or FORGE_LLM_PROVIDER=gemini with "
                "GEMINI_API_KEY, or FORGE_LLM_CASSETTE_MODE=replay."
            )
        self._client = None
        self._client_lock = threading.Lock()

    def _get_client(self):
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    import anthropic

                    self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _record_usage(self, response: Any) -> None:
        if self._usage is None:
            return
        meta = getattr(response, "usage", None)
        if meta is None:
            return
        prompt = int(getattr(meta, "input_tokens", 0) or 0)
        completion = int(getattr(meta, "output_tokens", 0) or 0)
        self._usage.record(
            model=self.model, role=self._role, input_tokens=prompt, output_tokens=completion
        )

    def _message(self, prompt: str, system: str | None) -> str:
        client = self._get_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=_MAX_TOKENS,
            temperature=self.temperature,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
        )
        self._record_usage(response)
        parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        text = "".join(parts).strip()
        if not text:
            raise LLMError("Anthropic returned an empty response.")
        return text

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict[str, Any]:
        sys_json = (system or "") + _JSON_SYSTEM_SUFFIX
        text = self._message(prompt, sys_json)
        for attempt in (0, 1):
            try:
                return json.loads(_FENCE_RE.sub("", text).strip())
            except json.JSONDecodeError as exc:
                if attempt == 1:
                    raise LLMError(f"Anthropic returned invalid JSON: {exc}\n---\n{text[:2000]}") from exc
                text = self._message(
                    "Your previous reply was not valid JSON. Reply again with only the JSON value.",
                    sys_json,
                )
        raise LLMError("unreachable")  # the loop always returns or raises

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return self._message(prompt, system)
