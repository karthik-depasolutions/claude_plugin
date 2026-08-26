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


def _apply_evidence(
    ranked: list[IndustryMatch], slug: str, confidence: float, signal: str
) -> list[IndustryMatch]:
    """Raise one pack's score to `confidence` if that is higher, recording
    why. Deliberately `max` and not a sum: an agent's read is corroborating
    evidence for a pack the deterministic matcher also considered, never a
    licence to push a pack past every structural signal at once."""
    updated: list[IndustryMatch] = []
    for match in ranked:
        if match.pack_slug == slug:
            updated.append(
                IndustryMatch(
                    pack_slug=match.pack_slug,
                    confidence=round(max(confidence, match.confidence), 4),
                    matched_signals=[signal] + match.matched_signals,
                )
            )
        else:
            updated.append(match)
    return updated


def classify(
    profile: SchemaProfile,
    packs: list[IndustryPack],
    business_context: dict | None = None,
) -> ClassificationResult:
    """`business_context` is the Context Discovery Agent's handoff payload
    (see BusinessContext.to_handoff, spec §22). Its `domain` is the agent's
    own evidence-backed industry claim, so classification consumes it here
    rather than the agent's investigation being thrown away and this stage
    re-deriving industry from name hints alone.

    It stays *evidence*, not an override: the deterministic matcher still
    ranks every pack, and a domain the agent names that scores near-zero
    structurally will still lose - and a run whose top match is weak still
    pauses for the customer to confirm."""
    if not packs:
        raise ValueError("No industry packs available to classify against.")

    sig = extract_signature(profile)
    ranked = [score_pack(p, sig) for p in packs]
    valid_slugs = {p.slug for p in packs}

    if business_context:
        domain = business_context.get("domain")
        domain_confidence = float(business_context.get("domain_confidence") or 0.0)
        if domain and domain in valid_slugs and domain_confidence > 0:
            grain = business_context.get("record_grain") or "investigated the data"
            ranked = _apply_evidence(
                ranked,
                domain,
                domain_confidence,
                f"Context Discovery Agent identified this domain ({domain_confidence:.0%} confidence): {grain}",
            )

    # Incorporate AI semantic / LLM exploration confidence if available
    ai_guess = profile.semantic.suggested_industry if profile.semantic else None
    if ai_guess and ai_guess.pack_slug_guess:
        updated_ranked = []
        for m in ranked:
            if m.pack_slug == ai_guess.pack_slug_guess:
                # Use the AI semantic analysis confidence
                combined_confidence = round(max(ai_guess.confidence, m.confidence), 4)
                signals = [f"AI semantic analysis: {ai_guess.reasoning[:90]}..."] + m.matched_signals
                updated_ranked.append(
                    IndustryMatch(
                        pack_slug=m.pack_slug,
                        confidence=combined_confidence,
                        matched_signals=signals,
                    )
                )
            else:
                updated_ranked.append(m)
        ranked = updated_ranked

    ranked.sort(key=lambda m: m.confidence, reverse=True)

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
