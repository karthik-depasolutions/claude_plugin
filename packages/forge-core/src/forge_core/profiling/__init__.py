"""Stage 2 — PROFILE. Public entry point: `build_schema_profile`."""

from __future__ import annotations

from forge_core.llm.provider import LLMProvider
from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import SchemaProfile, StructuralProfile
from forge_core.profiling.grain import infer_grains
from forge_core.profiling.relationships import detect_relationships
from forge_core.profiling.semantic import run_semantic_profile
from forge_core.profiling.structural import build_structural_profile
from forge_core.runtime_session import open_session


def build_structural_only(data_source: DataSource) -> StructuralProfile:
    """Deterministic profiling only — never requires an LLM. Used as a
    fallback when no API key is configured, and as the fast path in tests."""

    con = open_session(data_source)
    try:
        base = build_structural_profile(data_source, con)
        relationships = detect_relationships(data_source, base.columns, con)
        grains = infer_grains(data_source, base.columns)
        return StructuralProfile(columns=base.columns, relationships=relationships, grains=grains)
    finally:
        con.close()


def build_schema_profile(
    data_source: DataSource,
    provider: LLMProvider | None = None,
) -> SchemaProfile:
    structural = build_structural_only(data_source)
    semantic = run_semantic_profile(data_source, structural, provider) if provider else None
    return SchemaProfile(
        data_source_id=data_source.id, structural=structural, semantic=semantic, source=data_source
    )


__all__ = ["build_schema_profile", "build_structural_only"]
