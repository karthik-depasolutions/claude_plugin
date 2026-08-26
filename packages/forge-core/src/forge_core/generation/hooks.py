"""Stage 5e — hooks/hooks.json + hooks/session_context.py.

Deterministic only. Hook configuration decides what runs automatically in
the user's session, so unlike the prose stages it is never LLM-authored -
only the industry pack's hand-curated `guardrails.notes` feed it.

SessionStart context is injected via a `command` handler whose stdout is
surfaced as session context. A `prompt` handler on SessionStart would never
fire: per the current hook schema, prompt-type handlers are only supported
on Stop, SubagentStop, UserPromptSubmit, and PreToolUse. The script reads
config/schema_summary.json at session start, so regenerating `config/` alone
keeps the guardrails current without duplicating the content in this file.
"""

from __future__ import annotations

from forge_core.models.industry_pack import IndustryPack
from forge_core.models.plugin_spec import HookHandler, HookMatcherGroup, HooksFile

DEFAULT_GUARDRAIL_NOTES = [
    "Never display or infer personally identifiable information.",
    "Prefer get_kpi over run_safe_query whenever a KPI already covers the question.",
]

_SESSION_CONTEXT_SCRIPT = '''"""SessionStart hook for the MIS plugin.

Prints this plugin's guardrails, data-quality findings, and DataUnderstanding
summary to stdout, which Claude Code surfaces as session context. Reads
config/schema_summary.json and config/data-understanding.json at session start
so regenerating config/ alone keeps the context current.

Kept small and dependency-free (stdlib only) because its latency is prepended
to every session start.
"""

import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    summary = PLUGIN_ROOT / "config" / "schema_summary.json"
    if not summary.is_file():
        print("Working with MIS data.", file=sys.stderr)
        return 0
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("Could not read config/schema_summary.json.", file=sys.stderr)
        return 0

    guardrails = data.get("guardrails") or {}
    pack_name = guardrails.get("pack_name") or data.get("pack_slug", "MIS")
    notes = guardrails.get("notes") or []

    lines = [f"You are working with {pack_name} MIS data. Guardrails for this session:"]
    lines += [f"- {note}" for note in notes]

    context = data.get("data_context") or {}
    ctx_notes = context.get("notes") or []
    if ctx_notes:
        lines.append("## What the business owner told us")
        for note in ctx_notes:
            lines.append(f"- Q: {note['question']}  A: {note['answer']}")
    if context.get("findings"):
        lines.append("## Known data-quality findings")
        for finding in context["findings"]:
            location = f"{finding['table']}.{finding['column']}"
            lines.append(f"- [{finding['severity']}] {location}: {finding['summary']}")

    # Record grain and owner-confirmed facts, from the Context Discovery
    # Agent. Grain earns its place in every session: an analyst that does not
    # know one row is an interaction rather than a customer will double-count
    # on the first GROUP BY it writes. Hypotheses and open questions are
    # deliberately left out - they are uncertain, and nobody in this session
    # can resolve them.
    business = context.get("business_context") or {}
    if business.get("record_grain") or business.get("confirmed_facts"):
        lines.append("## What this data represents")
        if business.get("record_grain"):
            lines.append(f"- One row is {business['record_grain']}")
        for entity in (business.get("primary_entities") or [])[:4]:
            if not entity.get("is_unique_key"):
                lines.append(
                    f"- {entity['table']}.{entity['identifier_column']} repeats across rows - "
                    f"count distinct values, not rows, to count {entity['name']}"
                )
        for fact in (business.get("confirmed_facts") or [])[:5]:
            lines.append(f"- Confirmed by the owner: {fact['observation']}")

    # U5 - DataUnderstanding summary (grain, temporal, vocabularies, business questions)
    du_path = PLUGIN_ROOT / "config" / "data-understanding.json"
    if du_path.is_file():
        try:
            du = json.loads(du_path.read_text(encoding="utf-8"))
            tables = du.get("tables") or []
            if tables:
                lines.append("## Data overview")
                for t in tables[:3]:
                    grain = (t.get("grain") or {}).get("description") or "unknown grain"
                    temporal = t.get("temporal") or {}
                    span = temporal.get("span")
                    line = f"- {t['name']}: {t.get('row_count', '?')} rows, {grain}"
                    if span:
                        line += f", span {span}"
                    lines.append(line)
            cols = du.get("columns") or []
            vocab_cols = [c for c in cols if c.get("vocabulary")]
            if vocab_cols:
                lines.append("## Key vocabularies")
                for c in vocab_cols[:3]:
                    vals = ", ".join(v["value"] for v in (c.get("vocabulary") or [])[:3])
                    lines.append(f"- {c['table']}.{c['name']}: {vals}")
            bqs = du.get("business_questions") or []
            if bqs:
                lines.append("## What you can ask (validated)")
                for q in bqs[:4]:
                    lines.append(f"- {q['question']}")
            open_qs = du.get("open_questions") or []
            if open_qs:
                lines.append("## Open questions (do not guess)")
                for q in open_qs[:2]:
                    lines.append(f"- {q.get('column')}: {q.get('question')}")
        except (OSError, ValueError):
            pass

    print("\\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def generate_hooks(
    pack: IndustryPack, data_context: dict | None = None, data_understanding: dict | None = None
) -> HooksFile:
    """A SessionStart `command` handler whose stdout (the guardrails block,
    rendered by hooks/session_context.py from live config) is injected as
    session context. No customer- or LLM-controlled command is executed.

    `data_context` (the DataReview.to_context payload) is *not* baked in
    here - the script reads it from config/schema_summary.json at session
    start, so the shipped content never goes stale when config/ is
    regenerated. Kept for API compatibility with older call sites."""
    handler = HookHandler(
        type="command",
        command='python "${CLAUDE_PLUGIN_ROOT}/hooks/session_context.py"',
    )
    return HooksFile(hooks={"SessionStart": [HookMatcherGroup(matcher="*", hooks=[handler])]})


def session_context_script() -> str:
    """The hooks/session_context.py body, written alongside hooks.json so the
    SessionStart command handler actually has something to run."""
    return _SESSION_CONTEXT_SCRIPT


__all__ = ["DEFAULT_GUARDRAIL_NOTES", "generate_hooks", "session_context_script"]