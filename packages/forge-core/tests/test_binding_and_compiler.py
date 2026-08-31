from __future__ import annotations

from pathlib import Path

from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.ingestion.registry import ingest
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

    bindings = resolve_bindings(profile, pack)
    assert bindings.unresolved_roles == []
    assert bindings.column("revenue_amount") is not None
    assert bindings.column("revenue_amount").physical == "amount_inr"
    # free_text columns are not analytically bindable/projectable.
    assert "customer_name" in bindings.denied_columns

    kpi_defs = compile_all(pack, bindings)
    assert kpi_defs.skipped == []
    assert len(kpi_defs.kpis) == len(pack.kpis)

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


def test_every_table_is_exposed_even_when_the_tables_are_unrelated(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "orders.csv").write_text("order_id,amount\n1,10\n2,20\n3,30\n", encoding="utf-8")
    (src / "weather.csv").write_text("city,temp_c\nA,20\nB,25\n", encoding="utf-8")
    (src / "staff.csv").write_text("staff_id,shift\n1,am\n2,pm\n", encoding="utf-8")

    profile = _profile_for(src)
    bindings = resolve_bindings(profile, load_pack(PACKS_ROOT / "generic-analytics"))

    assert len(bindings.allowed_tables) == 3
    assert bindings.relationships == []  # no shared keys -> not an error, just empty


def test_verified_relationships_are_carried_into_the_bindings(retail_orders_dir: Path):
    profile = _profile_for(retail_orders_dir)
    bindings = resolve_bindings(profile, load_pack(PACKS_ROOT / "retail-ecommerce"))

    assert len(bindings.allowed_tables) == 3
    pairs = {(r.from_ref, r.to_ref) for r in bindings.relationships}
    assert ("orders.customer_id", "customers.customer_id") in pairs


def test_cross_table_kpi_binds_via_a_relationship_and_compiles_a_join(retail_orders_dir: Path):
    profile = _profile_for(retail_orders_dir)
    pack = load_pack(PACKS_ROOT / "retail-ecommerce")
    bindings = resolve_bindings(profile, pack)

    tier = bindings.column("customer_tier")
    assert tier is not None
    assert tier.table_alias == "customers" and tier.source == "cross_table"
    assert any(t.alias == "customers" and t.join_on for t in bindings.tables)

    kpi_defs = compile_all(pack, bindings)
    xk = kpi_defs.get("revenue_by_customer_tier")
    assert xk is not None, f"cross-table KPI was skipped: {kpi_defs.skipped}"
    assert "LEFT JOIN" in xk.sql.upper()

    con = open_session(profile.source)
    try:
        rows = con.execute(xk.sql).fetchdf()
        assert rows.shape[0] >= 1 and "revenue" in rows.columns
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
