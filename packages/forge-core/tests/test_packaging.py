from __future__ import annotations

from pathlib import Path

from forge_core.binding import resolve_bindings
from forge_core.classification import load_pack
from forge_core.compiler import compile_all
from forge_core.generation import generate_plugin_content
from forge_core.ingestion.registry import ingest
from forge_core.models.common import CheckStatus
from forge_core.models.plugin_spec import PluginManifest
from forge_core.models.schema_profile import SchemaProfile
from forge_core.packaging import build_plugin_spec, plugin_name_for, write_plugin, zip_plugin
from forge_core.packaging.versioning import bump
from forge_core.profiling import build_structural_only
from forge_core.validation import run_harness
from forge_core.validation.plugin_spec import check_plugin_spec

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"


def _profile_for(source_path: Path) -> SchemaProfile:
    ds = ingest(source_path)
    structural = build_structural_only(ds)
    return SchemaProfile(data_source_id=ds.id, structural=structural, semantic=None, source=ds)


def _pipeline(source_path: Path, pack_slug: str):
    profile = _profile_for(source_path)
    pack = load_pack(PACKS_ROOT / pack_slug)
    bindings = resolve_bindings(profile, pack)
    kpi_defs = compile_all(pack, bindings)
    generated = generate_plugin_content(pack, kpi_defs, profile.source, provider=None)
    return profile, pack, bindings, kpi_defs, generated


def test_build_plugin_spec_is_pure_and_conventional(bookings_csv: Path):
    profile, pack, bindings, kpi_defs, generated = _pipeline(bookings_csv, "healthcare-diagnostics")
    spec = build_plugin_spec(pack, profile, bindings, kpi_defs, generated)

    assert spec.manifest.name == plugin_name_for(pack)
    assert isinstance(spec.manifest, PluginManifest)
    # Every declared component uses the conventional auto-discovery layout -
    # no explicit paths needed, and none set.
    assert spec.manifest.skills is None
    assert spec.manifest.agents is None
    assert spec.manifest.commands is None
    relative_paths = {f.relative_path for f in spec.files}
    assert f"skills/{generated.skill_name}/SKILL.md" in relative_paths
    assert f"agents/{generated.agent_name}.md" in relative_paths
    assert "config/kpi_defs.json" in relative_paths


def test_write_plugin_produces_a_bom_less_spec_valid_plugin(bookings_csv: Path, tmp_path: Path):
    profile, pack, bindings, kpi_defs, generated = _pipeline(bookings_csv, "healthcare-diagnostics")
    spec = build_plugin_spec(pack, profile, bindings, kpi_defs, generated)

    plugin_dir = tmp_path / spec.manifest.name
    write_plugin(spec, plugin_dir, source=profile.source, profile=profile, pack=pack, bundle_mcp_runtime=True)

    assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file()
    assert (plugin_dir / ".mcp.json").is_file()
    assert (plugin_dir / "hooks" / "hooks.json").is_file()
    assert (plugin_dir / "mcp_server" / "run_server.py").is_file()
    assert (plugin_dir / "mcp_server" / "mis_mcp_runtime" / "server.py").is_file()
    assert (plugin_dir / "data" / "bookings.csv").is_file()

    # `phone` is denied (healthcare-diagnostics denies the `phone` role
    # category) - it must be dropped from the shipped file, not just from
    # compiled SQL/generated prose.
    header = (plugin_dir / "data" / "bookings.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "phone" not in header.split(",")
    assert "booking_id" in header.split(",")

    for path in plugin_dir.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".md"):
            assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), f"{path} has a BOM"

    result = check_plugin_spec(plugin_dir)
    assert result.status == CheckStatus.PASS, result.issues


def test_end_to_end_run_harness_against_a_real_packaged_plugin(bookings_csv: Path, tmp_path: Path):
    """The full loop: generate -> package -> validate every check that a
    packaged plugin directory unlocks (5, 6, 7), not just the pre-package
    checks M8 could already exercise on its own."""
    profile, pack, bindings, kpi_defs, generated = _pipeline(bookings_csv, "healthcare-diagnostics")
    spec = build_plugin_spec(pack, profile, bindings, kpi_defs, generated)
    plugin_dir = tmp_path / spec.manifest.name
    write_plugin(spec, plugin_dir, source=profile.source, profile=profile, pack=pack)

    config_dir = plugin_dir / "config"
    data_dir = plugin_dir / "data"

    report = run_harness(
        pack=pack,
        profile=profile,
        bindings=bindings,
        kpi_defs=kpi_defs,
        generated=generated,
        plugin_dir=plugin_dir,
        config_dir=config_dir,
        data_dir=data_dir,
    )

    for name in ("fact_check", "sql_safety", "dry_run", "pii_scan", "plugin_spec", "mcp_smoke"):
        result = report.check(name)
        assert result.status in (CheckStatus.PASS, CheckStatus.WARN), f"{name}: {result.issues}"
    assert report.overall != CheckStatus.FAIL


def test_zip_plugin_round_trips(bookings_csv: Path, tmp_path: Path):
    profile, pack, bindings, kpi_defs, generated = _pipeline(bookings_csv, "healthcare-diagnostics")
    spec = build_plugin_spec(pack, profile, bindings, kpi_defs, generated)
    plugin_dir = tmp_path / "plugin"
    write_plugin(spec, plugin_dir, bundle_mcp_runtime=False)

    zip_path = zip_plugin(plugin_dir, tmp_path / "out" / "plugin.zip")
    assert zip_path.is_file()

    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert ".claude-plugin/plugin.json" in [n.replace("\\", "/") for n in names]


def test_versioning_bump():
    assert bump("0.1.0", "patch") == "0.1.1"
    assert bump("0.1.0", "minor") == "0.2.0"
    assert bump("0.1.0", "major") == "1.0.0"
