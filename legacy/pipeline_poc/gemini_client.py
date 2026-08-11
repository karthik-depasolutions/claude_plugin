"""Gemini API client wrapper."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-2.5-flash"


def get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is required. "
            "Set it before running the pipeline."
        )
    return genai.Client(api_key=api_key)


def _extract_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            return json.loads(match.group(1).strip())
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if match:
            return json.loads(match.group(1))
        raise


def generate_json(prompt: str, *, model: str = DEFAULT_MODEL) -> Any:
    client = get_client()
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )
    return _extract_json(response.text or "")


def generate_text(prompt: str, *, model: str = DEFAULT_MODEL) -> str:
    client = get_client()
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.2),
    )
    return (response.text or "").strip()
