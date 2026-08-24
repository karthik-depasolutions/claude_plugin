from __future__ import annotations

from pathlib import Path

from forge_core.binding.gate import gate_bindings
from forge_core.classification import load_pack
from forge_core.models.bindings import ColumnBinding, SchemaBindings, TableBinding
from forge_core.models.kpi import CompiledKpi, KpiDefsFile

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"


def _bindings(*columns: ColumnBinding) -> SchemaBindings:
    return SchemaBindings(
        pack_slug="test",
        data_source_id="test",
        tables=[TableBinding(alias="fact", physical="srcdb.fact")],
        columns=list(columns),
        allowed_tables=["srcdb.fact"],
    )


def _kpi(kpi_id: str, sql: str) -> CompiledKpi:
    return CompiledKpi(
        id=kpi_id, label=kpi_id, description="", unit="count", sql=sql, source_kpi_id=kpi_id
    )


def _kpi_defs(*kpis: CompiledKpi) -> KpiDefsFile:
    return KpiDefsFile(pack_slug="test", generated_at="now", kpis=list(kpis))


def test_needs_confirmation_binding_used_by_a_kpi_is_gated():
    binding = ColumnBinding(
        role="revenue_amount", table_alias="fact", physical="score", confidence=0.45,
        evidence="test evidence", source="deterministic", needs_confirmation=True,
        alternatives=[("amount_inr", 0.3)],
    )
    kpi_defs = _kpi_defs(_kpi("total_revenue", 'SELECT SUM("score") FROM fact'))
    pack = load_pack(PACKS_ROOT / "edtech")

    questions = gate_bindings(_bindings(binding), pack, kpi_defs)

    assert len(questions) == 1
    q = questions[0]
    assert q.id == "binding:revenue_amount"
    assert q.role == "revenue_amount"
    assert q.physical == "score"
    assert q.kpis_affected == ["total_revenue"]
    assert q.alternatives == [("amount_inr", 0.3)]
    assert "score" in q.question


def test_needs_confirmation_binding_unused_by_any_kpi_is_never_gated():
    """The exact edtech case: revenue_amount->score is low-confidence, but no
    KPI references it, so asking about it would be a gate nobody could act
    on meaningfully - the point of the "only what's in use" rule."""
    binding = ColumnBinding(
        role="revenue_amount", table_alias="fact", physical="score", confidence=0.45,
        evidence="test evidence", source="deterministic", needs_confirmation=True,
    )
    kpi_defs = _kpi_defs(_kpi("total_enrollments", 'SELECT COUNT(*) FROM fact'))
    pack = load_pack(PACKS_ROOT / "edtech")

    questions = gate_bindings(_bindings(binding), pack, kpi_defs)

    assert questions == []


def test_confident_binding_is_never_gated_even_if_used():
    binding = ColumnBinding(
        role="revenue_amount", table_alias="fact", physical="amount_inr", confidence=0.9,
        evidence="test evidence", source="deterministic", needs_confirmation=False,
    )
    kpi_defs = _kpi_defs(_kpi("total_revenue", 'SELECT SUM("amount_inr") FROM fact'))
    pack = load_pack(PACKS_ROOT / "edtech")

    questions = gate_bindings(_bindings(binding), pack, kpi_defs)

    assert questions == []


def test_human_override_is_never_gated_even_if_flagged():
    """Sticky per the plan - resolver.py never actually sets both together,
    but the gate itself must not trust needs_confirmation alone."""
    binding = ColumnBinding(
        role="revenue_amount", table_alias="fact", physical="score", confidence=0.45,
        evidence="test evidence", source="human_override", needs_confirmation=True,
    )
    kpi_defs = _kpi_defs(_kpi("total_revenue", 'SELECT SUM("score") FROM fact'))
    pack = load_pack(PACKS_ROOT / "edtech")

    questions = gate_bindings(_bindings(binding), pack, kpi_defs)

    assert questions == []


def test_multiple_kpis_referencing_the_same_gated_role_are_all_listed():
    binding = ColumnBinding(
        role="transaction_status", table_alias="fact", physical="status", confidence=0.56,
        evidence="test evidence", source="deterministic", needs_confirmation=True,
    )
    kpi_defs = _kpi_defs(
        _kpi("completion_rate", 'SELECT AVG(CASE WHEN "status" = \'completed\' THEN 1 ELSE 0 END) FROM fact'),
        _kpi("dropout_rate", 'SELECT AVG(CASE WHEN "status" = \'dropped\' THEN 1 ELSE 0 END) FROM fact'),
        _kpi("total_enrollments", 'SELECT COUNT(*) FROM fact'),
    )
    pack = load_pack(PACKS_ROOT / "edtech")

    questions = gate_bindings(_bindings(binding), pack, kpi_defs)

    assert len(questions) == 1
    assert set(questions[0].kpis_affected) == {"completion_rate", "dropout_rate"}
