"""Local publishing strategy - copy or zip a packaged plugin to a target
location on disk. No network calls; this is the default for development and
for customers who install plugins from a local path."""

from __future__ import annotations

import shutil
from pathlib import Path

from forge_core.packaging.zip import zip_plugin


def publish_local(plugin_dir: Path, destination: Path, *, as_zip: bool = False) -> Path:
    if as_zip:
        output_zip = destination if destination.suffix == ".zip" else destination / f"{plugin_dir.name}.zip"
        return zip_plugin(plugin_dir, output_zip)

    destination.mkdir(parents=True, exist_ok=True)
    target = destination / plugin_dir.name
    shutil.copytree(plugin_dir, target, dirs_exist_ok=True)
    return target
