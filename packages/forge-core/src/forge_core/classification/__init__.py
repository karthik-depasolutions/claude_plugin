"""Stage 3 — CLASSIFY. Public entry points: load_all_packs, load_pack, classify."""

from __future__ import annotations

from forge_core.classification.loader import load_all_packs, load_pack
from forge_core.classification.matcher import classify

__all__ = ["classify", "load_all_packs", "load_pack"]
