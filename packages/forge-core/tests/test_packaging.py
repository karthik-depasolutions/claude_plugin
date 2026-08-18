from __future__ import annotations

import json
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
    assert spec.manifest.skills is None
    assert spec.manifest.agents is None
    assert spec.manifest.commands is None
    assert spec.manifest.mcpServers == "./.mcp.json"
    assert spec.manifest.userConfig == {}


def test_build_plugin_spec_personalizes_name_with_a_customer_label(bookings_csv: Path):
    profile, pack, bindings, kpi_defs, generated = _pipeline(bookings_csv, "healthcare-diagnostics")

    spec = build_plugin_spec(pack, profile, bindings, kpi_defs, generated, customer_label="Sparda Music Academy")

    assert spec.manifest.name == "healthcare-diagnostics-sparda-music-academy-mis-plugin"
    assert spec.manifest.displayName == "Sparda Music Academy — Healthcare MIS / Diagnostics Booking Pack Analyst"
    # An unlabeled build for the same pack must stay exactly as before - no
    # cross-customer collision, and the generic default is unaffected.
    assert spec.manifest.name != plugin_name_for(pack)


def test_plugin_name_for_slugifies_and_bounds_an_arbitrary_label():
    from forge_core.classification import load_pack

    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")
    name = plugin_name_for(pack, "  Weird!! Name_With (Punctuation) & CAPS  " + "x" * 60)

    assert name.startswith("healthcare-diagnostics-weird-name-with-punctuation-caps-x")
    assert " " not in name and "!" not in name and "(" not in name
    assert plugin_name_for(pack, None) == plugin_name_for(pack)  # no label -> unchanged default
    assert plugin_name_for(pack, "") == plugin_name_for(pack)  # blank label -> unchanged default


def test_plugin_name_for_stays_manifest_valid_when_label_starts_with_a_digit(bookings_csv: Path):
    """`pack.slug` must lead the name - PluginManifest.name requires
    ^[a-z][a-z0-9-]*$, and a label like "2024 Sales Data" would otherwise
    put a digit first."""
    from forge_core.classification import load_pack

    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")
    name = plugin_name_for(pack, "2024 Sales Data")

    assert name[0].isalpha()
    PluginManifest(name=name)  # raises if the pattern is violated


def test_live_db_plugin_prompts_for_the_connection_string_in_user_config(bookings_csv: Path):
    profile, pack, bindings, kpi_defs, generated = _pipeline(bookings_csv, "healthcare-diagnostics")
    profile.source.connection.credential_env_vars = ["FORGE_SOURCE_DB_URL"]
    spec = build_plugin_spec(pack, profile, bindings, kpi_defs, generated)

    assert "source_db_url" in spec.manifest.userConfig
    assert spec.manifest.userConfig["source_db_url"].required is True
    assert spec.manifest.userConfig["source_db_url"].sensitive is True
    mcp_env = spec.mcp_config.mcpServers["mis-mcp-runtime"].env
    assert mcp_env["FORGE_SOURCE_DB_URL"] == "${user_config.source_db_url}"
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
    manifest_on_disk = json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest_on_disk["mcpServers"] == "./.mcp.json"
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


def test_bind_http_mcp_rewrites_stdio_to_a_remote_url(bookings_csv: Path, tmp_path: Path):
    profile, pack, bindings, kpi_defs, generated = _pipeline(bookings_csv, "healthcare-diagnostics")
    spec = build_plugin_spec(pack, profile, bindings, kpi_defs, generated)
    plugin_dir = tmp_path / spec.manifest.name
    write_plugin(spec, plugin_dir, bundle_mcp_runtime=False)

    from forge_core.packaging import bind_http_mcp

    bind_http_mcp(plugin_dir, "https://forge.example/mcp/abc/tok")
    mcp = json.loads((plugin_dir / ".mcp.json").read_text(encoding="utf-8"))
    server = mcp["mcpServers"]["mis-mcp-runtime"]
    assert server["type"] == "http"
    assert server["url"] == "https://forge.example/mcp/abc/tok"
    assert "command" not in server
    manifest = json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "source_db_url" not in (manifest.get("userConfig") or {})


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
