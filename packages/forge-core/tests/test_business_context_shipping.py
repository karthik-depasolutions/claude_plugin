"""The Context Discovery Agent's findings must reach the *shipped plugin*,
not just the build UI.

Record grain is the highest-value thing to tell an analyst: not knowing that
one row is an interaction rather than a customer produces a wrong answer on
the first GROUP BY anyone writes. It rides in `config/schema_summary.json`
and is rendered by the SessionStart hook into every plugin session.

The packager ships an explicit allowlist rather than the whole payload, so
these tests pin both halves: what must arrive, and what must not.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.generation import generate_plugin_content
from forge_core.generation.hooks import session_context_script
from forge_core.ingestion.registry import ingest
from forge_core.models.schema_profile import SchemaProfile
from forge_core.packaging import build_plugin_spec, write_plugin
from forge_core.profiling import build_structural_only

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"

BUSINESS_CONTEXT = {
    "record_grain": "one call attempt to a lead, not one lead",
    "business_objective": "Increase trial-to-enrollment conversion",
    "primary_entities": [
        {
            "name": "Lead",
            "table": "bookings",
            "identifier_column": "customer_id",
            "is_unique_key": False,
        },
        # Denied (PII) - must be dropped by the packaging gate.
        {
            "name": "Contact",
            "table": "bookings",
            "identifier_column": "phone",
            "is_unique_key": False,
        },
    ],
    "confirmed_facts": [{"source": "answer:q1", "observation": "Cancelled rows are still real demand"}],
    # Neither of these may ship: uncertain, and unresolvable by anyone in a
    # plugin session.
    "hypotheses": [{"claim": "score is probably revenue", "category": "kpi", "confidence": 0.4}],
    "unresolved_questions": [{"question": "Which status means paid?", "category": "kpi", "impact": "critical"}],
}


@pytest.fixture
def packaged(bookings_csv: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGE_ENABLE_PII_PROTECTION", "true")
    data_source = ingest(bookings_csv)
    structural = build_structural_only(data_source)
    profile = SchemaProfile(
        data_source_id=data_source.id, structural=structural, semantic=None, source=data_source
    )
    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")
    bindings = resolve_bindings(profile, pack, overrides={"location": "city"})
    kpi_defs = compile_all(pack, bindings)
    generated = generate_plugin_content(pack, kpi_defs, data_source, provider=None)

    data_context = {
        "notes": [],
        "findings": [],
        "business_context": BUSINESS_CONTEXT,
    }
    denied_by_table = {"bookings": {"phone", "customer_name"}}
    spec = build_plugin_spec(
        pack,
        profile,
        bindings,
        kpi_defs,
        generated,
        data_context=data_context,
        denied_by_table=denied_by_table,
    )
    plugin_dir = tmp_path / spec.manifest.name
    write_plugin(spec, plugin_dir, source=data_source, profile=profile, pack=pack,
                 denied_by_table=denied_by_table)
    summary = json.loads(
        (plugin_dir / "config" / "schema_summary.json").read_text(encoding="utf-8")
    )
    return summary["data_context"]["business_context"]


def test_record_grain_ships_with_the_plugin(packaged):
    assert packaged["record_grain"] == "one call attempt to a lead, not one lead"
    assert packaged["business_objective"] == "Increase trial-to-enrollment conversion"
    assert packaged["confirmed_facts"][0]["observation"] == "Cancelled rows are still real demand"


def test_a_denied_column_never_ships_as_an_entity_identifier(packaged):
    """`phone` is denied, so the Contact entity naming it must be dropped -
    the same gate the quality findings already pass through. Shipping the
    name of a column whose values were physically deleted is the exact
    mismatch that made a plugin's pii_scan pass while it still leaked."""
    identifiers = {e["identifier_column"] for e in packaged["primary_entities"]}
    assert "phone" not in identifiers
    assert identifiers == {"customer_id"}


def test_uncertain_material_is_not_shipped(packaged):
    """A hypothesis reads exactly like a fact once it is in a prompt, and
    nobody in a plugin session can resolve an open question."""
    assert "hypotheses" not in packaged
    assert "unresolved_questions" not in packaged


def test_session_hook_tells_the_analyst_not_to_count_rows(tmp_path: Path):
    """The repeating-entity warning is the practical payoff of knowing the
    grain: count distinct customers, not rows.

    Runs the generated hook as a real subprocess against a real
    schema_summary.json, exactly as Claude Code does at session start -
    reimplementing its rendering here would only test the copy."""
    plugin_root = tmp_path / "plugin"
    (plugin_root / "hooks").mkdir(parents=True)
    (plugin_root / "config").mkdir(parents=True)
    (plugin_root / "hooks" / "session_context.py").write_text(
        session_context_script(), encoding="utf-8"
    )
    (plugin_root / "config" / "schema_summary.json").write_text(
        json.dumps(
            {
                "pack_slug": "healthcare-diagnostics",
                "guardrails": {"pack_name": "Diagnostics", "notes": []},
                "data_context": {
                    "notes": [],
                    "findings": [],
                    "business_context": {
                        "record_grain": "one call attempt to a lead, not one lead",
                        "primary_entities": [
                            {
                                "name": "Lead",
                                "table": "bookings",
                                "identifier_column": "customer_id",
                                "is_unique_key": False,
                            }
                        ],
                        "confirmed_facts": [
                            {"source": "a", "observation": "Cancelled rows are real demand"}
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(  # noqa: S603
        [sys.executable, str(plugin_root / "hooks" / "session_context.py")],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "one call attempt to a lead" in result.stdout
    assert "count distinct values, not rows" in result.stdout
    assert "Confirmed by the owner: Cancelled rows are real demand" in result.stdout
