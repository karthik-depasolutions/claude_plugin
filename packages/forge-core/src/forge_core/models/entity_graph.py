"""Entity graph — Layer 2, deterministic (P2-01). Consumes
`StructuralProfile.relationships`, which has been computed since the
beginning and never read by anything downstream until now (see the
architecture review's P1.1: "the join graph is computed and thrown away").

Turns "which tables exist, how do they connect, and is it safe to aggregate
across that connection" into a structure `binding/resolver.py` can bind
directly and `run_safe_query`'s allow-list can expand to. No LLM anywhere in
this module - fact/dimension/bridge classification and cardinality are
mechanical facts about the data, not judgment calls.
"""

from __future__ import annotations

from collections import deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Cardinality = Literal["1:1", "N:1", "1:N", "N:N"]
EntityRole = Literal["fact", "dimension", "bridge", "unknown"]
EdgeOrigin = Literal["declared_fk", "value_overlap", "llm_proposed"]

# A join is only unsafe to aggregate across on the "one becomes many" side -
# traversing child->parent (N:1) never duplicates the child-side grain;
# traversing parent->child (1:N) or across a bridge (N:N) does.
FAN_OUT_RISK_BY_CARDINALITY: dict[Cardinality, bool] = {
    "1:1": False,
    "N:1": False,
    "1:N": True,
    "N:N": True,
}
_FLIP: dict[Cardinality, Cardinality] = {"1:1": "1:1", "N:N": "N:N", "N:1": "1:N", "1:N": "N:1"}


class JoinEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: Cardinality
    overlap_ratio: float = Field(ge=0.0, le=1.0)
    orphan_ratio: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    origin: EdgeOrigin
    verified: bool = Field(
        description="False until a real overlap/cardinality query has run against this exact "
        "edge - an LLM-proposed edge (Phase 2's data-understanding agent) starts False and stays "
        "excluded from join_path until check_relationship confirms it (P2-06's V4 gate)."
    )
    evidence: str
    fan_out_risk: bool = Field(
        description="True when traversing this edge can duplicate a measure at the far end - "
        "the guard against silently double/triple-counted revenue."
    )

    def flipped(self) -> JoinEdge:
        cardinality = _FLIP[self.cardinality]
        return JoinEdge(
            from_table=self.to_table,
            from_column=self.to_column,
            to_table=self.from_table,
            to_column=self.from_column,
            cardinality=cardinality,
            overlap_ratio=self.overlap_ratio,
            orphan_ratio=self.orphan_ratio,
            confidence=self.confidence,
            origin=self.origin,
            verified=self.verified,
            evidence=self.evidence,
            fan_out_risk=FAN_OUT_RISK_BY_CARDINALITY[cardinality],
        )


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    physical_table: str
    role: EntityRole
    key_columns: list[str] = Field(default_factory=list)
    measures: list[str] = Field(default_factory=list, description="Additive numeric columns.")
    dimensions: list[str] = Field(default_factory=list)
    time_columns: list[str] = Field(default_factory=list)
    row_count: int
    grain_confidence: float = Field(ge=0.0, le=1.0)


class EntityGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[Entity] = Field(default_factory=list)
    edges: list[JoinEdge] = Field(default_factory=list)

    def entity(self, name: str) -> Entity | None:
        return next((e for e in self.entities if e.name == name), None)

    def fact_entity(self) -> Entity | None:
        return next((e for e in self.entities if e.role == "fact"), None)

    def _adjacency(self) -> dict[str, list[JoinEdge]]:
        adj: dict[str, list[JoinEdge]] = {}
        for edge in self.edges:
            if not edge.verified:
                continue
            adj.setdefault(edge.from_table, []).append(edge)
            adj.setdefault(edge.to_table, []).append(edge.flipped())
        return adj

    def join_path(self, a: str, b: str) -> list[JoinEdge] | None:
        """Shortest path over *verified* edges only - an edge that has never
        been confirmed by a real overlap/cardinality query can never be part
        of a path a query would actually be allowed to traverse."""
        if a == b:
            return []
        adjacency = self._adjacency()
        visited = {a}
        queue: deque[tuple[str, list[JoinEdge]]] = deque([(a, [])])
        while queue:
            table, path = queue.popleft()
            for edge in adjacency.get(table, []):
                if edge.to_table in visited:
                    continue
                new_path = [*path, edge]
                if edge.to_table == b:
                    return new_path
                visited.add(edge.to_table)
                queue.append((edge.to_table, new_path))
        return None

    def is_safe_to_aggregate(self, measure_entity: str, along: list[JoinEdge]) -> bool:
        """False if any edge in the path fans out - the actual guard against
        double-counted revenue, not just a descriptive flag."""
        return not any(edge.fan_out_risk for edge in along)

    def reachable_tables(self, from_table: str) -> set[str]:
        """Every physical table reachable from `from_table` over verified
        edges, `from_table` included - what `allowed_tables` expands to."""
        reachable = {from_table}
        adjacency = self._adjacency()
        queue = deque([from_table])
        while queue:
            table = queue.popleft()
            for edge in adjacency.get(table, []):
                if edge.to_table not in reachable:
                    reachable.add(edge.to_table)
                    queue.append(edge.to_table)
        return reachable


__all__ = ["Entity", "EntityGraph", "EntityRole", "FAN_OUT_RISK_BY_CARDINALITY", "JoinEdge"]
