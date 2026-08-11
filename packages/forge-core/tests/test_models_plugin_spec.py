"""Locks in the verified Claude plugin spec constraints from docs/plugin-format.md
directly in the pydantic models, so a regression here fails fast instead of
surfacing as a `claude plugin validate` failure on a generated plugin."""

from __future__ import annotations

import pytest
from forge_core.models.plugin_spec import (
    Author,
    MarketplaceManifest,
    MarketplaceOwner,
    MarketplacePluginEntry,
    McpConfig,
    McpRemoteServer,
    McpStdioServer,
    PluginManifest,
    SkillFrontmatter,
)
from pydantic import ValidationError


def test_author_must_be_object_not_string():
    with pytest.raises(ValidationError):
        PluginManifest(name="acme-plugin", author="Some Name")  # type: ignore[arg-type]


def test_repository_must_be_string_not_object():
    with pytest.raises(ValidationError):
        PluginManifest(
            name="acme-plugin",
            repository={"type": "git", "url": "https://example.com"},  # type: ignore[arg-type]
        )


def test_valid_manifest_round_trips():
    m = PluginManifest(
        name="acme-plugin",
        version="0.1.0",
        author=Author(name="Acme", email="dev@acme.com"),
        repository="https://github.com/acme/acme-plugin",
        skills="./",
        commands=["./commands/"],
    )
    d = m.to_json_dict()
    assert d["author"] == {"name": "Acme", "email": "dev@acme.com"}
    assert d["repository"] == "https://github.com/acme/acme-plugin"
    assert d["skills"] == "./"


@pytest.mark.parametrize("bad_path", ["commands/kpi.md", "/tmp/x.md", "../shared/x.md", "."])
def test_component_paths_must_be_relative_dot_slash(bad_path: str):
    with pytest.raises(ValidationError):
        PluginManifest(name="acme-plugin", commands=bad_path)


def test_mcp_remote_server_requires_type():
    # Pydantic discriminated union without `type` should fail construction.
    with pytest.raises(ValidationError):
        McpRemoteServer(url="https://mcp.example.com")  # type: ignore[call-arg]


def test_mcp_config_serializes_stdio_and_remote():
    cfg = McpConfig(
        mcpServers={
            "bookings": McpStdioServer(
                command="python",
                args=["${CLAUDE_PLUGIN_ROOT}/mcp_server/server.py"],
                env={"CONFIG_DIR": "${CLAUDE_PLUGIN_ROOT}/config"},
            ),
            "remote": McpRemoteServer(type="http", url="https://mcp.example.com/mcp"),
        }
    )
    d = cfg.to_json_dict()
    assert d["mcpServers"]["bookings"]["type"] == "stdio"
    assert d["mcpServers"]["remote"]["type"] == "http"


def test_reserved_marketplace_name_rejected():
    with pytest.raises(ValidationError):
        MarketplaceManifest(
            name="healthcare",
            owner=MarketplaceOwner(name="Acme"),
            plugins=[MarketplacePluginEntry(name="acme-plugin", source="./plugins/acme-plugin")],
        )


def test_skill_frontmatter_portable_fields_only():
    fm = SkillFrontmatter(
        name="booking-analyst",
        description="Use when analyzing diagnostic lab booking MIS data.",
    )
    d = fm.to_frontmatter_dict()
    assert set(d).issubset({"name", "description", "license", "compatibility", "metadata", "allowed-tools"})


def test_skill_name_length_and_kebab_case_enforced():
    with pytest.raises(ValidationError):
        SkillFrontmatter(name="Booking_Analyst", description="x")
    with pytest.raises(ValidationError):
        SkillFrontmatter(name="a" * 65, description="x")
