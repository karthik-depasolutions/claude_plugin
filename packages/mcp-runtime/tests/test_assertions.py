from __future__ import annotations

import pytest

from mis_mcp_runtime.engine.assertions import AssertionPolicyError, evaluate_assertion, validate_assertion

ACCEPTS = [
    "total >= 0",
    "pct >= 0 and pct <= 100",
    "abs(delta) < 10",
    "round(rate, 2) == rate",
]

REJECTS = [
    "().__class__.__bases__[0].__subclasses__()",
    "__import__('os').system('id')",
    "open('/etc/passwd').read()",
    "[x for x in ().__class__.__mro__]",
    "(lambda: 1)()",
    "row.__class__",
    "x" * 300,
]


@pytest.mark.parametrize("expr", ACCEPTS)
def test_accepts_safe_assertions(expr: str) -> None:
    validate_assertion(expr)


@pytest.mark.parametrize("expr", REJECTS)
def test_rejects_unsafe_assertions(expr: str) -> None:
    with pytest.raises(AssertionPolicyError):
        validate_assertion(expr)


def test_evaluate_truthy_expression() -> None:
    assert evaluate_assertion("total >= 0", {"total": 5}) is True


def test_evaluate_falsey_expression() -> None:
    assert evaluate_assertion("total >= 0", {"total": -5}) is False


def test_unknown_name_raises_rather_than_silently_passing() -> None:
    with pytest.raises(AssertionPolicyError):
        evaluate_assertion("mystery > 0", {"total": 5})


def test_calls_limited_to_whitelist() -> None:
    # abs/min/max/round/len are the only callable names
    with pytest.raises(AssertionPolicyError):
        validate_assertion("pow(total, 2) > 100")
    with pytest.raises(AssertionPolicyError):
        validate_assertion("total.sqrt()")  # attribute calls are disallowed too