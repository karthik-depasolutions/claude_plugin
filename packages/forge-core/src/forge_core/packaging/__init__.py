"""Stage 7 - packaging. Turns generated pipeline output into a real,
installable Claude Code plugin directory (`plugin_builder`), bundles the
generic MCP runtime (`mcp_bundle`), zips it for distribution (`zip`), and
tracks semver (`versioning`). See `forge_core.publishing` for what happens
to the packaged output next.
"""

from __future__ import annotations

from forge_core.packaging.marketplace_builder import build_marketplace_manifest
from forge_core.packaging.plugin_builder import (
    bind_http_mcp,
    build_plugin_spec,
    ensure_mcp_token,
    hosted_mcp_url,
    plugin_name_for,
    run_id_from_plugin_dir,
    write_plugin,
)
from forge_core.packaging.versioning import INITIAL_VERSION, bump
from forge_core.packaging.zip import zip_plugin

__all__ = [
    "INITIAL_VERSION",
    "build_marketplace_manifest",
    "bind_http_mcp",
    "build_plugin_spec",
    "ensure_mcp_token",
    "hosted_mcp_url",
    "run_id_from_plugin_dir",
    "bump",
    "plugin_name_for",
    "write_plugin",
    "zip_plugin",
]
