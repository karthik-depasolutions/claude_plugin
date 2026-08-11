"""Deterministic industry-pack matcher (architecture doc §4.3).

Classification is treated as a matching/routing problem, not a generative
one — there is no LLM call in this module. Low-confidence results are
surfaced to the customer/operator for confirmation rather than guessed.
"""

from __future__ import annotations

from forge_core.classification.signatures import ProfileSignature, extract_signature
from forge_core.models.industry_pack import ClassificationResult, IndustryMatch, IndustryPack
from forge_core.models.schema_profile import SchemaProfile

AUTO_ACCEPT_THRESHOLD = 0.45
BLEND_MARGIN = 0.12
BLEND_MIN_CONFIDENCE = 0.3

_W_ENTITY = 0.35
_W_COLUMN = 0.35
_W_ROLE = 0.2
_W_TABLE_COUNT = 0.1


def _fraction_and_hits(hints: list[str], haystack: frozenset[str]) -> tuple[float, list[str]]:
    if not hints:
        return 0.0, []
    hits = []
    for hint in hints:
        hint_l = hint.lower()
        if any(hint_l in value for value in haystack):
            hits.append(hint)
    return len(hits) / len(hints), hits


def score_pack(pack: IndustryPack, sig: ProfileSignature) -> IndustryMatch:
    entity_frac, entity_hits = _fraction_and_hits(pack.signature.entity_name_hints, sig.table_names)
    column_frac, column_hits = _fraction_and_hits(pack.signature.column_name_hints, sig.column_names)

    role_hints = pack.signature.required_role_categories
    role_hits = [r for r in role_hints if r in sig.role_categories]
    role_frac = (len(role_hits) / len(role_hints)) if role_hints else 0.0

    lo, hi = pack.signature.table_count_range
    table_count_score = 1.0 if lo <= sig.table_count <= hi else 0.3

    confidence = (
        _W_ENTITY * entity_frac
        + _W_COLUMN * column_frac
        + _W_ROLE * role_frac
        + _W_TABLE_COUNT * table_count_score
    )

    matched_signals = [f"entity hint {h!r} matched a table name" for h in entity_hits]
    matched_signals += [f"column hint {h!r} matched a column name" for h in column_hits]
    matched_signals += [f"required role {r!r} present in data" for r in role_hits]
    if lo <= sig.table_count <= hi:
        matched_signals.append(f"table count {sig.table_count} within expected range [{lo}, {hi}]")

    return IndustryMatch(
        pack_slug=pack.slug, confidence=round(confidence, 4), matched_signals=matched_signals
    )


def classify(profile: SchemaProfile, packs: list[IndustryPack]) -> ClassificationResult:
    if not packs:
        raise ValueError("No industry packs available to classify against.")

    sig = extract_signature(profile)
    ranked = sorted((score_pack(p, sig) for p in packs), key=lambda m: m.confidence, reverse=True)

    primary = ranked[0]
    secondary = None
    if (
        len(ranked) > 1
        and ranked[1].confidence >= BLEND_MIN_CONFIDENCE
        and primary.confidence - ranked[1].confidence <= BLEND_MARGIN
    ):
        secondary = ranked[1].pack_slug

    return ClassificationResult(
        ranked_matches=ranked,
        primary_pack_slug=primary.pack_slug,
        secondary_pack_slug=secondary,
        requires_customer_confirmation=primary.confidence < AUTO_ACCEPT_THRESHOLD,
    )
