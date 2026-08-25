"""Stage 2 — PROFILE. Public entry point: `build_schema_profile`."""

from __future__ import annotations

from typing import Callable

from forge_core.llm.provider import LLMProvider
from forge_core.models.datasource import DataSource
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.schema_profile import SchemaProfile, StructuralProfile
from forge_core.profiling.data_map import build_data_map
from forge_core.profiling.entity_graph import build_entity_graph
from forge_core.profiling.grain import infer_grains
from forge_core.profiling.relationships import detect_relationships
from forge_core.profiling.semantic import run_semantic_profile
from forge_core.profiling.structural import build_structural_profile, reclassify_dimension_labels
from forge_core.runtime_session import open_session


def build_structural_only(data_source: DataSource) -> StructuralProfile:
    """Deterministic profiling only — never requires an LLM. Used as a
    fallback when no API key is configured, and as the fast path in tests."""

    con = open_session(data_source)
    try:
        base = build_structural_profile(data_source, con)
        relationships = detect_relationships(data_source, base.columns, con)
        grains = infer_grains(data_source, base.columns, con)
        # P2-02: revise FREE_TEXT columns using the grain just computed,
        # before anything downstream (denial, entity-graph measures/
        # dimensions) reads guessed_role.
        columns = reclassify_dimension_labels(data_source, base.columns, grains)
        # P2-01: only worth building a graph when there's more than one
        # table - a single-table source has nothing to join.
        entity_graph = (
            build_entity_graph(data_source, columns, grains, con)
            if len(data_source.tables) > 1
            else None
        )
        structural = StructuralProfile(
            columns=columns, relationships=relationships, grains=grains, entity_graph=entity_graph
        )
        # P2-03: built from the same connection/columns above - percentiles
        # and top-values need a live connection, so this can't be deferred
        # past this function's `con` scope.
        structural.data_map = build_data_map(data_source, structural, con)
        return structural
    finally:
        con.close()


def build_schema_profile(
    data_source: DataSource,
    provider: LLMProvider | None = None,
    *,
    use_agent: bool = False,
    packs: list[IndustryPack] | None = None,
    on_agent_stats: Callable[[dict], None] | None = None,
) -> SchemaProfile:
    structural = build_structural_only(data_source)
    semantic = (
        run_semantic_profile(
            data_source,
            structural,
            provider,
            use_agent=use_agent,
            packs=packs,
            on_agent_stats=on_agent_stats,
        )
        if provider
        else None
    )
    return SchemaProfile(
        data_source_id=data_source.id, structural=structural, semantic=semantic, source=data_source
    )


__all__ = ["build_schema_profile", "build_structural_only"]
