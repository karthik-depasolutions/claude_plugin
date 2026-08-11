"""Stage 5e — hooks/hooks.json.

Deterministic only. Hook configuration decides what runs automatically in
the user's session, so unlike the prose stages it is never LLM-authored -
only the industry pack's hand-curated `guardrails.notes` feed it.
"""

from __future__ import annotations

from forge_core.models.industry_pack import IndustryPack
from forge_core.models.plugin_spec import HookHandler, HookMatcherGroup, HooksFile

_DEFAULT_NOTES = [
    "Never display or infer personally identifiable information.",
    "Prefer get_kpi over run_safe_query whenever a KPI already covers the question.",
]


def generate_hooks(pack: IndustryPack) -> HooksFile:
    """A SessionStart reminder that surfaces the pack's guardrails as context,
    without executing any customer- or LLM-controlled command."""
    notes = pack.guardrails.notes or _DEFAULT_NOTES
    reminder = f"You are working with {pack.name} MIS data. Guardrails for this session:\n" + "\n".join(
        f"- {n}" for n in notes
    )
    handler = HookHandler(type="prompt", prompt=reminder)
    return HooksFile(hooks={"SessionStart": [HookMatcherGroup(matcher="*", hooks=[handler])]})
