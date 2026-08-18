"""Shared helpers for `render_chart`: turning a `get_kpi`-shaped result into
either a chart payload for the MCP Apps iframe, or a markdown table for
clients that never negotiated Apps (SEP-2133 graceful degradation).

No chart-hint metadata exists on a KPI yet (that lands with `KpiSpec` in a
later phase) - so, for now, `chart_payload` infers a type purely from the
shape of the result: one numeric cell is a number tile, a two-column result
is a bar chart, anything else is left to the iframe's own table fallback.
"""

from __future__ import annotations

from typing import Any

# ponytail: bar count capped for chart legibility; paginate if a KPI wants more.
MAX_BARS = 20


def chart_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = result.get("rows") or []
    if not rows:
        return None

    columns = list(rows[0].keys())
    if len(rows) == 1 and len(columns) == 1:
        return {"type": "number"}

    if len(columns) == 2:
        label_col, value_col = columns
        try:
            values = [float(row[value_col]) for row in rows[:MAX_BARS]]
        except (TypeError, ValueError):
            return None
        labels = [str(row[label_col]) for row in rows[:MAX_BARS]]
        return {"type": "bar", "labels": labels, "values": values}

    return None


def markdown_table(result: dict[str, Any], *, max_rows: int = 20) -> str:
    rows: list[dict[str, Any]] = result.get("rows") or []
    if not rows:
        return "No rows."

    columns = list(rows[0].keys())
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows[:max_rows]:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |")
    if len(rows) > max_rows:
        lines.append(f"\n_{len(rows) - max_rows} more row(s) not shown - refine the query for a smaller result._")
    return "\n".join(lines)


__all__ = ["chart_payload", "markdown_table"]
