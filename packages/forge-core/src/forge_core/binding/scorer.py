"""Deterministic canonical-role <-> physical-column scoring.

This is what turns "the pack needs a revenue_amount" into "bind it to
bookings.amount_inr" without any LLM call, for the common case where the
column names or types make the mapping obvious. See resolver.py for how
low-confidence roles fall through to the LLM proposer.

Scoring has three independent signals, combined so no single weak signal can
win a tie by accident:
  1. pack-authored `role_hints` (IndustryPack.role_hints) — an explicit
     synonym list an author supplies for genuinely ambiguous roles, e.g.
     'product_name' vs a column literally called 'package_name'.
  2. "core" token overlap — role/column name tokens with generic connector
     words (name, id, ref, amount, ...) stripped out first, so two columns
     that only share a generic word (e.g. two *_name columns) don't tie.
  3. structural type compatibility — does the column's guessed_role match
     what this kind of canonical role expects (currency, date, etc.)?
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from forge_core.models.common import ColumnRole
from forge_core.models.schema_profile import ColumnProfile

MIN_BIND_CONFIDENCE = 0.4

_GENERIC_TOKENS = {
    "name", "id", "ref", "amount", "date", "status", "flag", "value", "code", "type", "key",
}

_SUBSTRING_ROLE_HINTS: list[tuple[tuple[str, ...], frozenset[ColumnRole]]] = [
    (
        ("amount", "revenue", "volume", "total", "measure", "price", "value"),
        frozenset({ColumnRole.CURRENCY, ColumnRole.NUMERIC}),
    ),
    (("date", "_at", "_on"), frozenset({ColumnRole.DATE, ColumnRole.DATETIME})),
    (("status",), frozenset({ColumnRole.CATEGORICAL, ColumnRole.BOOLEAN_FLAG})),
    (("flag", "repeat"), frozenset({ColumnRole.BOOLEAN_FLAG, ColumnRole.CATEGORICAL})),
    (
        ("type", "channel", "category", "partner", "product", "course", "tier"),
        frozenset({ColumnRole.CATEGORICAL, ColumnRole.FREE_TEXT}),
    ),
    (("location", "city"), frozenset({ColumnRole.GEOGRAPHIC})),
    (("ref", "id"), frozenset({ColumnRole.IDENTIFIER})),
    (("score",), frozenset({ColumnRole.NUMERIC})),
]


def _token_set(text: str) -> set[str]:
    return {t for t in re.split(r"[_\s]+", text.lower()) if t}


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return (len(a & b) / len(union)) if union else 0.0


def _expected_roles(role_name: str) -> frozenset[ColumnRole]:
    lower = role_name.lower()
    expected: set[ColumnRole] = set()
    for substrings, roles in _SUBSTRING_ROLE_HINTS:
        if any(s in lower for s in substrings):
            expected |= roles
    return frozenset(expected)


@dataclass(frozen=True)
class ScoredCandidate:
    column: ColumnProfile
    confidence: float
    evidence: str


def score_column_for_role(
    role_name: str, column: ColumnProfile, hints: tuple[str, ...] = ()
) -> ScoredCandidate:
    col_lower = column.name.lower()
    hint_hit = any(h in col_lower for h in hints)

    role_tokens = _token_set(role_name)
    col_tokens = _token_set(column.name)
    core_role = role_tokens - _GENERIC_TOKENS
    core_col = col_tokens - _GENERIC_TOKENS
    core_overlap = _jaccard(core_role, core_col) if core_role and core_col else 0.0
    full_overlap = _jaccard(role_tokens, col_tokens)

    name_score = 1.0 if hint_hit else (0.6 * core_overlap + 0.4 * full_overlap)

    expected = _expected_roles(role_name)
    if not expected:
        category_bonus = 0.5
    elif column.guessed_role in expected:
        category_bonus = 1.0
    else:
        category_bonus = 0.25

    confidence = round(min(1.0, 0.55 * name_score + 0.45 * category_bonus), 4)
    evidence = (
        f"{'pack hint match' if hint_hit else f'core-token overlap {core_overlap:.2f}'} "
        f"for role {role_name!r}; column role {column.guessed_role.value} "
        f"{'matches' if category_bonus >= 1.0 else 'does not clearly match'} expected type"
    )
    return ScoredCandidate(column=column, confidence=confidence, evidence=evidence)


def best_candidate(
    role_name: str, candidates: list[ColumnProfile], hints: tuple[str, ...] = ()
) -> ScoredCandidate | None:
    scored = [score_column_for_role(role_name, c, hints) for c in candidates]
    if not scored:
        return None
    return max(scored, key=lambda s: s.confidence)
