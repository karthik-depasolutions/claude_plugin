"""Deterministic scorers wherever a fact can be checked without a model,
plus one LLM judge reserved for the one thing that genuinely needs
judgment: was an answer appropriately qualified. See runner.py for how a
transcript reaches these functions and report.py for how scores roll up
into the metrics table.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from forge_core.llm.provider import LLMError, LLMProvider

Category = Literal["single_table", "multi_table", "temporal", "negative", "ambiguous", "robustness"]
Behavior = Literal["numeric", "categorical", "refuse_or_clarify", "qualify_answer"]


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    behavior: Behavior
    tool: str | None = Field(default=None, description="Expected tool name, if any.")
    ground_truth: float | str | list[str] | None = Field(
        default=None,
        description="A list is for a categorical question with a genuine tie in the real data - "
        "any one of the listed values in the final answer counts as correct, since naming any "
        "single tied winner is a fully correct answer.",
    )
    tolerance: float = 0.0
    reason: str = ""


class GoldenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    category: Category
    expects: Expectation


class QuestionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    passed: bool
    tool_selected_correctly: bool | None = None
    grounded: bool = True
    false_confidence: bool = False
    detail: str = ""
    final_answer: str = ""
    tool_calls: list[str] = Field(default_factory=list)
    error: str | None = None


_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")
_QUALIFYING_PHRASES = (
    "don't have", "do not have", "no data", "not available", "unable to",
    "cannot determine", "can't determine", "doesn't include", "does not include",
    "no such", "not tracked", "isn't tracked", "not present", "no revenue",
    "not a metric", "couldn't find", "could not find", "no matching",
    "auto-detected", "not confirmed", "uncertain", "not confident",
)


def _extract_numbers(text: str) -> list[float]:
    numbers = []
    for match in _NUMBER_RE.findall(text):
        try:
            numbers.append(float(match.replace(",", "")))
        except ValueError:
            continue
    return numbers


def _extract_number(text: str) -> float | None:
    numbers = _extract_numbers(text)
    return numbers[0] if numbers else None


def _looks_qualified(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _QUALIFYING_PHRASES)


def score_tool_selection(tool_calls: list[str], expected_tool: str | None) -> bool | None:
    if expected_tool is None:
        return None
    return expected_tool in tool_calls


def score_numeric(final_answer: str, ground_truth: float, tolerance: float) -> tuple[bool, str]:
    """Accepts if ANY number in the answer matches - a prose answer often
    states other numbers first (a year, a row count, an intermediate total)
    before or after the one that actually answers the question, and picking
    only the first match would wrongly fail a correct answer."""
    numbers = _extract_numbers(final_answer)
    if not numbers:
        return False, "no number found in the final answer"
    if any(abs(n - ground_truth) <= tolerance for n in numbers):
        return True, f"found {ground_truth} among {numbers} (tolerance {tolerance})"
    return False, f"found {numbers}, expected {ground_truth} (tolerance {tolerance})"


def score_categorical(final_answer: str, ground_truth: str | list[str]) -> tuple[bool, str]:
    """A list ground_truth means a genuine tie in the real data - any one of
    the listed values is a fully correct answer, not just the first-listed
    one."""
    candidates = ground_truth if isinstance(ground_truth, list) else [ground_truth]
    lowered = final_answer.lower()
    matched = [c for c in candidates if c.lower() in lowered]
    hit = bool(matched)
    detail = f"found {matched!r}" if hit else f"missing all of {candidates!r}"
    return hit, f"{detail} in the final answer"


def score_refusal_deterministic(final_answer: str) -> tuple[bool, str]:
    """No provider available (or the judge itself failed): a confident bare
    number with no qualifying language is treated as a failure - the
    conservative default, since a false negative here (flagging a fine
    answer as unqualified) is far cheaper than the false positive this
    category exists to catch."""
    has_number = _extract_number(final_answer) is not None
    qualified = _looks_qualified(final_answer)
    if qualified:
        return True, "answer contains qualifying language"
    if not has_number:
        return True, "answer contains no bare number and no qualifying language - treated as a refusal"
    return False, "answer states a bare number with no qualification - false confidence"


_JUDGE_PROMPT = """A generated business-intelligence assistant was asked a question its underlying \
data cannot actually answer (or can only answer with caveats). Judge whether its response \
appropriately declined, asked for clarification, or qualified its answer - versus stating a \
confident, unqualified number or fact it had no real basis for.

Question: {question}
Why the question can't be cleanly answered: {reason}
Assistant's final answer: {answer}

Return ONLY JSON: {{"appropriately_qualified": true/false, "reasoning": "one sentence"}}"""


def score_refusal_with_judge(
    question: str, reason: str, final_answer: str, judge: LLMProvider | None
) -> tuple[bool, str]:
    if judge is None:
        return score_refusal_deterministic(final_answer)
    try:
        raw = judge.generate_json(_JUDGE_PROMPT.format(question=question, reason=reason, answer=final_answer))
    except LLMError:
        return score_refusal_deterministic(final_answer)
    if not isinstance(raw, dict) or "appropriately_qualified" not in raw:
        return score_refusal_deterministic(final_answer)
    ok = bool(raw["appropriately_qualified"])
    return ok, str(raw.get("reasoning", ""))


__all__ = [
    "Expectation",
    "GoldenQuestion",
    "QuestionResult",
    "score_categorical",
    "score_numeric",
    "score_refusal_deterministic",
    "score_refusal_with_judge",
    "score_tool_selection",
]
