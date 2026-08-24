"""Aggregates per-question QuestionResult lists into the metrics table P1-09
promised. `false_confidence_rate` is the one the review says to run the
programme on - it maps directly to commercial risk.
"""

from __future__ import annotations

from typing import Any

from evals.harness.scoring import QuestionResult


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(100 * numerator / denominator, 1)


def summarize(results: list[QuestionResult]) -> dict[str, Any]:
    total = len(results)
    answered = [r for r in results if r.error is None]
    negatives = [r for r in results if r.category == "negative"]
    tool_scored = [r for r in results if r.tool_selected_correctly is not None]

    return {
        "total_questions": total,
        "errored": total - len(answered),
        "answer_accuracy": _pct(sum(1 for r in answered if r.passed), len(answered)),
        "capability_coverage": _pct(len(answered), total),
        "false_confidence_rate": _pct(sum(1 for r in results if r.false_confidence), total),
        "refusal_correctness": _pct(sum(1 for r in negatives if r.passed), len(negatives)),
        "tool_selection_precision": _pct(
            sum(1 for r in tool_scored if r.tool_selected_correctly), len(tool_scored)
        ),
        "by_category": {
            cat: _pct(
                sum(1 for r in results if r.category == cat and r.passed),
                sum(1 for r in results if r.category == cat),
            )
            for cat in sorted({r.category for r in results})
        },
    }


def build_report(all_results: dict[str, list[QuestionResult]], *, generated_at: str) -> dict[str, Any]:
    flat = [r for results in all_results.values() for r in results]
    return {
        "generated_at": generated_at,
        "datasets": {
            name: {"summary": summarize(results), "questions": [r.model_dump() for r in results]}
            for name, results in all_results.items()
        },
        "summary": summarize(flat),
    }


def diff_summaries(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "answer_accuracy",
        "capability_coverage",
        "false_confidence_rate",
        "refusal_correctness",
        "tool_selection_precision",
    )
    out = {}
    for k in keys:
        b, c = baseline.get(k), current.get(k)
        out[k] = {"baseline": b, "current": c, "delta": None if b is None or c is None else round(c - b, 1)}
    return out


__all__ = ["build_report", "diff_summaries", "summarize"]
