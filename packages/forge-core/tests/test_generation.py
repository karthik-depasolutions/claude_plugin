from __future__ import annotations

import os
from pathlib import Path

import pytest
from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.generation import generate_plugin_content
from forge_core.ingestion.registry import ingest
from forge_core.llm import get_provider
from forge_core.models.schema_profile import SchemaProfile
from forge_core.profiling import build_structural_only

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"


@pytest.fixture(autouse=True)
def _cassette_mode(monkeypatch):
    monkeypatch.setenv("FORGE_LLM_CASSETTE_MODE", os.environ.get("FORGE_LLM_CASSETTE_MODE", "replay"))
    monkeypatch.setenv("FORGE_LLM_CASSETTE_DIR", "fixtures/cassettes")


def _profile_for(source_path: Path) -> SchemaProfile:
    ds = ingest(source_path)
    structural = build_structural_only(ds)
    return SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)


def test_generate_plugin_content_grounds_every_kpi_id(bookings_csv: Path):
    profile = _profile_for(bookings_csv)
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")
    bindings = resolve_bindings(profile, pack)
    kpi_defs = compile_all(pack, bindings)

    content = generate_plugin_content(pack, kpi_defs, profile.source, provider=None)

    # Skill frontmatter must satisfy the portable field constraints by construction.
    assert content.skill_frontmatter.name == content.skill_name
    assert len(content.skill_frontmatter.description) <= 1024
    for kpi in kpi_defs.kpis:
        assert kpi.id in content.skill_body

    # Agent tools must be real, generic MCP tool references - never customer specifics.
    assert all(t.startswith("mcp__mis-mcp-runtime__") for t in content.agent_frontmatter.tools)
    assert content.agent_frontmatter.model == "inherit"

    # At least one command exists and its steps cite real KPI ids.
    assert content.commands
    command = content.commands[0]
    for kpi in kpi_defs.kpis:
        assert kpi.id in command.body

    # Hooks are deterministic guardrail text, not executable commands.
    session_hooks = content.hooks.hooks["SessionStart"]
    assert session_hooks[0].hooks[0].type == "prompt"

    # PII never leaks into the generated dashboard snapshot.
    for denied in bindings.denied_columns:
        assert denied not in content.dashboard_html
    assert "KPI Snapshot" in content.dashboard_html


def test_generate_plugin_content_is_generic_across_packs(retail_orders_dir: Path):
    profile = _profile_for(retail_orders_dir)
    pack = load_pack(PACKS_ROOT / "retail-ecommerce")
    bindings = resolve_bindings(profile, pack)
    kpi_defs = compile_all(pack, bindings)

    content = generate_plugin_content(pack, kpi_defs, profile.source, provider=None)

    assert content.skill_name == "retail-ecommerce-analyst"
    assert content.agent_name == "retail-ecommerce-analyst"
    assert kpi_defs.kpis, "expected at least one compiled KPI for retail_orders"


def test_generate_plugin_content_with_llm_provider_stays_grounded(bookings_csv: Path):
    """The LLM only ever contributes framing prose - KPI ids/labels still come
    from the compiled pipeline output even when a real provider is used."""
    profile = _profile_for(bookings_csv)
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")
    bindings = resolve_bindings(profile, pack)
    kpi_defs = compile_all(pack, bindings)
    provider = get_provider(role="generation")

    content = generate_plugin_content(pack, kpi_defs, profile.source, provider=provider)

    for kpi in kpi_defs.kpis:
        assert kpi.id in content.skill_body
        assert kpi.id in content.commands[0].body
    assert content.agent_frontmatter.tools  # unaffected by the LLM call
    # Guardrail notes are allowed to *name* denied columns as things to avoid;
    # what must never happen is one appearing as data in the dashboard snapshot.
    for denied in bindings.denied_columns:
        assert denied not in content.dashboard_html
