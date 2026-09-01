"""MCP resources that carry the knowledge pack to the client.

`config/schema_model.json` (the generator's LLM synthesis) plus the
deterministic `schema_summary.json` are exposed under `schema://...` URIs so
a connecting client can read the overview, per-table docs, verified
cookbook, relationships, and column profiles on demand - not just call
tools. `build_instructions` folds the overview, grain, caveats and the
top pattern notes into the server's top-level `instructions` string, so
the key findings reach the model on connect without a `resources/read`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

# These land in the model's context on every request, so they are capped. The
# rest is one `resources/read` away in schema://model, schema://patterns and
# schema://statistics.
# ponytail: fixed caps; if a dataset routinely overflows them, make the cap a
# guardrail setting rather than raising the constant.
_MAX_CAVEATS_IN_INSTRUCTIONS = 6
_MAX_PATTERNS_IN_INSTRUCTIONS = 12


def _pattern_lines(patterns: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for p in patterns:
        finding = str(p.get("finding", "")).strip()
        if not finding:
            continue
        line = f"- [{p.get('kind', '?')}] {finding}"
        directive = str(p.get("directive", "")).strip()
        if directive:
            line += f" -> {directive}"
        lines.append(line)
    return lines


def build_instructions(schema_model: dict[str, Any]) -> str:
    overview = (schema_model.get("overview") or "").strip()
    caveats = [str(c).strip() for c in schema_model.get("caveats", []) if str(c).strip()]
    tables = schema_model.get("tables", [])
    grain_lines = [
        f"- {t['name']}: {t.get('grain_prose') or t.get('purpose', '')}".rstrip()
        for t in tables
        if t.get("grain_prose") or t.get("purpose")
    ]
    pattern_lines = _pattern_lines(schema_model.get("patterns", []))

    parts = ["Tools for querying this business's data."]
    if overview:
        parts.append(overview)
    if grain_lines:
        parts.append("GRAIN:\n" + "\n".join(grain_lines))
    if caveats:
        shown = caveats[:_MAX_CAVEATS_IN_INSTRUCTIONS]
        parts.append("CRITICAL CAVEATS:\n" + "\n".join(f"- {c}" for c in shown))
    if pattern_lines:
        shown_p = pattern_lines[:_MAX_PATTERNS_IN_INSTRUCTIONS]
        block = (
            "KEY PATTERNS (profiled from the data - the numbers behind each are in "
            "schema://statistics):\n" + "\n".join(shown_p)
        )
        hidden = len(pattern_lines) - len(shown_p)
        if hidden > 0:
            block += f"\n- (+{hidden} more in schema://patterns)"
        parts.append(block)
    parts.append(
        "BEFORE WRITING SQL: read resource schema://model (per-column meaning + enum decodes), "
        "schema://cookbook (verified example queries), and schema://relationships (join keys). "
        "Check list_kpis() first - a vetted metric may already exist. Consult schema://patterns "
        "and schema://statistics for correlations, trends, and data-quality issues that affect "
        "your result. All access is read-only."
    )
    return "\n\n".join(parts)


def register_resources(mcp: Any, resolve: Callable[[], tuple[Any, Any]]) -> None:
    """Attach the schema:// resources. `resolve` returns (config, con)."""

    def _model() -> dict[str, Any]:
        return resolve()[0].schema_model or {}

    def _summary() -> dict[str, Any]:
        return resolve()[0].schema_summary or {}

    @mcp.resource("schema://overview", name="Data overview", mime_type="text/markdown")
    def overview() -> str:
        m = _model()
        lines = [m.get("overview", "No overview available.")]
        if m.get("caveats"):
            lines.append("\n## Caveats\n" + "\n".join(f"- {c}" for c in m["caveats"]))
        return "\n".join(lines)

    @mcp.resource("schema://model", name="Schema model", mime_type="application/json")
    def model() -> str:
        return json.dumps(_model(), indent=2)

    @mcp.resource("schema://relationships", name="Table relationships", mime_type="application/json")
    def relationships() -> str:
        rels = _model().get("relationships", [])
        return json.dumps(rels or {"note": "No relationships detected between the tables."}, indent=2)

    @mcp.resource("schema://patterns", name="Pattern notes", mime_type="application/json")
    def patterns() -> str:
        return json.dumps(_model().get("patterns", []), indent=2)

    @mcp.resource("schema://statistics", name="Raw statistics", mime_type="application/json")
    def statistics() -> str:
        m = _model()
        return json.dumps(
            {
                "statistics": m.get("statistics", {}),
                "quality_findings": m.get("quality_findings", []),
                "value_sets": m.get("value_sets", {}),
            },
            indent=2,
        )

    @mcp.resource("schema://cookbook", name="Verified query cookbook", mime_type="application/json")
    def cookbook() -> str:
        return json.dumps(_model().get("cookbook", []), indent=2)

    @mcp.resource("schema://profile", name="Column profiles", mime_type="application/json")
    def profile() -> str:
        return json.dumps(_summary().get("tables", []), indent=2)
