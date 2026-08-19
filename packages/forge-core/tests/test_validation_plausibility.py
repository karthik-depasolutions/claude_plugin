from __future__ import annotations

from pathlib import Path

from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.ingestion.registry import ingest
from forge_core.models.bindings import ColumnBinding, SchemaBindings, TableBinding
from forge_core.models.common import CheckStatus
from forge_core.models.schema_profile import ColumnProfile, SchemaProfile, StructuralProfile
from forge_core.profiling import build_structural_only
from forge_core.validation.plausibility import check_binding_plausibility, rule_for_role

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"
DATASETS_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "datasets"


def _profile_for(source_path: Path) -> SchemaProfile:
    ds = ingest(source_path)
    structural = build_structural_only(ds)
    return SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)


def _col(**overrides) -> ColumnProfile:
    defaults = dict(
        table="fact",
        name="score",
        dtype="DOUBLE",
        null_percent=0.0,
        cardinality=100,
        distinct_ratio=1.0,
        guessed_role="numeric",
        min_value=0,
        max_value=100,
    )
    defaults.update(overrides)
    return ColumnProfile(**defaults)


def _bindings_with(profile: SchemaProfile, role: str, physical: str, confidence: float = 0.5):
    return SchemaBindings(
        pack_slug="test",
        data_source_id="test",
        tables=[TableBinding(alias="fact", physical="srcdb.fact")],
        columns=[
            ColumnBinding(
                role=role, table_alias="fact", physical=physical, confidence=confidence, evidence="test"
            )
        ],
        allowed_tables=["srcdb.fact"],
    )


def _profile_with_single_column(col: ColumnProfile) -> SchemaProfile:
    source = _profile_for(DATASETS_ROOT / "bookings.csv").source
    return SchemaProfile(
        data_source_id="x",
        structural=StructuralProfile(columns=[col]),
        semantic=None,
        source=source,
    )


def test_real_edtech_score_to_revenue_binding_fails():
    """The shipped edtech artifact bound a student test score (0-100) to the
    revenue role. That exact binding must now be a hard validation failure."""
    profile = _profile_for(DATASETS_ROOT / "edtech.sqlite")
    pack = load_pack(PACKS_ROOT / "edtech")
    bindings = resolve_bindings(profile, pack)

    result = check_binding_plausibility(bindings, profile)
    assert result.status == CheckStatus.FAIL
    revenue_issue = next(i for i in result.issues if "revenue_amount" in i.location)
    assert "score" in revenue_issue.message
    assert "0-100" in revenue_issue.message


def test_plausible_currency_binding_passes():
    profile = _profile_for(DATASETS_ROOT / "bookings.csv")
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")
    bindings = resolve_bindings(profile, pack)

    result = check_binding_plausibility(bindings, profile)
    assert result.status == CheckStatus.PASS, result.issues


def test_amount_outside_0_100_range_passes():
    profile = _profile_for(DATASETS_ROOT / "bookings.csv")
    bindings = _bindings_with(profile, "revenue_amount", "amount_inr", confidence=0.9)
    # amount_inr is 299..15000 - a real currency shape
    result = check_binding_plausibility(bindings, profile)
    assert result.status == CheckStatus.PASS


def test_single_value_date_fails():
    col = _col(name="enrolled_on", cardinality=1, guessed_role="date")
    profile = _profile_with_single_column(col)
    bindings = _bindings_with(profile, "transaction_date", "enrolled_on")
    result = check_binding_plausibility(bindings, profile)
    assert result.status == CheckStatus.FAIL
    assert "transaction_date" in result.issues[0].location


def test_percent_with_max_above_100_fails():
    col = _col(name="pct", min_value=0, max_value=150, guessed_role="numeric")
    profile = _profile_with_single_column(col)
    bindings = _bindings_with(profile, "completion_rate", "pct")
    result = check_binding_plausibility(bindings, profile)
    assert result.status == CheckStatus.FAIL
    assert any("100" in i.message for i in result.issues)


def test_unknown_role_with_no_rule_passes():
    """Rules are opt-in: an unlisted role can never fail by default."""
    profile = _profile_for(DATASETS_ROOT / "bookings.csv")
    bindings = _bindings_with(profile, "some_exotic_role", "amount_inr")
    result = check_binding_plausibility(bindings, profile)
    assert result.status == CheckStatus.PASS


def test_rule_for_role_matching():
    assert rule_for_role("revenue_amount") is not None
    assert rule_for_role("score") is not None
    assert rule_for_role("transaction_date") is not None
    assert rule_for_role("total_revenue_amount") is not None
    assert rule_for_role("mystery_thing") is None