"""Generator-side copy of the runtime's assertion policy
(mis_mcp_runtime.engine.assertions).

forge-core and mcp-runtime are deliberately independent packages (see
docs/architecture.md §4.6) — the runtime ships inside every generated plugin
and must never import forge_core. So the whitelist validator is duplicated
here rather than shared: forge-core runs it at the CanonicalKpi boundary so a
malicious/LLM-authored assertion never reaches kpi_defs.json, and the runtime
re-runs its own copy before evaluating (defence in depth).

A drift test (tests/test_assertion_policy_parity.py) asserts both whitelists
stay identical, so this duplication cannot silently diverge.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_CMP = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
_FUNCS = {"abs": abs, "min": min, "max": max, "round": round, "len": len}

_ALLOWED = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Call,
    ast.And,
    ast.Or,
    ast.Not,
    ast.USub,
    ast.UAdd,
    *_BIN,
    *_CMP,
)

_MAX_ASSERTION_LENGTH = 200


class AssertionPolicyError(ValueError):
    """Raised when an assertion expression violates the whitelist policy."""


def _allowed_node_types() -> tuple[type[ast.AST], ...]:
    return _ALLOWED


def _allowed_call_names() -> frozenset[str]:
    return frozenset(_FUNCS)


def validate_assertion(expr: str) -> None:
    """Raises AssertionPolicyError on anything disallowed. Cap at 200 chars."""
    if len(expr) > _MAX_ASSERTION_LENGTH:
        raise AssertionPolicyError(f"assertion exceeds {_MAX_ASSERTION_LENGTH} characters")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise AssertionPolicyError(f"not a valid expression: {exc}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise AssertionPolicyError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise AssertionPolicyError("only abs/min/max/round/len may be called")


def evaluate_assertion(expr: str, row: dict[str, Any]) -> bool:
    validate_assertion(expr)
    return bool(_eval(ast.parse(expr, mode="eval").body, row))


def _eval(node: ast.AST, row: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in row:
            return row[node.id]
        raise AssertionPolicyError(f"unknown column {node.id!r} in assertion")
    if isinstance(node, ast.BinOp):
        return _BIN[type(node.op)](_eval(node.left, row), _eval(node.right, row))
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, row)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
        if isinstance(node.op, ast.Not):
            return not value
        raise AssertionPolicyError(f"disallowed unary operator {type(node.op).__name__}")
    if isinstance(node, ast.Compare):
        left = _eval(node.left, row)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _eval(comparator, row)
            if not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        values = (_eval(v, row) for v in node.values)
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Call):
        return _FUNCS[node.func.id](*[_eval(arg, row) for arg in node.args])
    raise AssertionPolicyError(f"disallowed node {type(node).__name__}")


__all__ = [
    "AssertionPolicyError",
    "_allowed_call_names",
    "_allowed_node_types",
    "evaluate_assertion",
    "validate_assertion",
]