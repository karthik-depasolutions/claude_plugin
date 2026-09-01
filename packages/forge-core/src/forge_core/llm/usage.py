"""Token accounting for one generation run.

A single `TokenUsage` is created per run and shared by the profiling,
generation and critique providers. Each provider calls `record(...)` after
every LLM response with the numbers the API itself reported (never an
estimate). It is thread-safe because `synthesis.py` fans per-table calls
across a pool that shares one provider instance.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

_ZERO = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0}


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    by_role: dict[str, dict[str, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record(
        self, *, model: str, role: str, input_tokens: int, output_tokens: int, total_tokens: int | None = None
    ) -> None:
        total = total_tokens if total_tokens else input_tokens + output_tokens
        with self._lock:
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.total_tokens += total
            self.calls += 1
            for key, bucket in ((model, self.by_model), (role, self.by_role)):
                slot = bucket.setdefault(key, dict(_ZERO))
                slot["input_tokens"] += input_tokens
                slot["output_tokens"] += output_tokens
                slot["total_tokens"] += total
                slot["calls"] += 1

    def snapshot(self) -> dict:
        """A plain dict for JSON storage / API responses."""
        with self._lock:
            return {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
                "calls": self.calls,
                "by_model": {k: dict(v) for k, v in self.by_model.items()},
                "by_role": {k: dict(v) for k, v in self.by_role.items()},
            }
