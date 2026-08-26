"""Stage 2 — PROFILE. Public entry point: `build_schema_profile`."""

from __future__ import annotations

from typing import Callable

from forge_core.llm.provider import LLMProvider
from forge_core.models.datasource import DataSource
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.schema_profile import SchemaProfile, SemanticProfile, StructuralProfile
from forge_core.profiling.data_map import build_data_map
from forge_core.profiling.entity_graph import build_entity_graph
from forge_core.profiling.grain import infer_grains
from forge_core.profiling.relationships import detect_relationships
from forge_core.profiling.semantic import run_semantic_profile
from forge_core.profiling.structural import build_structural_profile, reclassify_dimension_labels
from forge_core.runtime_session import open_session


def build_structural_only(
    data_source: DataSource, on_progress: Callable[[str], None] | None = None
) -> StructuralProfile:
    """Deterministic profiling only — never requires an LLM. Used as a
    fallback when no API key is configured, and as the fast path in tests."""

    con = open_session(data_source)
    try:
        if on_progress:
            on_progress(f"Analyzing structure across {len(data_source.tables)} table(s)")
        base = build_structural_profile(data_source, con)

        if on_progress:
            on_progress(f"Profiling {len(base.columns)} columns and value distributions")
        relationships = detect_relationships(data_source, base.columns, con)

        if on_progress and relationships:
            on_progress(f"Detected {len(relationships)} candidate foreign-key relationship(s)")
        grains = infer_grains(data_source, base.columns, con)

        if on_progress:
            on_progress("Inferring entity grain and dimension labels")
        columns = reclassify_dimension_labels(data_source, base.columns, grains)

        entity_graph = (
            build_entity_graph(data_source, columns, grains, con)
            if len(data_source.tables) > 1
            else None
        )
        if on_progress and entity_graph:
            on_progress(f"Constructed entity join graph ({len(entity_graph.entities)} entities)")

        structural = StructuralProfile(
            columns=columns, relationships=relationships, grains=grains, entity_graph=entity_graph
        )

        if on_progress:
            on_progress("Building statistical data map and percentiles")
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
    on_progress: Callable[[str], None] | None = None,
    cached_semantic: SemanticProfile | None = None,
) -> SchemaProfile:
    """`cached_semantic` short-circuits the semantic pass with a result a
    previous run of this same pipeline already paid for. Structural profiling
    is deterministic and cheap, so it always re-runs; the semantic pass is
    the expensive agent and would otherwise be re-run in full on every
    resume, re-deriving the same answer at the same price."""
    structural = build_structural_only(data_source, on_progress=on_progress)
    if cached_semantic is not None:
        if on_progress:
            on_progress("Reusing semantic analysis from this run's earlier pass")
        return SchemaProfile(
            data_source_id=data_source.id,
            structural=structural,
            semantic=cached_semantic,
            source=data_source,
        )
    if provider:
        if on_progress:
            on_progress(
                "🤖 Reasoning Agent active: Running semantic analysis & column investigation"
                if use_agent
                else "Running LLM semantic profiling"
            )
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
    if on_progress and semantic:
        on_progress(f"Semantic analysis complete — profiled {len(semantic.column_semantics)} column semantic(s)")
    return SchemaProfile(
        data_source_id=data_source.id, structural=structural, semantic=semantic, source=data_source
    )


__all__ = ["build_schema_profile", "build_structural_only"]
