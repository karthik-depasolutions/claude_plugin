"""Stage 4a.5 (optional) — turns a low-confidence binding into a human
question, but only when a shipped KPI actually depends on it. This is what
fixes P0.2 for real: resolver.py now lets a binding ship unresolved-enough to
need confirmation rather than silently trusting a 0.45 score, but shipping
*every* such binding as a question would be a gate people click through
without reading. Only ask about the ones a customer would actually notice
being wrong - i.e. the ones a compiled KPI's SQL references.
"""

from __future__ import annotations

from forge_core.models.bindings import BindingQuestion, SchemaBindings
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.kpi import KpiDefsFile


def _roles_in_use(kpi_defs: KpiDefsFile, bindings: SchemaBindings) -> dict[str, list[str]]:
    """role -> [kpi ids whose compiled SQL actually references that role's
    physical column] - the same "does anything depend on this" test a human
    reviewing the plugin would apply by hand."""
    role_by_physical = {b.physical: b.role for b in bindings.columns}
    roles_to_kpis: dict[str, list[str]] = {}
    for kpi in kpi_defs.kpis:
        for physical, role in role_by_physical.items():
            if f'"{physical}"' in kpi.sql:
                roles_to_kpis.setdefault(role, []).append(kpi.id)
    return roles_to_kpis


def gate_bindings(
    bindings: SchemaBindings, pack: IndustryPack, kpi_defs: KpiDefsFile
) -> list[BindingQuestion]:
    """Every binding flagged `needs_confirmation` by the resolver, filtered
    down to the ones at least one compiled KPI depends on. `source ==
    "human_override"` is never gated - a confirmed binding is never re-asked,
    matching resolver.py never setting needs_confirmation on one."""
    roles_to_kpis = _roles_in_use(kpi_defs, bindings)
    questions: list[BindingQuestion] = []
    for binding in bindings.columns:
        if not binding.needs_confirmation or binding.source == "human_override":
            continue
        kpis_affected = roles_to_kpis.get(binding.role, [])
        if not kpis_affected:
            continue
        meaning = pack.canonical_roles.get(binding.role, binding.role)
        questions.append(
            BindingQuestion(
                id=f"binding:{binding.role}",
                role=binding.role,
                physical=binding.physical,
                confidence=binding.confidence,
                evidence=binding.evidence,
                alternatives=binding.alternatives,
                kpis_affected=kpis_affected,
                question=(
                    f"We matched \"{meaning}\" to the column \"{binding.physical}\" "
                    f"(confidence {binding.confidence:.0%}). This affects: "
                    f"{', '.join(kpis_affected)}. Is that correct?"
                ),
            )
        )
    return questions


__all__ = ["gate_bindings"]
