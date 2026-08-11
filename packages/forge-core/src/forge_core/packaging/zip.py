"""Zips a packaged plugin directory into a distributable archive - the
`archive`/local-install output format alongside the GitHub/marketplace
publishing strategies (see forge_core.publishing)."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path


def zip_plugin(plugin_dir: Path, output_zip: Path) -> Path:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(plugin_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(plugin_dir))
    return output_zip


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
