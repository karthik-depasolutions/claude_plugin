"""Central marketplace publishing strategy.

Operates on a local checkout of a marketplace repository (a directory with
`.claude-plugin/marketplace.json` and a `plugins/` folder): copies the newly
packaged plugin into `plugins/<name>/` and rewrites the catalog to include
it, merging with whatever plugins are already listed. Pushing the resulting
commit to GitHub is a separate step - see `forge_core.publishing.github`,
which can push this same directory just as it pushes a single plugin.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from forge_core.models.plugin_spec import MarketplaceManifest, MarketplaceOwner, PluginManifest
from forge_core.packaging.marketplace_builder import build_marketplace_manifest


def _read_existing_manifest(marketplace_dir: Path) -> MarketplaceManifest | None:
    path = marketplace_dir / ".claude-plugin" / "marketplace.json"
    if not path.is_file():
        return None
    return MarketplaceManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def publish_to_marketplace(
    plugin_dir: Path,
    marketplace_dir: Path,
    *,
    marketplace_name: str,
    owner: MarketplaceOwner,
    marketplace_description: str | None = None,
) -> Path:
    """Copy `plugin_dir` into `marketplace_dir/plugins/<name>/` and rewrite
    `.claude-plugin/marketplace.json` to include it alongside every plugin
    already published there. Returns the marketplace directory."""
    from forge_core.validation.plugin_spec import load_manifest  # reuse manifest parsing

    new_manifest, issues = load_manifest(plugin_dir)
    if new_manifest is None:
        raise ValueError(f"cannot publish an invalid plugin: {[i.message for i in issues]}")

    plugins_dir = marketplace_dir / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    target = plugins_dir / new_manifest.name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(plugin_dir, target)

    existing = _read_existing_manifest(marketplace_dir)
    manifests_by_name: dict[str, PluginManifest] = {}
    if existing is not None:
        for entry in existing.plugins:
            entry_dir = plugins_dir / entry.name
            manifest, _ = load_manifest(entry_dir) if entry_dir.is_dir() else (None, [])
            if manifest is not None:
                manifests_by_name[manifest.name] = manifest
    manifests_by_name[new_manifest.name] = new_manifest

    catalog = build_marketplace_manifest(
        marketplace_name,
        owner,
        list(manifests_by_name.values()),
        description=marketplace_description,
    )

    manifest_dir = marketplace_dir / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "marketplace.json"
    manifest_path.write_text(
        json.dumps(catalog.to_json_dict(), indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return marketplace_dir
