"""Turn a tool result into a small, renderer-agnostic chart spec, or a
markdown table for clients that never negotiated MCP Apps.

The type is inferred purely from the shape of the result - there is no
chart-hint metadata on a KPI yet:

* one numeric cell             -> {"type": "number"}
* date/datetime + >=1 numeric  -> {"type": "line", "x": [...], "series": [{name, values}]}
* label + numeric, <=8 rows    -> {"type": "pie",  "labels": [...], "values": [...]}
* label + numeric              -> {"type": "bar",  "labels": [...], "values": [...]}
* anything else                -> None (the client falls back to the table)

The label is always the first column; the measure is the first fully-numeric
column after it (trailing columns such as share_percent or rank are ignored).

`attach_chart` adds a `chart` key to any row-returning tool result so every
data tool, not just `render_chart`, comes back chart-ready.
"""

from __future__ import annotations

import re
from typing import Any

MAX_POINTS = 60  # line
MAX_BARS = 20
MAX_PIE_SLICES = 8

_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2})?")


def _looks_temporal(values: list[Any]) -> bool:
    seen = [v for v in values if v not in (None, "")]
    return bool(seen) and all(isinstance(v, str) and _DATE_RE.match(v) for v in seen)


def _as_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def chart_payload(result: dict[str, Any], *, rows_key: str = "rows") -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = result.get(rows_key) or result.get("rows") or []
    if not rows:
        return None
    columns = list(rows[0].keys())

    # one row -> a single number tile (show the first numeric cell)
    if len(rows) == 1:
        return {"type": "number"} if _as_float(rows[0].get(columns[0])) is not None else None

    if len(columns) < 2:
        return None

    x_col, rest = columns[0], columns[1:]
    x_vals = [r.get(x_col) for r in rows]

    # line / trend: first column temporal, at least one numeric series after it
    if _looks_temporal(x_vals):
        series = []
        for col in rest:
            vals = [_as_float(r.get(col)) for r in rows[:MAX_POINTS]]
            if any(v is not None for v in vals):
                series.append({"name": col, "values": [v if v is not None else 0.0 for v in vals]})
        if series:
            return {"type": "line", "x": [str(v) for v in x_vals[:MAX_POINTS]], "series": series}

    # categorical: first column is the label, the first numeric column after it
    # is the measure (extra columns like share_percent / rank are ignored) ->
    # pie for a small non-negative set, bar otherwise.
    measure = next((c for c in rest if all(_as_float(r.get(c)) is not None for r in rows)), None)
    if measure is not None:
        pvals = [_as_float(r.get(measure)) or 0.0 for r in rows]
        labels = [str(r.get(x_col)) for r in rows]
        if len(rows) <= MAX_PIE_SLICES and all(v >= 0 for v in pvals):
            return {"type": "pie", "labels": labels, "values": pvals}
        return {"type": "bar", "labels": labels[:MAX_BARS], "values": pvals[:MAX_BARS]}

    return None


def attach_chart(result: dict[str, Any], *, rows_key: str = "rows") -> dict[str, Any]:
    """Return `result` with a `chart` key (the spec, or None). Cheap; a client
    that cannot render it just ignores it."""
    if not isinstance(result, dict) or "error" in result:
        return result
    return {**result, "chart": chart_payload(result, rows_key=rows_key)}


def delta_chart(period_a: float | None, period_b: float | None, *, label: str = "") -> dict[str, Any]:
    a = _as_float(period_a) or 0.0
    b = _as_float(period_b) or 0.0
    return {"type": "delta", "from": a, "to": b, "change": b - a, "label": label}


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
        more = len(rows) - max_rows
        lines.append(f"\n_{more} more row(s) not shown - refine the query for a smaller result._")
    return "\n".join(lines)


__all__ = ["attach_chart", "chart_payload", "delta_chart", "markdown_table"]
