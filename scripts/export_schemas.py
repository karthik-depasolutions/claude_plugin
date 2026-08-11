#!/usr/bin/env python3
"""Export JSON Schemas for every public forge_core model to fixtures/schemas/.

Run after changing anything in forge_core.models so the schemas used by the
plugin_spec validation check (and any external tooling / editor autocomplete)
stay in sync with the pydantic source of truth.
"""

from __future__ import annotations

import json
from pathlib import Path

from forge_core import models

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "fixtures" / "schemas" / "generated"

EXPORTED_MODELS = [
    "DataSource",
    "SchemaProfile",
    "IndustryPack",
    "SchemaBindings",
    "KpiDefsFile",
    "PluginManifest",
    "MarketplaceManifest",
    "McpConfig",
    "HooksFile",
    "RunRecord",
    "ValidationReport",
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in EXPORTED_MODELS:
        model_cls = getattr(models, name)
        schema = model_cls.model_json_schema()
        out_path = OUT_DIR / f"{name}.schema.json"
        out_path.write_text(json.dumps(schema, indent=2), encoding="utf-8", newline="\n")
        print(f"wrote {out_path.relative_to(ROOT)}")

    # industry-packs/_schema/pack.schema.json is authored against the same
    # IndustryPack model that forge_core.classification.loader validates
    # against at load time — one source of truth, no drift.
    pack_schema_path = ROOT / "industry-packs" / "_schema" / "pack.schema.json"
    pack_schema_path.parent.mkdir(parents=True, exist_ok=True)
    pack_schema_path.write_text(
        json.dumps(models.IndustryPack.model_json_schema(), indent=2), encoding="utf-8", newline="\n"
    )
    print(f"wrote {pack_schema_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
