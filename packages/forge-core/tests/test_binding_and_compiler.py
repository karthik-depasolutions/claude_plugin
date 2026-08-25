from __future__ import annotations

from pathlib import Path

from forge_core.binding import resolve_bindings
from forge_core.binding.resolver import _judge_value_set
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.ingestion.registry import ingest
from forge_core.llm.provider import LLMError
from forge_core.models.schema_profile import SchemaProfile
from forge_core.profiling import build_structural_only
from forge_core.runtime_session import open_session

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"


def _profile_for(source_path: Path) -> SchemaProfile:
    ds = ingest(source_path)
    structural = build_structural_only(ds)
    return SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)


def test_bookings_binds_and_compiles_all_kpis(bookings_csv: Path):
    profile = _profile_for(bookings_csv)
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")

    # Deterministic-only (no agent): "location" has zero name-token overlap
    # with its real column "city" and, since GEOGRAPHIC is no longer a
    # name-derived role (city|state|country|... used to grant it purely
    # from the column name - one of the illegitimate heuristics this pass
    # removes), it correctly can no longer resolve without real semantic
    # judgment. That's the mandatory agent's job on a real run - verified
    # live against this exact fixture; here we're testing the deterministic
    # floor stays honest rather than silently guessing.
    bindings = resolve_bindings(profile, pack)
    assert bindings.unresolved_roles == ["location"]
    assert bindings.column("revenue_amount") is not None
    assert bindings.column("revenue_amount").physical == "amount_inr"
    # PII columns must never be bindable/projectable.
    assert "phone" in bindings.denied_columns
    assert "customer_name" in bindings.denied_columns

    kpi_defs = compile_all(pack, bindings)
    assert set(kpi_defs.skipped) == {"bookings_by_location"}
    assert len(kpi_defs.kpis) == len(pack.kpis) - 1

    con = open_session(profile.source)
    try:
        for kpi in kpi_defs.kpis:
            result = con.execute(kpi.sql).fetchdf()
            assert result.shape[0] >= 1, f"{kpi.id} returned no rows"
    finally:
        con.close()


def test_retail_orders_binds_to_orders_table_not_line_items(retail_orders_dir: Path):
    profile = _profile_for(retail_orders_dir)
    pack = load_pack(PACKS_ROOT / "retail-ecommerce")

    bindings = resolve_bindings(profile, pack)
    fact_binding = bindings.table("fact")
    assert "orders" in fact_binding.physical  # view name is src_orders

    kpi_defs = compile_all(pack, bindings)
    assert kpi_defs.get("total_revenue") is not None

    con = open_session(profile.source)
    try:
        for kpi in kpi_defs.kpis:
            con.execute(kpi.sql).fetchdf()
    finally:
        con.close()


def test_edtech_sqlite_binds_and_compiles(edtech_sqlite: Path):
    profile = _profile_for(edtech_sqlite)
    pack = load_pack(PACKS_ROOT / "edtech")

    bindings = resolve_bindings(profile, pack)
    fact_binding = bindings.table("fact")
    assert "enrollments" in fact_binding.physical

    kpi_defs = compile_all(pack, bindings)
    assert len(kpi_defs.kpis) >= 4

    con = open_session(profile.source)
    try:
        for kpi in kpi_defs.kpis:
            result = con.execute(kpi.sql).fetchdf()
            assert result.shape[0] >= 1
    finally:
        con.close()


def test_denied_columns_never_appear_in_compiled_sql(bookings_csv: Path):
    profile = _profile_for(bookings_csv)
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")
    bindings = resolve_bindings(profile, pack)
    kpi_defs = compile_all(pack, bindings)

    for kpi in kpi_defs.kpis:
        for denied in bindings.denied_columns:
            assert f'"{denied}"' not in kpi.sql


def test_generic_pack_never_binds_a_denied_column(tmp_path: Path):
    source = tmp_path / "users.csv"
    source.write_text(
        "user_uuid,full_name,username,xp,last_active_date\n"
        "u1,Alice Smith,alice,10,2026-01-01\n"
        "u2,Bob Jones,bob,20,2026-02-01\n",
        encoding="utf-8",
    )
    profile = _profile_for(source)
    pack = load_pack(PACKS_ROOT / "generic-analytics")

    bindings = resolve_bindings(profile, pack)
    bound_columns = {column.physical for column in bindings.columns}

    assert "full_name" in bindings.denied_columns
    assert bound_columns.isdisjoint(bindings.denied_columns)

    kpi_defs = compile_all(pack, bindings)
    assert "count_by_category" in kpi_defs.skipped
    for kpi in kpi_defs.kpis:
        for denied in bindings.denied_columns:
            assert f'"{denied}"' not in kpi.sql


# --- Value-set resolution: agent-judged, not substring-hint-matched --------
# The edtech pack's own value_set_hints literally lists "active" as a hint
# for "completed_values" - a pack-authoring mistake no smarter string
# matcher would have caught either. The fix is judging real meaning against
# real observed values, not hint-list membership.


class _StubValueSetJudge:
    def __init__(self, matched: list[str]):
        self._matched = matched

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
        return {"matched": self._matched}

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return ""


class _RaisingProvider:
    def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
        raise LLMError("boom")

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return ""


def test_judge_excludes_active_from_completed_despite_the_bad_pack_hint():
    """Reproduces the real bug: edtech's value_set_hints lists "active" under
    "completed_values". A judge that reasons about meaning must reject it
    even though the pack's own hint list says otherwise."""
    judge = _StubValueSetJudge(matched=["completed", "passed"])
    matched, source = _judge_value_set(
        "completed_values", ["active", "completed", "dropped", "passed"], ["completed", "active", "passed"], judge
    )
    assert matched == ["completed", "passed"]
    assert "active" not in matched
    assert source == "llm_judged"


def test_judge_response_is_grounded_to_real_observed_values():
    """A value the judge invents (never in the real distinct list) must
    never be trusted - the same grounding invariant every other LLM call
    site in this pipeline enforces."""
    judge = _StubValueSetJudge(matched=["completed", "a_value_that_was_never_observed"])
    matched, source = _judge_value_set("completed_values", ["active", "completed"], [], judge)
    assert matched == ["completed"]
    assert source == "llm_judged"


def test_no_provider_falls_back_to_deterministic_hint_matching():
    matched, source = _judge_value_set(
        "completed_values", ["active", "completed", "dropped"], ["completed"], None
    )
    assert matched == ["completed"]
    assert source == "deterministic"


def test_llm_error_falls_back_to_deterministic_hint_matching():
    matched, source = _judge_value_set(
        "completed_values", ["active", "completed", "dropped"], ["completed"], _RaisingProvider()
    )
    assert matched == ["completed"]
    assert source == "deterministic"


# --- P1-08: binding confidence gate - resolver fallthrough --------------
# MIN_CONFIDENCE_RESOLVED (0.70) is stricter than MIN_BIND_CONFIDENCE (0.4).
# A tier's result below it no longer ends the search for that role - the
# best candidate found still ships (so a KPI that doesn't need it is never
# blocked), but flagged needs_confirmation=True with runner-up alternatives,
# for binding/gate.py to route into a human question when something depends
# on it.


class _ColumnProposingProvider:
    """A stub LLM provider whose propose_binding call always proposes the
    same column, regardless of prompt - exercises the LLM tier's place in
    the fallthrough without a real LLM call."""

    def __init__(self, column: str):
        self._column = column

    def generate_json(self, prompt: str, *, system: str | None = None) -> dict:
        return {"column": self._column, "confidence": 0.8, "reasoning": "stubbed"}

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return ""


def test_fk_shaped_roles_need_confirmation_without_the_agent(edtech_sqlite: Path):
    """student_ref->student_id and course_ref->course_id are foreign keys
    on THIS table (enrollments), not this table's own unique identifier -
    `is_likely_identifier` is now a genuine uniqueness fact rather than a
    `*_id`-name shortcut, so a bare FK column correctly does NOT self-
    certify as a confident identifier match without either real value
    evidence (a verified join, which the agent checks) or a human
    confirming it. Verified live: with the agent on (the default for a
    real run), both resolve at confidence 1.0, agent-verified - this test
    covers the deterministic floor staying honest, not silently guessing,
    when there's no agent to ask."""
    profile = _profile_for(edtech_sqlite)
    pack = load_pack(PACKS_ROOT / "edtech")

    bindings = resolve_bindings(profile, pack)

    for role in ("student_ref", "course_ref"):
        binding = bindings.column(role)
        assert binding is not None
        assert binding.needs_confirmation is True
        assert binding.confidence < 0.70


def test_low_confidence_binding_needs_confirmation_with_alternatives(edtech_sqlite: Path):
    """transaction_status->status is real evidence (edtech.sqlite) that never
    clears MIN_CONFIDENCE_RESOLVED on name/type overlap alone - it must ship
    flagged, with runner-up columns attached, not silently trusted."""
    profile = _profile_for(edtech_sqlite)
    pack = load_pack(PACKS_ROOT / "edtech")

    bindings = resolve_bindings(profile, pack)

    binding = bindings.column("transaction_status")
    assert binding is not None
    assert binding.confidence < 0.70
    assert binding.needs_confirmation is True
    assert binding.physical not in {name for name, _ in binding.alternatives}


def test_llm_proposal_below_threshold_still_needs_confirmation(edtech_sqlite: Path):
    """Even a successful LLM proposal doesn't auto-resolve a role - its fixed
    0.55 confidence never clears MIN_CONFIDENCE_RESOLVED, so it becomes the
    new best-so-far (if it beats the deterministic tier) but still ships
    needing confirmation, same as the deterministic-only case."""
    profile = _profile_for(edtech_sqlite)
    pack = load_pack(PACKS_ROOT / "edtech")
    # revenue_amount's deterministic candidate (score, 0.45) is weaker than
    # the LLM tier's fixed 0.55 - the LLM proposal should become best_so_far.
    provider = _ColumnProposingProvider("score")

    bindings = resolve_bindings(profile, pack, provider=provider)

    binding = bindings.column("revenue_amount")
    assert binding is not None
    assert binding.source == "llm_proposed"
    assert binding.confidence == 0.55
    assert binding.needs_confirmation is True


def test_edtech_value_set_resolution_end_to_end_with_stub_judge(edtech_sqlite: Path):
    """The real edtech schema + pack, with a judge that (unlike the old
    substring matcher) correctly excludes 'active' from 'completed_values'.
    The stub ignores the prompt and always returns the same verdict - this
    exercises the real resolve_bindings -> _resolve_value_sets ->
    _judge_value_set wiring end to end, not just the judge function alone."""
    profile = _profile_for(edtech_sqlite)
    pack = load_pack(PACKS_ROOT / "edtech")

    bindings = resolve_bindings(profile, pack, provider=_StubValueSetJudge(matched=["completed"]))

    value_set = bindings.value_set("completed_values")
    assert value_set is not None
    assert value_set.values == ["completed"]
    assert "active" not in value_set.values
    assert value_set.source == "llm_judged"
