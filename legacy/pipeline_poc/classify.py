"""Stage 3 — CLASSIFY (load industry pack)."""

from __future__ import annotations

import json
from pathlib import Path


def classify_industry(pack_path: Path) -> dict:
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    print(f"  Industry pack loaded: {pack.get('name', pack_path.name)} ({pack.get('industry')})")
    return pack
