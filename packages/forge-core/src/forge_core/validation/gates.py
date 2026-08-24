"""P2-06 — deterministic verification gates for every claim the P2-05 data-
understanding agent produces. The agent is not trusted; it is checked. This
is what makes it safe to let an LLM decide freely, and it is the layer
`boringdata/boring-semantic-layer`'s public demo has no analogue for at all
(its LLM-proposed joins ship unverified; see PHASE_2.md §0.4).

Every gate here is a pure function over real data or a real tool result -
no LLM anywhere in this module.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from forge_core.agentic.investigation_tools import Coverage, RelationshipFact
from forge_core.models.claims import ColumnClaim, RelationClaim
from forge_core.models.entity_graph import JoinEdge
from forge_core.models.metrics import AggOp
from forge_core.models.schema_profile import ColumnProfile

MIN_RELATION_OVERLAP = 0.5
MIN_VALUE_SET_COVERAGE_WARNING = 0.8

_NON_ADDITIVE_UNITS = {"percent", "rate", "percentage", "score", "ratio"}
_ADDITIVE_ONLY_OPS = {AggOp.MEAN, AggOp.MEDIAN, AggOp.MIN, AggOp.MAX, AggOp.NUNIQUE, AggOp.COUNT}


class GateVerdict(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    UNVERIFIABLE = "unverifiable"


class GateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: GateVerdict
    reasons: list[str] = Field(default_factory=list)


class ClaimOutcome(str, Enum):
    ACCEPTED = "accepted"
    RETRY = "retry"
    ESCALATED = "escalated"


def route(verdict: GateVerdict, *, attempts: int, max_attempts: int = 2) -> ClaimOutcome:
    """The only three outcomes: verified, retried, or escalated to the human
    gate (P1-08). Never accept an unverified claim; never silently drop one
    either."""
    if verdict == GateVerdict.VERIFIED:
        return ClaimOutcome.ACCEPTED
    if verdict == GateVerdict.FAILED and attempts < max_attempts:
        return ClaimOutcome.RETRY
    return ClaimOutcome.ESCALATED


# --- V1: evidence exists -----------------------------------------------------

_NUMBER_RE = re.compile(r"-?\d+\.?\d*")
_QUOTED_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")
_EVIDENCE_STOPWORDS = {
    "that", "this", "these", "those", "with", "from", "have", "has", "had", "which", "what",
    "identified", "indicating", "indicates", "represents", "representing", "appears", "appear",
    "looks", "looked", "genuinely", "really", "actually", "being", "been", "also", "based",
    "real", "data", "map", "tool", "result", "results", "call", "called", "value", "values",
    "column", "columns", "table", "tables", "role", "matches", "matching", "match", "perfectly",
    "expected", "lifecycle", "shows", "shown", "confirms", "confirmed", "here", "there", "than",
}


def verify_evidence_exists(evidence: list[str], real_evidence_log: list[str]) -> GateResult:
    """Every evidence string a claim cites must be grounded in a real fact
    from this run - either an actual tool result, or the precomputed data
    map itself (P2-03; seeded into the log by the caller), since most
    columns are legitimately decided from the map alone without a tool
    call. An LLM naturally paraphrases rather than copying text verbatim,
    so this checks for grounded FACTS, not verbatim string containment:
    any number or quoted value the evidence cites must appear in the real
    log outright (that's where confabulation actually shows up - a specific
    number or value nobody ever computed); prose with no such hard facts
    still needs at least one distinctive, non-generic word grounded in the
    log, so pure invention with no real anchor still fails."""
    if not evidence:
        return GateResult(verdict=GateVerdict.FAILED, reasons=["claim cites no evidence"])

    combined = " ".join(real_evidence_log)
    combined_lower = combined.lower()
    ungrounded: list[str] = []
    for e in evidence:
        if e in combined:
            continue
        hard_facts = _NUMBER_RE.findall(e) + [g1 or g2 for g1, g2 in _QUOTED_RE.findall(e)]
        if hard_facts:
            missing_facts = [f for f in hard_facts if f and f not in combined]
            if missing_facts:
                ungrounded.append(e)
            continue
        tokens = {
            t for t in re.findall(r"[a-z][a-z0-9_]{3,}", e.lower()) if t not in _EVIDENCE_STOPWORDS
        }
        if tokens and not any(t in combined_lower for t in tokens):
            ungrounded.append(e)

    if ungrounded:
        return GateResult(
            verdict=GateVerdict.FAILED,
            reasons=[f"evidence not grounded in any real result this run: {ungrounded}"],
        )
    return GateResult(verdict=GateVerdict.VERIFIED)


# --- V2: distribution plausibility -------------------------------------------


def _as_float(value: float | str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def verify_distribution_plausibility(claim: ColumnClaim, col: ColumnProfile) -> GateResult:
    """Extends P1-04's rule table with the agent's own declared unit -
    catches the exact revenue_amount->score shape mechanically, independent
    of whether the agent's semantic reasoning was right."""
    unit = (claim.unit or "").strip().lower()
    min_v, max_v = _as_float(col.min_value), _as_float(col.max_value)
    reasons: list[str] = []

    if unit in ("currency", "inr", "usd", "money", "revenue"):
        if min_v is not None and max_v is not None and 0 <= min_v and max_v <= 100:
            reasons.append(
                f"declared unit {claim.unit!r} but values are bounded [{min_v}, {max_v}] - "
                "that shape is a score/percentage, not money"
            )
        if min_v is not None and min_v < 0:
            reasons.append(f"declared unit {claim.unit!r} but min value is negative ({min_v})")
    elif unit in ("percent", "rate", "percentage"):
        if min_v is not None and min_v < 0:
            reasons.append(f"declared unit {claim.unit!r} but min value is negative ({min_v})")
        if max_v is not None and max_v > 100:
            reasons.append(f"declared unit {claim.unit!r} but max value exceeds 100 ({max_v})")
    elif unit == "score":
        if max_v is not None and max_v > 1000:
            reasons.append(f"declared unit 'score' but max value is {max_v} - implausibly large")
    elif unit == "count":
        if min_v is not None and min_v < 0:
            reasons.append(f"declared unit 'count' but min value is negative ({min_v})")
    elif unit == "time" or claim.kind == "time":
        if col.cardinality < 2:
            reasons.append("declared a time unit but cardinality < 2 - a time series needs >=2 points")

    return GateResult(verdict=GateVerdict.FAILED if reasons else GateVerdict.VERIFIED, reasons=reasons)


# --- V3: aggregation validity -------------------------------------------------


def verify_aggregation_validity(claim: ColumnClaim) -> GateResult:
    """SUM on a non-additive measure (a ratio, a percentage, a score) fails
    - catches the summed-percentages bug before it can exist."""
    unit = (claim.unit or "").strip().lower()
    if unit in _NON_ADDITIVE_UNITS and AggOp.SUM in claim.valid_aggregations:
        return GateResult(
            verdict=GateVerdict.FAILED,
            reasons=[f"SUM is not valid for a {claim.unit!r}-unit column - only {sorted(o.value for o in _ADDITIVE_ONLY_OPS)}"],
        )
    return GateResult(verdict=GateVerdict.VERIFIED)


# --- V4: relation verification ------------------------------------------------


def verify_relation(fact: RelationshipFact) -> GateResult:
    """A claimed join is a hypothesis until a query says otherwise - exactly
    where BSL's default path ships an unverified LLM guess (PHASE_2.md
    §0.4)."""
    if fact.overlap_ratio < MIN_RELATION_OVERLAP:
        return GateResult(
            verdict=GateVerdict.FAILED,
            reasons=[
                f"overlap_ratio {fact.overlap_ratio} below {MIN_RELATION_OVERLAP} - "
                f"{fact.from_table}.{fact.from_column} does not genuinely relate to "
                f"{fact.to_table}.{fact.to_column}"
            ],
        )
    return GateResult(verdict=GateVerdict.VERIFIED)


# --- V5: value-set coverage --------------------------------------------------


def verify_value_set_coverage(coverage: Coverage) -> GateResult:
    """Any candidate absent from the real distinct values is rejected
    outright - catches 'active' being counted as completed. Coverage below
    80% of real values is a warning, not a rejection - still accepted, but
    flagged for a human to sanity-check."""
    if coverage.unmatched_candidates:
        return GateResult(
            verdict=GateVerdict.FAILED,
            reasons=[
                f"candidate(s) not present in the real observed values: {coverage.unmatched_candidates}"
            ],
        )
    reasons = []
    if coverage.real_distinct_values:
        real_matched_ratio = len(coverage.matched) / len(coverage.real_distinct_values)
        if real_matched_ratio < MIN_VALUE_SET_COVERAGE_WARNING:
            reasons.append(
                f"matched values cover only {real_matched_ratio:.0%} of the real distinct "
                "values on this column - worth a second look, not blocking"
            )
    return GateResult(verdict=GateVerdict.VERIFIED, reasons=reasons)


# --- V6: fan-out safety --------------------------------------------------------


def verify_fan_out_safety(path: list[JoinEdge]) -> GateResult:
    """No metric may traverse a fan-out edge unless the measure is
    de-duplicated - the guard against silently double/triple-counted
    revenue."""
    risky = [e for e in path if e.fan_out_risk]
    if risky:
        return GateResult(
            verdict=GateVerdict.FAILED,
            reasons=[
                f"{e.from_table}.{e.from_column} -> {e.to_table}.{e.to_column} "
                f"({e.cardinality}) can duplicate a measure at the far end"
                for e in risky
            ],
        )
    return GateResult(verdict=GateVerdict.VERIFIED)


# --- Orchestrators -------------------------------------------------------------


def verify_column_claim(
    claim: ColumnClaim, col: ColumnProfile, real_evidence_log: list[str]
) -> GateResult:
    """V1 + V2 + V3, combined - the full check for one ColumnClaim."""
    for gate in (
        verify_evidence_exists(claim.evidence, real_evidence_log),
        verify_distribution_plausibility(claim, col),
        verify_aggregation_validity(claim),
    ):
        if gate.verdict != GateVerdict.VERIFIED:
            return gate
    return GateResult(verdict=GateVerdict.VERIFIED)


def verify_relation_claim(
    claim: RelationClaim, fact: RelationshipFact, real_evidence_log: list[str]
) -> GateResult:
    """V1 + V4, combined - the full check for one RelationClaim."""
    evidence_check = verify_evidence_exists(claim.evidence, real_evidence_log)
    if evidence_check.verdict != GateVerdict.VERIFIED:
        return evidence_check
    return verify_relation(fact)


__all__ = [
    "ClaimOutcome",
    "GateResult",
    "GateVerdict",
    "route",
    "verify_aggregation_validity",
    "verify_column_claim",
    "verify_distribution_plausibility",
    "verify_evidence_exists",
    "verify_fan_out_safety",
    "verify_relation",
    "verify_relation_claim",
    "verify_value_set_coverage",
]
