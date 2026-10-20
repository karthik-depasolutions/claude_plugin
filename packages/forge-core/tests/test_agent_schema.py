"""Agent Schema mode: catalog read, profiling budgets, synonyms, and the
sharded BM25 catalog. All offline - a SQLite fixture built in-test stands in
for the "large database with declared foreign keys" case."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from forge_core.ingestion.catalog import read_catalog
from forge_core.ingestion.registry import ingest
from forge_core.llm.provider import LLMError
from forge_core.models.common import SourceKind
from forge_core.packaging.catalog_shard import bm25_search, write_catalog_shards
from forge_core.profiling import build_structural_only
from forge_core.profiling.budget import (
    AGENT_SCHEMA_BUDGET,
    FAST_AGENT_SCHEMA_BUDGET,
    SMALL_DB_BUDGET,
)
from forge_core.profiling.relationships import detect_relationships
from forge_core.profiling.synthesis import build_schema_model
from forge_core.runtime_session import open_session
from forge_core.testing import FakeLLMProvider


@pytest.fixture
def fk_sqlite(tmp_path: Path) -> Path:
    """customers <- orders <- order_items, with real FK constraints."""
    db = tmp_path / "shop.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, tier TEXT);
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
            total REAL, status TEXT
        );
        CREATE TABLE order_items (
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
            sku TEXT, qty INTEGER
        );
        INSERT INTO customers VALUES (1,'A','gold'),(2,'B','silver'),(3,'C','gold');
        INSERT INTO orders VALUES (1,1,10.0,'paid'),(2,1,20.0,'paid'),(3,2,5.0,'open');
        INSERT INTO order_items VALUES (1,1,'x',2),(2,1,'y',1),(3,2,'x',4),(4,3,'z',1);
        """
    )
    con.commit()
    con.close()
    return db


# --------------------------------------------------------------------------- #
# read_catalog
# --------------------------------------------------------------------------- #
def test_read_catalog_returns_declared_pks_and_fks(fk_sqlite: Path) -> None:
    ds = ingest(fk_sqlite)
    con = open_session(ds)
    try:
        facts = read_catalog(con, source_kind=SourceKind.SQLITE, catalog_alias="srcdb")
    finally:
        con.close()

    assert facts.primary_keys["customers"] == ["id"]
    assert facts.primary_keys["orders"] == ["id"]
    pairs = facts.declared_fk_pairs()
    assert ("orders", "customer_id", "customers", "id") in pairs
    assert ("order_items", "order_id", "orders", "id") in pairs
    assert "name" in facts.not_null.get("customers", set())


def test_read_catalog_empty_for_file_source(retail_orders_dir: Path) -> None:
    ds = ingest(retail_orders_dir)
    con = open_session(ds)
    try:
        facts = read_catalog(con, source_kind=ds.kind, catalog_alias="srcdb")
    finally:
        con.close()
    assert facts.empty


def test_read_catalog_rejects_unsafe_schema_name(fk_sqlite: Path) -> None:
    ds = ingest(fk_sqlite)
    con = open_session(ds)
    try:
        facts = read_catalog(
            con, source_kind=SourceKind.POSTGRES, catalog_alias="srcdb", schema="public; DROP"
        )
    finally:
        con.close()
    assert facts.empty


# --------------------------------------------------------------------------- #
# detect_relationships: declared FK short-circuit + probe budget
# --------------------------------------------------------------------------- #
def test_declared_fk_emitted_without_probing(fk_sqlite: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import forge_core.profiling.relationships as rel_mod

    probes: list[tuple[str, str]] = []
    real_overlap = rel_mod._overlap_ratio

    def counting_overlap(con, ct, cc, pt, pc):  # type: ignore[no-untyped-def]
        probes.append((f"{ct}.{cc}", f"{pt}.{pc}"))
        return real_overlap(con, ct, cc, pt, pc)

    monkeypatch.setattr(rel_mod, "_overlap_ratio", counting_overlap)

    ds = ingest(fk_sqlite)
    con = open_session(ds)
    try:
        facts = read_catalog(con, source_kind=SourceKind.SQLITE, catalog_alias="srcdb")
        cols = build_structural_only(ds, catalog=facts).columns
        rels = detect_relationships(ds, cols, con, catalog=facts, budget=AGENT_SCHEMA_BUDGET)
    finally:
        con.close()

    declared = {
        (r.from_table, r.from_column, r.to_table, r.to_column)
        for r in rels
        if r.source == "declared"
    }
    assert ("orders", "customer_id", "customers", "id") in declared
    assert ("order_items", "order_id", "orders", "id") in declared
    # The declared FK pairs must not have been value-overlap probed.
    assert ("orders.customer_id", "customers.id") not in probes
    assert ("order_items.order_id", "orders.id") not in probes


def test_probe_budget_caps_inferred_relationship_queries(fk_sqlite: Path) -> None:
    ds = ingest(fk_sqlite)
    con = open_session(ds)
    tight = SMALL_DB_BUDGET.__class__(max_inferred_relationship_probes=0)
    try:
        cols = build_structural_only(ds).columns
        rels = detect_relationships(ds, cols, con, catalog=None, budget=tight)
    finally:
        con.close()
    # With zero probe budget and no catalog, no inferred relationship survives.
    assert all(r.source == "declared" for r in rels)  # i.e. none, since catalog=None


class _CountingCon:
    """Forwards to a real DuckDB connection, counting SELECT DISTINCT calls."""

    def __init__(self, inner) -> None:  # type: ignore[no-untyped-def]
        self._inner = inner
        self.distinct_calls = 0

    def execute(self, sql, *a, **k):  # type: ignore[no-untyped-def]
        if "SELECT DISTINCT" in sql:
            self.distinct_calls += 1
        return self._inner.execute(sql, *a, **k)


def test_agent_budget_skips_per_column_distinct_queries(fk_sqlite: Path) -> None:
    """The big remote-DB win: with max_distinct_sample_columns=0 no per-column
    SELECT DISTINCT is issued, and sample values fall back to the ingest rows."""
    from forge_core.profiling.structural import build_structural_profile

    ds = ingest(fk_sqlite)
    con = open_session(ds)
    try:
        capped = _CountingCon(con)
        prof = build_structural_profile(ds, capped, max_distinct_sample_columns=0)  # type: ignore[arg-type]
        uncapped = _CountingCon(con)
        build_structural_profile(ds, uncapped, max_distinct_sample_columns=None)  # type: ignore[arg-type]
    finally:
        con.close()

    assert capped.distinct_calls == 0
    assert uncapped.distinct_calls > 0  # legacy path still probes per column
    tier = next(c for c in prof.columns if c.table == "customers" and c.name == "tier")
    assert tier.sample_values  # still populated, from the ingest sample rows


def test_fast_budget_does_the_minimum_but_keeps_declared_keys(
    fk_sqlite: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAST_AGENT_SCHEMA_BUDGET: zero inferred probes, zero value-set queries,
    zero pattern mining, no composite grain - but declared FK/PK still land."""
    import forge_core.profiling.relationships as rel_mod

    probes: list[object] = []
    monkeypatch.setattr(
        rel_mod, "_overlap_ratio", lambda *a: (probes.append(1), 0.0)[1]  # type: ignore[no-untyped-def]
    )

    ds = ingest(fk_sqlite)
    con = open_session(ds)
    try:
        facts = read_catalog(con, source_kind=SourceKind.SQLITE, catalog_alias="srcdb")
        prof = build_structural_only(
            ds, catalog=facts, budget=FAST_AGENT_SCHEMA_BUDGET
        )
    finally:
        con.close()

    assert probes == []  # no value-overlap probing at all
    assert prof.value_sets == {}  # no enum-decode scans
    assert all(len(p.correlations) == 0 for p in [prof.patterns])  # no pattern mining
    assert prof.patterns.functional_dependencies == [] and prof.patterns.mismatches == []
    declared = {
        (r.from_table, r.from_column, r.to_table, r.to_column)
        for r in prof.relationships
        if r.source == "declared"
    }
    assert ("orders", "customer_id", "customers", "id") in declared
    # single-column PK grain still detected (composite probing is what's skipped)
    grains = {g.table: g.grain_columns for g in prof.grains}
    assert grains["customers"] == ["id"]


def test_fast_budget_is_a_stricter_subset_of_the_agent_budget() -> None:
    assert FAST_AGENT_SCHEMA_BUDGET.max_inferred_relationship_probes == 0
    assert FAST_AGENT_SCHEMA_BUDGET.max_valueset_columns == 0
    assert FAST_AGENT_SCHEMA_BUDGET.max_pattern_tables == 0
    assert FAST_AGENT_SCHEMA_BUDGET.run_quality_analysis is False
    assert AGENT_SCHEMA_BUDGET.run_quality_analysis is True  # normal mode keeps it


def test_file_source_relationships_unchanged_without_catalog(retail_orders_dir: Path) -> None:
    ds = ingest(retail_orders_dir)
    con = open_session(ds)
    try:
        cols = build_structural_only(ds).columns
        baseline = detect_relationships(ds, cols, con)
        with_budget_none = detect_relationships(ds, cols, con, catalog=None, budget=None)
    finally:
        con.close()
    assert [r.model_dump() for r in baseline] == [r.model_dump() for r in with_budget_none]


# --------------------------------------------------------------------------- #
# sharded catalog + BM25 (synonyms drive retrieval)
# --------------------------------------------------------------------------- #
def _model_with(tables: list[dict]) -> dict:
    return {"tables": tables, "relationships": [], "cookbook": []}


def test_bm25_ranks_by_synonyms_not_just_names(tmp_path: Path) -> None:
    model = _model_with(
        [
            {
                "name": "v_gads_keyword_performance",
                "purpose": "keyword level performance",
                "role": "fact",
                "synonyms": ["google ads", "adwords", "paid search"],
                "columns": [
                    {"name": "keyword", "meaning": "the keyword", "synonyms": []},
                    {"name": "clicks", "meaning": "clicks", "synonyms": []},
                    {"name": "cost", "meaning": "spend", "synonyms": ["spend", "ad cost"]},
                ],
            },
            {
                "name": "v_meta_ads_campaign_insights",
                "purpose": "campaign level performance",
                "role": "fact",
                "synonyms": ["facebook", "instagram", "paid social", "meta"],
                "columns": [
                    {"name": "campaign_id", "meaning": "campaign", "synonyms": []},
                    {"name": "spend", "meaning": "spend", "synonyms": []},
                ],
            },
        ]
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_catalog_shards(model, config_dir, tables_per_shard=20)

    index = json.loads((config_dir / "catalog" / "index.json").read_text())
    top = bm25_search("which google ads keywords get the most clicks", index, top_k=3)
    assert top and top[0]["name"] == "v_gads_keyword_performance"

    top2 = bm25_search("paid social spend", index, top_k=3)
    assert top2 and top2[0]["name"] == "v_meta_ads_campaign_insights"


def test_shards_carry_relationships_and_domains(tmp_path: Path) -> None:
    model = {
        "tables": [
            {"name": "customers", "purpose": "", "role": "dimension", "columns": []},
            {"name": "orders", "purpose": "", "role": "fact", "columns": []},
            {"name": "alembic_version", "purpose": "", "role": "staging", "columns": []},
        ],
        "relationships": [
            {"from_ref": "orders.customer_id", "to_ref": "customers.id",
             "strength": "strong", "cardinality": "N:1"}
        ],
        "cookbook": [],
    }
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_catalog_shards(model, config_dir, tables_per_shard=20)

    cat_dir = config_dir / "catalog"
    rels = json.loads((cat_dir / "relationships.json").read_text())
    assert rels and rels[0]["to_ref"] == "customers.id"

    domains = json.loads((cat_dir / "index.json").read_text())["domains"]
    members = {t for d in domains for t in d["tables"]}
    assert "orders" in members and "customers" in members
    assert "alembic_version" not in members  # staging table dropped


# --------------------------------------------------------------------------- #
# per-table synthesis failures are surfaced, not silent
# --------------------------------------------------------------------------- #
class _FlakyLLM(FakeLLMProvider):
    """Raises on the per-table synthesis prompt for one named table."""

    def __init__(self, fail_table: str) -> None:
        self._fail = fail_table

    def generate_json(self, prompt: str, *, system: str | None = None):  # type: ignore[override]
        if prompt.startswith("You are documenting one table") and f"TABLE: {self._fail}\n" in prompt:
            raise LLMError(f"simulated failure for {self._fail}")
        return super().generate_json(prompt, system=system)


def test_synthesis_failure_is_recorded(fk_sqlite: Path, tmp_path: Path) -> None:
    ds = ingest(fk_sqlite)
    structural = build_structural_only(ds)
    model = build_schema_model(
        structural, ds, _FlakyLLM("orders"), use_cache=False, cache_dir=tmp_path / "c"
    )
    assert "orders" in model.synthesis_failures
    # the run still completes and still ships a (backfilled) doc for that table
    assert model.table("orders") is not None
    assert model.table("customers") is not None
