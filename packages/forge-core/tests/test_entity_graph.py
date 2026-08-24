from __future__ import annotations

from pathlib import Path

from forge_core.ingestion.registry import ingest
from forge_core.models.entity_graph import Entity, EntityGraph, JoinEdge
from forge_core.profiling import build_structural_only

DATASETS_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "datasets"


def _edge(from_table, from_col, to_table, to_col, cardinality="N:1", fan_out_risk=False, verified=True):
    return JoinEdge(
        from_table=from_table, from_column=from_col, to_table=to_table, to_column=to_col,
        cardinality=cardinality, overlap_ratio=1.0, orphan_ratio=0.0, confidence=1.0,
        origin="declared_fk", verified=verified, evidence="test", fan_out_risk=fan_out_risk,
    )


# --- EntityGraph model: traversal mechanics ---------------------------------


def test_join_path_finds_a_direct_edge():
    graph = EntityGraph(entities=[], edges=[_edge("enrollments", "course_id", "courses", "course_id")])
    path = graph.join_path("enrollments", "courses")
    assert path is not None and len(path) == 1
    assert path[0].to_table == "courses"


def test_join_path_finds_a_multi_hop_path():
    graph = EntityGraph(
        entities=[],
        edges=[
            _edge("order_items", "order_id", "orders", "order_id"),
            _edge("orders", "customer_id", "customers", "customer_id"),
        ],
    )
    path = graph.join_path("order_items", "customers")
    assert path is not None and [e.to_table for e in path] == ["orders", "customers"]


def test_join_path_traverses_in_reverse_with_flipped_cardinality():
    graph = EntityGraph(entities=[], edges=[_edge("enrollments", "course_id", "courses", "course_id")])
    path = graph.join_path("courses", "enrollments")
    assert path is not None and len(path) == 1
    assert path[0].cardinality == "1:N"
    assert path[0].fan_out_risk is True  # parent -> child duplicates a parent-side measure


def test_unverified_edge_is_excluded_from_join_path():
    unverified = _edge("a", "b_id", "b", "id", verified=False)
    graph = EntityGraph(entities=[], edges=[unverified])
    assert graph.join_path("a", "b") is None


def test_join_path_returns_none_when_unreachable():
    graph = EntityGraph(entities=[], edges=[_edge("a", "x", "b", "x")])
    assert graph.join_path("a", "c") is None


def test_is_safe_to_aggregate_false_when_any_edge_fans_out():
    safe = _edge("enrollments", "course_id", "courses", "course_id", fan_out_risk=False)
    risky = _edge("courses", "course_id", "enrollments", "course_id", cardinality="1:N", fan_out_risk=True)
    graph = EntityGraph(entities=[], edges=[])
    assert graph.is_safe_to_aggregate("enrollments", [safe]) is True
    assert graph.is_safe_to_aggregate("courses", [safe, risky]) is False


def test_reachable_tables_includes_the_starting_table():
    graph = EntityGraph(
        entities=[],
        edges=[_edge("orders", "customer_id", "customers", "customer_id")],
    )
    assert graph.reachable_tables("orders") == {"orders", "customers"}


# --- build_entity_graph against real fixtures -------------------------------


def test_edtech_sqlite_classifies_fact_and_dimensions_from_declared_fks():
    ds = ingest(DATASETS_ROOT / "edtech.sqlite")
    structural = build_structural_only(ds)
    graph = structural.entity_graph
    assert graph is not None

    assert graph.entity("enrollments").role == "fact"
    assert graph.entity("courses").role == "dimension"
    assert graph.entity("students").role == "dimension"

    edge = next(e for e in graph.edges if e.to_table == "courses")
    assert edge.origin == "declared_fk"  # read straight from the sqlite schema, not inferred
    assert edge.verified is True
    assert edge.cardinality == "N:1"
    assert edge.fan_out_risk is False


def test_retail_orders_binds_all_tables_with_correct_roles():
    ds = ingest(DATASETS_ROOT / "retail_orders")
    structural = build_structural_only(ds)
    graph = structural.entity_graph
    assert graph is not None

    assert graph.entity("orders").role == "fact"
    assert graph.fact_entity().name == "orders"
    assert graph.join_path("orders", "order_items") is not None
    assert graph.join_path("order_items", "customers") is not None
    assert graph.reachable_tables("orders") == {"orders", "order_items", "customers"}


def test_orders_to_order_items_fans_out_but_the_reverse_does_not():
    ds = ingest(DATASETS_ROOT / "retail_orders")
    structural = build_structural_only(ds)
    graph = structural.entity_graph

    orders_to_items = graph.join_path("orders", "order_items")
    items_to_orders = graph.join_path("order_items", "orders")
    assert orders_to_items[0].fan_out_risk is True  # one order -> many line items
    assert items_to_orders[0].fan_out_risk is False  # many items -> one order is safe


def test_single_table_source_has_no_entity_graph(bookings_csv: Path):
    ds = ingest(bookings_csv)
    structural = build_structural_only(ds)
    assert structural.entity_graph is None


# --- synthetic N:N bridge --------------------------------------------------


def test_bridge_table_edges_are_reclassified_as_n_to_n(tmp_path: Path):
    """A genuine many-to-many join table - composite key, two outbound FKs,
    no measures of its own beyond the FK columns - must be classified as a
    bridge, and both its edges reclassified N:N/fan_out_risk so no metric
    can silently traverse it as if it were an ordinary N:1 FK."""
    (tmp_path / "students.csv").write_text(
        "student_id,name\ns1,Alice\ns2,Bob\ns3,Cara\n", encoding="utf-8"
    )
    (tmp_path / "courses.csv").write_text(
        "course_id,title\nc1,Algebra\nc2,Physics\n", encoding="utf-8"
    )
    (tmp_path / "enrollments.csv").write_text(
        "student_id,course_id\ns1,c1\ns1,c2\ns2,c1\ns2,c2\ns3,c1\n", encoding="utf-8"
    )

    ds = ingest(tmp_path)
    structural = build_structural_only(ds)
    graph = structural.entity_graph
    assert graph is not None

    assert graph.entity("enrollments").role == "bridge"
    bridge_edges = [e for e in graph.edges if e.from_table == "enrollments"]
    assert len(bridge_edges) == 2
    assert all(e.cardinality == "N:N" for e in bridge_edges)
    assert all(e.fan_out_risk for e in bridge_edges)
