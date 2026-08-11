"""Loads an IndustryPack from `industry-packs/<slug>/pack.json` + its
`kpis/*.json` sibling files. KPIs are split into one file per KPI so a pack
author (or a future "propose a new KPI" LLM flow) can add one without
touching the rest of the pack.
"""

from __future__ import annotations

import json
from pathlib import Path

from forge_core.models.industry_pack import CanonicalKpi, IndustryPack


def load_pack(pack_dir: Path) -> IndustryPack:
    pack_json_path = pack_dir / "pack.json"
    if not pack_json_path.exists():
        raise FileNotFoundError(f"No pack.json in {pack_dir}")

    data = json.loads(pack_json_path.read_text(encoding="utf-8"))
    data.setdefault("slug", pack_dir.name)

    kpis_dir = pack_dir / "kpis"
    kpis = []
    if kpis_dir.is_dir():
        for kpi_file in sorted(kpis_dir.glob("*.json")):
            kpi_data = json.loads(kpi_file.read_text(encoding="utf-8"))
            kpis.append(CanonicalKpi(**kpi_data))
    data["kpis"] = kpis

    return IndustryPack(**data)


def load_all_packs(packs_root: Path) -> list[IndustryPack]:
    packs = []
    for entry in sorted(packs_root.iterdir()):
        if entry.is_dir() and not entry.name.startswith("_") and (entry / "pack.json").exists():
            packs.append(load_pack(entry))
    return packs
