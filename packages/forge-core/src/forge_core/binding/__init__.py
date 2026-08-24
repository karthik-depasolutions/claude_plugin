"""Stage 4a — BIND. Public entry points: resolve_bindings, gate_bindings."""

from __future__ import annotations

from forge_core.binding.gate import gate_bindings
from forge_core.binding.resolver import pick_fact_table, resolve_bindings

__all__ = ["gate_bindings", "pick_fact_table", "resolve_bindings"]
