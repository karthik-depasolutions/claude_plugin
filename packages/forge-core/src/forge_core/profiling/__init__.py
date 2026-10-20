"""Stage 2 — PROFILE. Public entry point: `build_schema_profile`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_core.llm.provider import LLMProvider
from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import SchemaProfile, StructuralProfile
from forge_core.profiling.grain import infer_grains
from forge_core.profiling.patterns import mine_patterns
from forge_core.profiling.relationships import detect_relationships
from forge_core.profiling.semantic import run_semantic_profile
from forge_core.profiling.structural import build_structural_profile
from forge_core.profiling.valuesets import capture_value_sets
from forge_core.runtime_session import open_session

if TYPE_CHECKING:
    from forge_core.ingestion.catalog import CatalogFacts
    from forge_core.profiling.budget import ProfilingBudget


def build_structural_only(
    data_source: DataSource,
    *,
    catalog: CatalogFacts | None = None,
    budget: ProfilingBudget | None = None,
) -> StructuralProfile:
    """Deterministic profiling only — never requires an LLM. Everything the
    synthesis pass reasons over (relationships, grains, value sets, patterns)
    is computed here from real queries, not a model call.

    Parameters
    ----------
    catalog:
        When supplied (agent_schema mode), declared FK/PK facts are used to
        short-circuit relationship probing and assign FOREIGN_KEY roles.
    budget:
        Caps on O(n²) deterministic work (relationship probes, value-set
        columns, etc.).  When None the legacy unlimited behaviour is used.
    """

    from forge_core.profiling.budget import ProfilingBudget  # noqa: PLC0415 - avoid import cycle

    b = budget or ProfilingBudget()
    con = open_session(data_source)
    try:
        base = build_structural_profile(
            data_source, con, catalog=catalog,
            max_distinct_sample_columns=(
                None if budget is None else b.max_distinct_sample_columns
            ),
        )
        relationships = detect_relationships(data_source, base.columns, con, catalog=catalog, budget=budget)
        grains = infer_grains(
            data_source, base.columns, con,
            skip_composite_above_columns=b.skip_grain_composite_above_columns,
        )
        value_sets = capture_value_sets(
            data_source, base.columns, con, max_columns=b.max_valueset_columns,
        )
        patterns = mine_patterns(
            data_source, base.columns, con, relationships, max_tables=b.max_pattern_tables,
        )
        return StructuralProfile(
            columns=base.columns,
            relationships=relationships,
            grains=grains,
            value_sets=value_sets,
            patterns=patterns,
        )
    finally:
        con.close()


# Above this column count the single-shot semantic pass reliably overflows the
# model's context; it is a best-effort "claims" pass (re-verified downstream and
# safe to skip), so on a large schema we don't even attempt it.
_SEMANTIC_MAX_COLUMNS = 1200


def build_schema_profile(
    data_source: DataSource,
    provider: LLMProvider | None = None,
    *,
    catalog: CatalogFacts | None = None,
    budget: ProfilingBudget | None = None,
) -> SchemaProfile:
    """Full profiling entry point (structural + optional semantic LLM pass).

    Parameters
    ----------
    catalog, budget:
        Forwarded to ``build_structural_only`` — see its docstring.
    """
    structural = build_structural_only(data_source, catalog=catalog, budget=budget)
    semantic = None
    if provider is not None and len(structural.columns) <= _SEMANTIC_MAX_COLUMNS:
        try:
            semantic = run_semantic_profile(data_source, structural, provider)
        except Exception:  # noqa: BLE001 - a Layer-2 claims pass must never fail the run;
            semantic = None  # every downstream consumer already handles semantic=None
    return SchemaProfile(
        data_source_id=data_source.id, structural=structural, semantic=semantic, source=data_source
    )


__all__ = ["build_schema_profile", "build_structural_only"]
