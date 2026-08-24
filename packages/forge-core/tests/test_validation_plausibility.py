from __future__ import annotations

from pathlib import Path

from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.ingestion.registry import ingest
from forge_core.llm.provider import LLMError
from forge_core.models.bindings import ColumnBinding, SchemaBindings, TableBinding
from forge_core.models.common import CheckStatus
from forge_core.models.schema_profile import ColumnProfile, SchemaProfile, StructuralProfile
from forge_core.profiling import build_structural_only
from forge_core.validation.plausibility import check_binding_plausibility

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"
DATASETS_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "datasets"


class _StubJudge:
    """Returns a fixed {"implausible": [...]} response, ignoring the prompt -
    exercises the grounding/parsing logic deterministically, without a real
    LLM call."""

    def __init__(self, implausible: list[dict]):
        self._implausible = implausible

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
        return {"implausible": self._implausible}

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return ""


class _RaisingProvider:
    def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
        raise LLMError("boom")

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return ""


class _MalformedProvider:
    """A response that isn't even a dict - genuinely malformed, unlike a
    dict that's merely missing the "implausible" key (which legitimately
    means "nothing flagged", same convention as every other call site's
    `raw.get(key, [])`)."""

    def generate_json(self, prompt: str, *, system: str | None = None) -> list:
        return []

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return ""


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


def _bindings_with(role: str, physical: str, confidence: float = 0.5) -> SchemaBindings:
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


def test_judge_flagging_a_real_role_fails_with_a_grounded_message():
    col = _col(name="score", min_value=0, max_value=100)
    profile = _profile_with_single_column(col)
    bindings = _bindings_with("revenue_amount", "score", confidence=0.45)
    pack = load_pack(PACKS_ROOT / "edtech")

    judge = _StubJudge(
        [{"role": "revenue_amount", "reason": "values are bounded 0-100, which looks like a score"}]
    )
    result = check_binding_plausibility(bindings, profile, pack, judge)

    assert result.status == CheckStatus.FAIL
    assert "revenue_amount" in result.issues[0].location
    assert "score" in result.issues[0].message
    assert "0.45" in result.issues[0].message


def test_judge_naming_no_problems_passes():
    col = _col(name="amount_inr", min_value=299, max_value=15000)
    profile = _profile_with_single_column(col)
    bindings = _bindings_with("revenue_amount", "amount_inr", confidence=0.9)
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")

    result = check_binding_plausibility(bindings, profile, pack, _StubJudge([]))
    assert result.status == CheckStatus.PASS


def test_judge_naming_an_unbound_role_is_dropped_not_trusted():
    """A role the judge invents (never in bindings_block) must never produce
    an issue - the same grounding invariant every other LLM call site in
    this pipeline enforces (binding proposers, the KPI proposer, question
    generation)."""
    col = _col(name="amount_inr", min_value=299, max_value=15000)
    profile = _profile_with_single_column(col)
    bindings = _bindings_with("revenue_amount", "amount_inr", confidence=0.9)
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")

    judge = _StubJudge([{"role": "totally_invented_role", "reason": "made up"}])
    result = check_binding_plausibility(bindings, profile, pack, judge)
    assert result.status == CheckStatus.PASS


def test_judge_response_missing_a_reason_is_dropped():
    col = _col(name="score", min_value=0, max_value=100)
    profile = _profile_with_single_column(col)
    bindings = _bindings_with("revenue_amount", "score")
    pack = load_pack(PACKS_ROOT / "edtech")

    judge = _StubJudge([{"role": "revenue_amount", "reason": ""}])
    result = check_binding_plausibility(bindings, profile, pack, judge)
    assert result.status == CheckStatus.PASS


def test_no_bound_roles_passes_trivially_without_calling_the_judge():
    profile = _profile_for(DATASETS_ROOT / "bookings.csv")
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")
    empty_bindings = SchemaBindings(
        pack_slug="test", data_source_id="test", tables=[], columns=[], allowed_tables=[]
    )
    result = check_binding_plausibility(empty_bindings, profile, pack, _StubJudge([]))
    assert result.status == CheckStatus.PASS


# --- Degraded fallback: no provider, or the provider fails/misbehaves ------
# Not a general rule table - only the one catastrophic, well-known pattern
# (a money-meaning role bound to a 0-100-bounded column), so --no-llm runs
# and transient LLM failures aren't left with zero protection.


def test_fallback_catches_the_real_edtech_score_to_revenue_binding():
    profile = _profile_for(DATASETS_ROOT / "edtech.sqlite")
    pack = load_pack(PACKS_ROOT / "edtech")
    bindings = resolve_bindings(profile, pack)

    result = check_binding_plausibility(bindings, profile, pack, None)
    assert result.status == CheckStatus.FAIL
    revenue_issue = next(i for i in result.issues if "revenue_amount" in i.location)
    assert "score" in revenue_issue.message
    assert "0-100" in revenue_issue.message


def test_fallback_passes_a_plausible_currency_binding():
    profile = _profile_for(DATASETS_ROOT / "bookings.csv")
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")
    bindings = resolve_bindings(profile, pack)

    result = check_binding_plausibility(bindings, profile, pack, None)
    assert result.status == CheckStatus.PASS, result.issues


def test_fallback_ignores_roles_whose_description_has_no_money_language():
    col = _col(name="pct", min_value=0, max_value=100)
    profile = _profile_with_single_column(col)
    bindings = _bindings_with("some_exotic_role", "pct")
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")

    result = check_binding_plausibility(bindings, profile, pack, None)
    assert result.status == CheckStatus.PASS


def test_dict_response_missing_the_implausible_key_means_nothing_flagged():
    """Not malformed - a dict without "implausible" means the judge reported
    nothing to flag, the same convention every other call site's
    `raw.get(key, [])` already follows. Only a non-dict response is
    genuinely malformed and degrades to the fallback."""
    col = _col(name="score", min_value=0, max_value=100)
    profile = _profile_with_single_column(col)
    bindings = _bindings_with("revenue_amount", "score")
    pack = load_pack(PACKS_ROOT / "edtech")

    class _KeylessProvider:
        def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
            return {}

        def generate_text(self, prompt: str, *, system: str | None = None) -> str:
            return ""

    result = check_binding_plausibility(bindings, profile, pack, _KeylessProvider())
    assert result.status == CheckStatus.PASS


def test_llm_error_degrades_to_the_fallback_not_a_silent_skip():
    profile = _profile_for(DATASETS_ROOT / "edtech.sqlite")
    pack = load_pack(PACKS_ROOT / "edtech")
    bindings = resolve_bindings(profile, pack)

    result = check_binding_plausibility(bindings, profile, pack, _RaisingProvider())
    assert result.status == CheckStatus.FAIL  # the fallback still catches it


def test_malformed_judge_response_degrades_to_the_fallback():
    profile = _profile_for(DATASETS_ROOT / "edtech.sqlite")
    pack = load_pack(PACKS_ROOT / "edtech")
    bindings = resolve_bindings(profile, pack)

    result = check_binding_plausibility(bindings, profile, pack, _MalformedProvider())
    assert result.status == CheckStatus.FAIL
