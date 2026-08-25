from __future__ import annotations

from pathlib import Path

from forge_core.ingestion.registry import ingest
from forge_core.models.data_map import ColumnMapEntry, DataMap, EntityMapEntry
from forge_core.profiling import build_structural_only

DATASETS_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "datasets"


def test_edtech_flags_score_as_ambiguous_with_real_distribution_evidence():
    """This is the exact evidence a distribution-aware check needs to catch
    score->revenue_amount (review P0.2): bounded 67.5-95.5, no currency
    fingerprint - nothing like money."""
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    dm = structural.data_map
    assert dm is not None
    assert "enrollments.score" in dm.ambiguous_columns

    score = next(c for e in dm.entities if e.name == "enrollments" for c in e.columns if c.name == "score")
    assert score.format_fingerprint is None
    assert score.p50 is not None
    assert score.min_value == "67.5"
    assert score.max_value == "95.5"


def test_edtech_verified_joins_appear_in_the_map():
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    dm = structural.data_map
    pairs = {(e.from_table, e.to_table) for e in dm.edges}
    assert ("enrollments", "courses") in pairs
    assert ("enrollments", "students") in pairs


def test_bookings_amount_column_is_ambiguous_without_a_name_based_currency_guess(bookings_csv: Path):
    """amount_inr has no currency symbol in its raw values (BIGINT column,
    values like 699/1799/2999) - a bare numeric column is exactly the
    "score bound to revenue_amount" shape, so it must stay ambiguous and
    route to the agent rather than being resolved by the name "amount"
    (Tier S/A triage - see docs/adr; this fingerprint used to be name-
    derived via a since-removed _CURRENCY_NAME_HINTS regex)."""
    ds = ingest(bookings_csv)
    structural = build_structural_only(ds)
    dm = structural.data_map
    amount = next(c for e in dm.entities for c in e.columns if c.name == "amount_inr")
    assert amount.format_fingerprint is None
    assert amount.ambiguous is True


def test_pii_columns_never_populate_top_values(bookings_csv: Path):
    ds = ingest(bookings_csv)
    structural = build_structural_only(ds)
    dm = structural.data_map
    for entity in dm.entities:
        for col in entity.columns:
            if col.is_likely_pii:
                assert col.top_values == [], f"{col.name} leaked PII into top_values"


def test_single_table_source_still_gets_a_data_map(bookings_csv: Path):
    """A single-table source has no entity_graph (ADR 0001), but the map is
    still built - the agent needs it regardless of table count."""
    ds = ingest(bookings_csv)
    structural = build_structural_only(ds)
    assert structural.entity_graph is None
    assert structural.data_map is not None
    assert structural.data_map.entities[0].role == "fact"


# --- to_prompt() budgeting --------------------------------------------------


def _synthetic_entity(name: str, n_columns: int) -> EntityMapEntry:
    columns = [
        ColumnMapEntry(
            name=f"col_{i}", dtype="VARCHAR", null_pct=0.0, cardinality=10, distinct_ratio=0.1,
            guessed_role="categorical", ambiguous=False,
        )
        for i in range(n_columns)
    ]
    return EntityMapEntry(name=name, role="dimension", grain="test", row_count=100, columns=columns)


def test_to_prompt_degrades_unambiguous_columns_under_a_tight_budget():
    # 200 tables x 10 columns each - the PHASE_2.md acceptance criterion is
    # "under 30k TOKENS" (~4 chars/token, so ~120k chars) - budget in chars
    # here, well under that token-equivalent ceiling.
    entities = [_synthetic_entity(f"table_{i}", 10) for i in range(200)]
    dm = DataMap(entities=entities, edges=[], ambiguous_columns=[])

    full = dm.to_prompt(char_budget=10_000_000)  # forces the full-detail path unconditionally
    terse = dm.to_prompt(char_budget=10)  # forces the terse path unconditionally
    assert len(terse) < len(full)  # degrading actually shrinks the render
    assert len(dm.to_prompt(char_budget=30_000)) < 120_000

    # No table silently dropped - every table name still appears, even in
    # the terse render.
    for entity in entities:
        assert f"## {entity.name}" in terse


def test_to_prompt_keeps_full_detail_when_it_fits():
    entities = [_synthetic_entity("small_table", 3)]
    dm = DataMap(entities=entities, edges=[], ambiguous_columns=[])
    rendered = dm.to_prompt(char_budget=30_000)
    assert "dtype=VARCHAR" in rendered or "VARCHAR" in rendered
