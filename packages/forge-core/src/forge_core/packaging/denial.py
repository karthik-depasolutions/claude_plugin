"""THE single source of truth for denied columns.

Every consumer that must not see a denied column — physical redaction
(packaging/redaction.py), advertised schemas and column profiles
(packaging/plugin_builder.py), and the runtime's own denied-column guardrail —
reads the result of `compute_denied_columns`, computed exactly once per run.
Before this existed, redaction and the bindings layer computed denial
*differently*, which is how PII column names shipped in a plugin whose
`pii_scan` passed while the physical values were deleted: the config and
advertised schema were filtered by the bindings-scoped list (empty for
non-fact tables), not by what was actually deleted.
"""

from __future__ import annotations

from forge_core.models.industry_pack import IndustryPack
from forge_core.models.schema_profile import SchemaProfile


def compute_denied_columns(profile: SchemaProfile, pack: IndustryPack) -> dict[str, set[str]]:
    """Every column, across *every* table, that must not appear in the shipped
    plugin — in data, in config, in any advertised schema, or in a live
    query (this feeds `SchemaBindings.denied_columns`, the runtime's own
    query-time guard). Denial here means *irreversible physical deletion* —
    it must therefore require an explicit high-confidence PII signal, never
    a role guess (P2-02).

    `pack.guardrails.denied_role_categories` (e.g. "free_text") still governs
    a *different* question — whether a column is eligible to be bound to a
    canonical role at all (see `binding/resolver.py::_is_denied`) — but a
    column being the wrong *kind* for a KPI role is not the same claim as it
    being PII, and conflating the two is exactly what deleted
    `courses.course_name` (4 distinct values on a 4-row table, correctly
    NOT free text, but caught by a cardinality heuristic that guessed wrong
    - see review P1.2). A wrong role guess should exclude a column from
    projection or binding at most; only PII should destroy data."""
    denied: dict[str, set[str]] = {}
    for col in profile.structural.columns:
        if col.is_likely_pii:
            denied.setdefault(col.table, set()).add(col.name)
    return denied


__all__ = ["compute_denied_columns"]