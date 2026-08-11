"""Builds a `.claude-plugin/marketplace.json` catalog referencing one or
more packaged plugins. Never sets `version` on a marketplace entry - the
packaged plugin's own `plugin.json` already sets it, and plan §4 is explicit
that setting it in both places is a spec violation.
"""

from __future__ import annotations

from forge_core.models.plugin_spec import (
    MarketplaceManifest,
    MarketplaceOwner,
    MarketplacePluginEntry,
    PluginManifest,
)

DEFAULT_CATEGORY = "mis-analytics"


def build_marketplace_manifest(
    name: str,
    owner: MarketplaceOwner,
    plugin_manifests: list[PluginManifest],
    *,
    description: str | None = None,
    source_prefix: str = "./plugins",
    category: str = DEFAULT_CATEGORY,
) -> MarketplaceManifest:
    entries = [
        MarketplacePluginEntry(
            name=manifest.name,
            source=f"{source_prefix}/{manifest.name}",
            description=manifest.description,
            category=category,
        )
        for manifest in plugin_manifests
    ]
    return MarketplaceManifest(name=name, owner=owner, description=description, plugins=entries)
