"""Read-only listing of the available industry packs, so the web wizard can
show what the classifier is choosing between."""

from __future__ import annotations

from fastapi import APIRouter
from forge_core.classification import load_all_packs
from forge_core.orchestrator import DEFAULT_PACKS_ROOT

from forge_api.schemas import PackSummary

router = APIRouter(prefix="/packs", tags=["packs"])


@router.get("", response_model=list[PackSummary])
async def list_packs() -> list[PackSummary]:
    packs = load_all_packs(DEFAULT_PACKS_ROOT)
    return [
        PackSummary(
            slug=p.slug, name=p.name, version=p.version, description=p.description, kpi_count=len(p.kpis)
        )
        for p in packs
    ]
