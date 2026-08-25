from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.generation import generate_plugin_content
from forge_core.ingestion.registry import ingest
from forge_core.llm import get_provider
from forge_core.models.plugin_spec import HookHandler
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


def test_hook_handler_rejects_type_mismatched_fields():
    """Mirrors a real `/plugin marketplace add` rejection: `args` set on a
    non-`command` hook. Must fail at construction, not just at packaging."""
    with pytest.raises(ValueError, match="only valid on type 'command' hooks"):
        HookHandler(type="prompt", prompt="hi", args=["oops"])

    # The matching type is always fine.
    HookHandler(type="command", command="node", args=["script.js"])
    HookHandler(type="prompt", prompt="hi")


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

    # Hooks are a SessionStart `command` handler: a `prompt` handler would
    # never fire on SessionStart (prompt types are only supported on Stop/
    # SubagentStop/UserPromptSubmit/PreToolUse), so the guardrail block is
    # rendered at session start by hooks/session_context.py instead.
    session_hooks = content.hooks.hooks["SessionStart"]
    handler = session_hooks[0].hooks[0]
    assert handler.type == "command"
    assert handler.command == 'python "${CLAUDE_PLUGIN_ROOT}/hooks/session_context.py"'

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


def test_session_context_script_reads_live_config(tmp_path: Path):
    """The SessionStart command hook runs hooks/session_context.py, which must
    render the guardrail block from config/schema_summary.json at session start
    - not from anything baked into the script at generation time."""
    from forge_core.generation.hooks import session_context_script
    from forge_core.validation.hooks_smoke import check_hooks_smoke

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "schema_summary.json").write_text(
        json.dumps(
            {
                "pack_slug": "healthcare-diagnostics",
                "guardrails": {
                    "pack_name": "Healthcare Diagnostics",
                    "notes": ["Never expose patient identifiers.", "Prefer get_kpi first."],
                },
                "data_context": {
                    "notes": [{"question": "What's the source?", "answer": "Bookings export."}],
                    "findings": [
                        {"severity": "warn", "table": "bookings", "column": "amount",
                         "summary": "has zero rows"},
                    ],
                },
            }
        )
    )

    (tmp_path / "hooks").mkdir()
    script_path = tmp_path / "hooks" / "session_context.py"
    script_path.write_text(session_context_script(), encoding="utf-8")

    result = check_hooks_smoke(tmp_path)
    assert result.status.name == "PASS"
    assert result.issues == []

    # The guardrail text must come from the live config, not the script body.
    assert "Healthcare Diagnostics" not in session_context_script()
    assert "Never expose patient identifiers" not in session_context_script()

    # The script resolves the config relative to its own location (plugin root).
    output = subprocess.run(
        [sys.executable, str(script_path)], capture_output=True, text=True, timeout=30
    )
    assert output.returncode == 0
    assert "Healthcare Diagnostics" in output.stdout
    assert "Never expose patient identifiers" in output.stdout
    assert "Bookings export." in output.stdout
    assert "bookings.amount" in output.stdout
    assert "## What the business owner told us" in output.stdout
