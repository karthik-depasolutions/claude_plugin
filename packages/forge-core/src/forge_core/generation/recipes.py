"""Stage 5c — recipe step lists that back generated slash commands.

Steps are built directly from `CompiledKpi` ids, never invented, so a
command can never tell the model to call a KPI that doesn't exist. A pack
may eventually declare bespoke `recipe_templates` (see
`IndustryPack.recipe_templates`); none of the shipped packs need one yet, so
every pack currently gets the same generic "run every available KPI, then
summarize" recipe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge_core.models.industry_pack import IndustryPack
from forge_core.models.kpi import KpiDefsFile


@dataclass
class Recipe:
    id: str
    title: str
    steps: list[str] = field(default_factory=list)


def default_recipe(pack: IndustryPack, kpi_defs: KpiDefsFile) -> Recipe:
    steps = [f'Call get_kpi("{kpi.id}") - {kpi.label}.' for kpi in kpi_defs.kpis]
    steps.append("Summarize the results in plain business language, calling out anything unusual.")
    return Recipe(id=f"{pack.slug}-report", title=f"{pack.name} Report", steps=steps)


def build_recipes(pack: IndustryPack, kpi_defs: KpiDefsFile) -> list[Recipe]:
    return [default_recipe(pack, kpi_defs)]
