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
    plugin — in data, in config, or in any advertised schema. A column is
    denied when profiling flagged it as likely PII, or its guessed structural
    role is in the pack's denied role categories."""
    denied: dict[str, set[str]] = {}
    for col in profile.structural.columns:
        if col.is_likely_pii or col.guessed_role.value in pack.guardrails.denied_role_categories:
            denied.setdefault(col.table, set()).add(col.name)
    return denied


__all__ = ["compute_denied_columns"]