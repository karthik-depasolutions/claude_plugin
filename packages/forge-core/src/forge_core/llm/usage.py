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

# USD per 1M tokens, (input, output). Public list prices as of 2026-01; a model
# id we don't recognise contributes $0 rather than a wrong guess.
# ponytail: hardcoded table, move to config/env if prices need to change without a deploy.
_PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (15.00, 75.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
}


def _model_price(model: str) -> tuple[float, float]:
    """Exact match, then a prefix match (ids often carry a date/version suffix)."""
    if model in _PRICE_PER_MTOK:
        return _PRICE_PER_MTOK[model]
    for known, price in _PRICE_PER_MTOK.items():
        if model.startswith(known):
            return price
    return (0.0, 0.0)


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

    def estimated_cost_usd(self) -> float:
        """Sum per-model (input, output) at list price. 0.0 for unknown models."""
        with self._lock:
            total = 0.0
            for model, b in self.by_model.items():
                in_price, out_price = _model_price(model)
                total += b["input_tokens"] / 1_000_000 * in_price
                total += b["output_tokens"] / 1_000_000 * out_price
            return round(total, 4)

    def snapshot(self) -> dict:
        """A plain dict for JSON storage / API responses."""
        with self._lock:
            model_snapshot = {k: dict(v) for k, v in self.by_model.items()}
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
            "estimated_cost_usd": self.estimated_cost_usd(),
            "by_model": model_snapshot,
            "by_role": {k: dict(v) for k, v in self.by_role.items()},
        }
