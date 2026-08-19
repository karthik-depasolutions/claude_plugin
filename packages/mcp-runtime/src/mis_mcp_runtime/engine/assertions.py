"""Assertion evaluation without eval(). Only a fixed whitelist of AST nodes
is permitted; anything else raises before evaluation. Attribute access, calls
to non-whitelisted names, subscripts, comprehensions, and lambdas are all
rejected at parse time, so the subclass-traversal escape is unreachable.

Assertion strings are LLM-authored on the --agent path and can originate in
untrusted data cells (see forge_core.compiler.kpi_proposer), so this is a
security boundary: the expressions are evaluated inside Claude Desktop's local
process. Restricted-builtins eval() is not a sandbox; this module is.
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
    """Exposed for the cross-package drift test: forge_core's duplicated
    validator must permit exactly the same node set as this module."""
    return _ALLOWED


def _allowed_call_names() -> frozenset[str]:
    return frozenset(_FUNCS)


def validate_assertion(expr: str) -> None:
    """Call at GENERATION time, in CanonicalKpi's field validator. A rejected
    assertion never reaches kpi_defs.json, so a customer never ships one."""
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
    validate_assertion(expr)  # defence in depth: check again at runtime
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