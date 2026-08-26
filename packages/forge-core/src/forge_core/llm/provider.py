"""LLM provider abstraction.

Every place in the pipeline that calls an LLM (semantic profiling, binding
proposals, generation, self-critique) goes through this interface, never
through `google-genai` directly. That keeps the door open for a second
provider and — more importantly — makes cassette record/replay (see
cassette.py) a drop-in wrapper for deterministic tests and CI.

The two *agents* (`agentic/binding_agent.py`, `agentic/data_agent.py`) are
the one exception: they construct `ChatGoogleGenerativeAI` directly, since
tool-using agent traces aren't cassette-recordable. `AgentCallRecorder`
closes that gap for cost telemetry — the agent passes it in its invoke
config and a caller reads `.summary()` afterwards (see the orchestrator,
which emits one StageEvent per agent invocation).
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from langchain_core.callbacks import BaseCallbackHandler


class UsageTracker:
    """Accumulates token usage across calls on a single provider instance.

    The agents report usage through `AgentCallRecorder` (a LangChain
    callback); the `LLMProvider` path has no equivalent hook, so providers
    mix this in and the orchestrator drains it per stage. Draining rather
    than reading keeps attribution honest: whatever has accrued since the
    last drain belongs to the stage that just ran."""

    def __init__(self) -> None:
        self._usage = {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "llm_calls": 0}

    def record_usage(self, input_tokens: int, output_tokens: int, thinking_tokens: int = 0) -> None:
        self._usage["input_tokens"] += input_tokens
        self._usage["output_tokens"] += output_tokens
        self._usage["thinking_tokens"] += thinking_tokens
        self._usage["llm_calls"] += 1

    def drain_usage(self) -> dict[str, int]:
        """Return usage accrued since the last drain, and reset."""
        drained = dict(self._usage)
        self._usage = dict.fromkeys(self._usage, 0)
        return drained


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


def _usage_from_response(response: Any) -> dict[str, int]:
    """Extract cumulative token counts from an `on_llm_end` LLMResult. The
    LangChain-google-genai adapter puts them on each generation's AIMessage
    (`usage_metadata`), with thinking tokens split out under
    `output_token_details`. Returns zeros when the shape differs (e.g. a
    non-chat model or a provider that reports no usage), never raises."""
    total = {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0}
    for generations in getattr(response, "generations", None) or []:
        for generation in generations:
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None) or {}
            total["input_tokens"] += usage.get("input_tokens", 0) or 0
            total["output_tokens"] += usage.get("output_tokens", 0) or 0
            details = usage.get("output_token_details") or {}
            total["thinking_tokens"] += details.get("thinking", 0) or details.get("reasoning", 0) or 0
    return total


class AgentCallRecorder(BaseCallbackHandler):
    """LangChain callback handler that accumulates token/tool/step usage for
    one `create_agent` invocation. Attach via
    `config={"callbacks": [recorder]}` on `agent.invoke`, then read
    `recorder.summary()` after invoke returns.

    Without this, the two agents' `ChatGoogleGenerativeAI` calls are the only
    LLM spend invisible to `RunRecord` telemetry (everything else goes
    through `LLMProvider`, which the orchestrator already accounts for)."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self.llm_calls = 0
        self.tool_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.thinking_tokens = 0

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        self.llm_calls += 1

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        self.tool_calls += 1

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        usage = _usage_from_response(response)
        self.input_tokens += usage["input_tokens"]
        self.output_tokens += usage["output_tokens"]
        self.thinking_tokens += usage["thinking_tokens"]

    def summary(self) -> dict[str, float]:
        return {
            "steps": self.llm_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
            "wall_seconds": round(time.monotonic() - self._started, 3),
        }


__all__ = ["AgentCallRecorder", "LLMError", "LLMProvider", "UsageTracker"]
