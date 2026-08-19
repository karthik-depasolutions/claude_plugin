"""Stage 4b — COMPILE_KPIS. Public entry points: compile_all, compile_kpi,
and the optional (use_agent=True) propose_kpis."""

from __future__ import annotations

from forge_core.compiler.kpi_compiler import KpiCompileError, compile_all, compile_kpi
from forge_core.compiler.kpi_proposer import propose_kpis

__all__ = ["KpiCompileError", "compile_all", "compile_kpi", "propose_kpis"]
