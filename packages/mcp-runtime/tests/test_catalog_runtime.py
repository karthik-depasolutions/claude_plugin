"""The runtime half of Agent Schema mode: mis_mcp_runtime.catalog reading a
config/catalog/ the generator wrote, and the search_schema / get_table_schema
/ find_join_path tools it backs. No forge_core import in the module under test."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mis_mcp_runtime.catalog import Catalog, load_catalog


@pytest.fixture
def catalog_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config" / "catalog"
    d.mkdir(parents=True)
    (d / "index.json").write_text(
        json.dumps(
            {
                "catalog_version": 1,
                "table_count": 3,
                "avg_doc_length": 8,
                "bm25_idf": {"google": 1.2, "ads": 1.0, "keyword": 1.4, "order": 1.3, "customer": 1.1},
                "tables": [
                    {"name": "gads_keywords", "role": "fact", "purpose": "keyword performance",
                     "synonyms": ["google ads", "adwords"], "shard": 0,
                     "bm25_tf": {"google": 3, "ads": 3, "keyword": 4}},
                    {"name": "orders", "role": "fact", "purpose": "one row per order",
                     "synonyms": [], "shard": 0, "bm25_tf": {"order": 3, "customer": 1}},
                    {"name": "customers", "role": "dimension", "purpose": "one row per customer",
                     "synonyms": [], "shard": 0, "bm25_tf": {"customer": 3}},
                ],
                "domains": [{"name": "orders", "table_count": 2, "tables": ["customers", "orders"]}],
            }
        ),
        encoding="utf-8",
    )
    (d / "shard_0000.json").write_text(
        json.dumps(
            {
                "tables": [
                    {"name": "gads_keywords", "purpose": "keyword performance",
                     "columns": [{"name": "clicks", "meaning": "ad clicks"}]},
                    {"name": "orders", "purpose": "one row per order", "columns": []},
                    {"name": "customers", "purpose": "one row per customer", "columns": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    (d / "relationships.json").write_text(
        json.dumps(
            [{"from_ref": "orders.customer_id", "to_ref": "customers.id",
              "strength": "strong", "cardinality": "N:1"}]
        ),
        encoding="utf-8",
    )
    return d


def test_load_catalog_none_without_index(tmp_path: Path) -> None:
    assert load_catalog(None) is None
    assert load_catalog(tmp_path / "nope") is None


def test_search_ranks_by_synonyms(catalog_dir: Path) -> None:
    cat = Catalog(catalog_dir)
    hits = cat.search("which google ads keywords perform best", limit=3)
    assert hits[0]["table"] == "gads_keywords"
    assert hits[0]["synonyms"] == ["google ads", "adwords"]


def test_get_table_loads_only_its_shard(catalog_dir: Path) -> None:
    cat = Catalog(catalog_dir)
    doc = cat.get_table("gads_keywords")
    assert doc is not None
    assert doc["columns"][0]["meaning"] == "ad clicks"
    assert cat.get_table("missing") is None


def test_join_path_walks_declared_relationships(catalog_dir: Path) -> None:
    cat = Catalog(catalog_dir)
    path = cat.join_path("orders", "customers")
    assert path == [
        {"from": "orders", "from_column": "customer_id", "to": "customers", "to_column": "id"}
    ]
    assert cat.join_path("orders", "nowhere") == []


def test_domains_and_table_count(catalog_dir: Path) -> None:
    cat = Catalog(catalog_dir)
    assert cat.table_count() == 3
    assert cat.domains()[0]["tables"] == ["customers", "orders"]


def test_load_runtime_config_detects_catalog(bookings_config_dir: Path) -> None:
    """A classic plugin config dir yields catalog_dir=None; adding
    config/catalog/index.json flips it on."""
    from mis_mcp_runtime.config import load_runtime_config

    assert load_runtime_config(bookings_config_dir).catalog_dir is None

    cat = bookings_config_dir / "catalog"
    cat.mkdir()
    (cat / "index.json").write_text('{"tables": []}', encoding="utf-8")
    assert load_runtime_config(bookings_config_dir).catalog_dir == cat
