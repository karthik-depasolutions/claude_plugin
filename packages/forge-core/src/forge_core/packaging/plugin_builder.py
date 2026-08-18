"""Stage 7 - PACKAGE. Turns already-generated, already-validated pipeline
output into a real, installable Claude Code plugin directory: every
constraint in plan §4 is enforced by construction (via the plugin_spec
models) rather than by string formatting.

Two-step API: `build_plugin_spec()` produces an in-memory `PluginSpec`
(pure, easy to unit test), `write_plugin()` serializes it to disk as
BOM-less UTF-8 and bundles the generic MCP runtime alongside it.
"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path

from forge_core.generation import MCP_SERVER_NAME, GeneratedPlugin
from forge_core.models.bindings import SchemaBindings
from forge_core.models.datasource import DataSource
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.kpi import KpiDefsFile
from forge_core.models.plugin_spec import (
    Author,
    GeneratedFile,
    McpConfig,
    McpRemoteServer,
    McpStdioServer,
    PluginManifest,
    PluginSpec,
    UserConfigOption,
)
from forge_core.models.schema_profile import SchemaProfile
from forge_core.packaging.mcp_bundle import bundle_runtime
from forge_core.packaging.redaction import write_redacted_data_files
from forge_core.packaging.versioning import INITIAL_VERSION
from forge_core.validation.frontmatter import render_frontmatter

DEFAULT_AUTHOR = Author(name="MIS Plugin Forge")


def _slugify_label(text: str) -> str:
    """A local, minimal slugify - deliberately not importing
    `forge_core.publishing.standalone_repo.slugify_repo_name` here, since
    packaging is a stage *before* publishing and shouldn't depend on it."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug[:40]


def plugin_name_for(pack: IndustryPack, customer_label: str | None = None) -> str:
    """Every customer of the same pack otherwise gets the exact same plugin
    name (`generic-analytics-mis-plugin`, unconditionally) - folding in an
    optional human-chosen label makes the on-disk directory, manifest name,
    and default GitHub repo name (PublishPanel pre-fills from this) actually
    distinguish one customer's plugin from another's.

    `pack.slug` - guaranteed by its own field pattern to start with a
    lowercase letter - always comes first, specifically so a label starting
    with a digit (e.g. "2024 Sales Data") can never produce a manifest
    `name` that fails `PluginManifest`'s `^[a-z][a-z0-9-]*$` pattern."""
    slug = _slugify_label(customer_label) if customer_label else ""
    return f"{pack.slug}-{slug}-mis-plugin" if slug else f"{pack.slug}-mis-plugin"


def _column_profiles_by_table(profile: SchemaProfile) -> dict[str, list[dict]]:
    """Per-table `column_profiles`, PII-safe by construction: denied/PII
    columns are simply never in `profile.structural.columns` for a table the
    binder didn't select from, and `get_data_profile` already excludes denied
    columns on its live-compute fallback - this pre-computed path must match."""
    by_table: dict[str, list[dict]] = {}
    for col in profile.structural.columns:
        by_table.setdefault(col.table, []).append(
            {"column": col.name, "null_percent": col.null_percent, "cardinality": col.cardinality}
        )
    return by_table


def _config_files(
    pack: IndustryPack, profile: SchemaProfile, bindings: SchemaBindings, kpi_defs: KpiDefsFile
) -> list[GeneratedFile]:
    source = profile.source
    data_source_json = {
        "kind": source.kind.value,
        "connection": {
            "duckdb_attach_sql": source.connection.duckdb_attach_sql,
            "read_only": source.connection.read_only,
        },
        "tables": [
            {"name": t.name, "physical_ref": t.physical_ref, "columns": [c.name for c in t.columns]}
            for t in source.tables
        ],
    }
    denied = set(bindings.denied_columns)
    column_profiles = _column_profiles_by_table(profile)
    schema_summary_json = {
        "pack_slug": kpi_defs.pack_slug,
        "tables": [
            {
                "name": t.name,
                "columns": [c.name for c in t.columns],
                "column_profiles": [
                    p for p in column_profiles.get(t.name, []) if p["column"] not in denied
                ],
            }
            for t in source.tables
        ],
        "guardrails": {
            "max_query_rows": pack.guardrails.max_query_rows,
            "query_timeout_seconds": pack.guardrails.query_timeout_seconds,
        },
    }

    return [
        GeneratedFile(
            relative_path="config/data_source.json",
            content=json.dumps(data_source_json, indent=2),
            is_json=True,
        ),
        GeneratedFile(
            relative_path="config/schema_bindings.json",
            content=bindings.model_dump_json(indent=2),
            is_json=True,
        ),
        GeneratedFile(
            relative_path="config/kpi_defs.json", content=kpi_defs.model_dump_json(indent=2), is_json=True
        ),
        GeneratedFile(
            relative_path="config/schema_summary.json",
            content=json.dumps(schema_summary_json, indent=2),
            is_json=True,
        ),
    ]


def _mcp_config(*, inject_source_db_url: bool = False) -> McpConfig:
    env = {
        "MIS_MCP_CONFIG_DIR": "${CLAUDE_PLUGIN_ROOT}/config",
        "MIS_MCP_DATA_DIR": "${CLAUDE_PLUGIN_ROOT}/data",
    }
    if inject_source_db_url:
        # Desktop prompts for this via plugin.json userConfig when the user
        # clicks Connect on mis-mcp-runtime — never bake the credential in.
        env["FORGE_SOURCE_DB_URL"] = "${user_config.source_db_url}"
    return McpConfig(
        mcpServers={
            MCP_SERVER_NAME: McpStdioServer(
                command="python",
                args=["${CLAUDE_PLUGIN_ROOT}/mcp_server/run_server.py"],
                env=env,
            )
        }
    )


def hosted_mcp_url(public_base_url: str, run_id: str, token: str) -> str:
    return f"{public_base_url.rstrip('/')}/mcp/{run_id}/{token}"


MCP_TOKEN_FILENAME = ".mcp_token"


def mcp_token_path(output_dir: Path) -> Path:
    return output_dir / MCP_TOKEN_FILENAME


def ensure_mcp_token(output_dir: Path) -> str:
    path = mcp_token_path(output_dir)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    output_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token


def run_id_from_plugin_dir(plugin_dir: Path) -> str | None:
    """`generated/runs/<run_id>/output/<plugin>` → run_id."""
    parts = plugin_dir.resolve().parts
    if "runs" not in parts:
        return None
    index = parts.index("runs")
    if index + 1 >= len(parts):
        return None
    return parts[index + 1]


def bind_http_mcp(plugin_dir: Path, url: str) -> None:
    """Point an already-written plugin at a hosted MCP URL so Claude Desktop
    can connect from GitHub install without local Python / claude_desktop_config."""
    spec = McpConfig(mcpServers={MCP_SERVER_NAME: McpRemoteServer(type="http", url=url)})
    _write_text(plugin_dir / ".mcp.json", json.dumps(spec.to_json_dict(), indent=2))
    manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        user_config = manifest.get("userConfig") or {}
        user_config.pop("source_db_url", None)
        if user_config:
            manifest["userConfig"] = user_config
        else:
            manifest.pop("userConfig", None)
        _write_text(manifest_path, json.dumps(manifest, indent=2))
    readme_path = plugin_dir / "README.md"
    if readme_path.is_file():
        text = readme_path.read_text(encoding="utf-8")
        text = text.replace(
            "1. Install the bundled MCP server's dependencies: "
            "`pip install -r mcp_server/requirements.txt`\n"
            "2. Install this plugin directory in Claude Code (`/plugin install <path>` or via a "
            "marketplace).\n",
            "Install this plugin from its GitHub marketplace. Claude Desktop/Code start the "
            "hosted MCP from `.mcp.json` automatically — no local Python and no "
            "`claude_desktop_config.json`.\n",
        )
        _write_text(readme_path, text)


def _readme(pack: IndustryPack, kpi_defs: KpiDefsFile, plugin_name: str, version: str) -> str:
    kpi_lines = "\n".join(f"- `{k.id}` - {k.label}" for k in kpi_defs.kpis)
    return (
        f"# {plugin_name}\n\n"
        f"Version {version}. Generated by MIS Plugin Forge for the **{pack.name}** industry pack.\n\n"
        "## Setup\n\n"
        "1. Install the bundled MCP server's dependencies: "
        "`pip install -r mcp_server/requirements.txt`\n"
        "2. Install this plugin directory in Claude Code (`/plugin install <path>` or via a "
        "marketplace).\n\n"
        "## Available KPIs\n\n"
        f"{kpi_lines}\n"
    )


def build_plugin_spec(
    pack: IndustryPack,
    profile: SchemaProfile,
    bindings: SchemaBindings,
    kpi_defs: KpiDefsFile,
    generated: GeneratedPlugin,
    *,
    plugin_name: str | None = None,
    customer_label: str | None = None,
    version: str = INITIAL_VERSION,
    author: Author | None = None,
) -> PluginSpec:
    """Build the complete in-memory plugin. Every path here is a
    conventional directory (`skills/`, `agents/`, `commands/`,
    `hooks/hooks.json`, `.mcp.json`) so the manifest relies on Claude Code's
    auto-discovery and never needs to declare explicit component paths.

    `customer_label`, if given, personalizes the plugin name/displayName
    (see `plugin_name_for`); `plugin_name` still wins as an exact override
    when both are supplied."""
    name = plugin_name or plugin_name_for(pack, customer_label)
    display_name = f"{customer_label} — {pack.name} Analyst" if customer_label else f"{pack.name} Analyst"
    needs_source_db = "FORGE_SOURCE_DB_URL" in profile.source.connection.credential_env_vars
    user_config = {}
    if needs_source_db:
        user_config["source_db_url"] = UserConfigOption(
            type="string",
            title="Database connection string",
            description=(
                "Paste the postgresql:// URL shown after generate. "
                "Only the URL — do not include FORGE_SOURCE_DB_URL= or quotes."
            ),
            sensitive=True,
            required=True,
        )

    manifest = PluginManifest(
        name=name,
        displayName=display_name,
        version=version,
        description=pack.description,
        author=author or DEFAULT_AUTHOR,
        keywords=[pack.slug, "mis-plugin-forge"],
        # Desktop (and some Code versions) will not attach the bundled MCP
        # server from `.mcp.json` unless the manifest points at it. Skills /
        # agents / commands still use conventional auto-discovery.
        mcpServers="./.mcp.json",
        userConfig=user_config,
    )

    files: list[GeneratedFile] = [
        GeneratedFile(
            relative_path=f"skills/{generated.skill_name}/SKILL.md",
            content=render_frontmatter(
                generated.skill_frontmatter.to_frontmatter_dict(), generated.skill_body
            ),
        ),
        GeneratedFile(
            relative_path=f"agents/{generated.agent_name}.md",
            content=render_frontmatter(
                generated.agent_frontmatter.to_frontmatter_dict(), generated.agent_body
            ),
        ),
        GeneratedFile(relative_path="artifacts/dashboard.html", content=generated.dashboard_html),
    ]
    for command in generated.commands:
        files.append(
            GeneratedFile(
                relative_path=f"commands/{command.name}.md",
                content=render_frontmatter(command.frontmatter.to_frontmatter_dict(), command.body),
            )
        )
    files.extend(_config_files(pack, profile, bindings, kpi_defs))

    return PluginSpec(
        manifest=manifest,
        mcp_config=_mcp_config(inject_source_db_url=needs_source_db),
        hooks=generated.hooks,
        files=files,
        readme=_readme(pack, kpi_defs, name, version),
    )


def write_plugin(
    spec: PluginSpec,
    output_dir: Path,
    *,
    source: DataSource | None = None,
    profile: SchemaProfile | None = None,
    pack: IndustryPack | None = None,
    bundle_mcp_runtime: bool = True,
) -> Path:
    """Serialize a `PluginSpec` to disk as BOM-less UTF-8, write the source
    data it references (redacted per `pack`'s guardrails - see
    forge_core.packaging.redaction), and bundle the generic MCP runtime next
    to it. Returns `output_dir`."""
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_dir = output_dir / ".claude-plugin"
    manifest_dir.mkdir(exist_ok=True)
    _write_text(manifest_dir / "plugin.json", json.dumps(spec.manifest.to_json_dict(), indent=2))

    if spec.mcp_config is not None:
        _write_text(output_dir / ".mcp.json", json.dumps(spec.mcp_config.to_json_dict(), indent=2))

    if spec.hooks is not None:
        hooks_dir = output_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)
        _write_text(hooks_dir / "hooks.json", json.dumps(spec.hooks.to_json_dict(), indent=2))

    for f in spec.files:
        target = output_dir / f.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_text(target, f.content)

    if spec.readme:
        _write_text(output_dir / "README.md", spec.readme)

    if source is not None:
        if profile is None or pack is None:
            raise ValueError(
                "write_plugin(source=...) requires profile= and pack= too, so denied columns "
                "(PII, or any role category the pack's guardrails deny) can be redacted before "
                "the data ships inside the plugin - see forge_core.packaging.redaction."
            )
        write_redacted_data_files(source, profile, pack, output_dir)

    if bundle_mcp_runtime:
        bundle_runtime(output_dir)

    return output_dir


def _write_text(path: Path, content: str) -> None:
    """UTF-8, no BOM, exactly the content given plus a single trailing
    newline - the exact thing the plugin_spec validation check verifies."""
    text = content if content.endswith("\n") else content + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
