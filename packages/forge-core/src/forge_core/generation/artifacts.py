"""Stage 5f — a static HTML KPI snapshot.

Executed once, at generation time, against the real (or sampled) customer
data using the same compiled SQL the runtime will later serve live. This is
a point-in-time artifact bundled with the plugin, not a live dashboard - the
MCP tools are the live path. No LLM involvement: every number comes straight
out of DuckDB.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime

from forge_core.models.datasource import DataSource
from forge_core.models.kpi import KpiDefsFile
from forge_core.runtime_session import open_session

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{title}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }}
h1 {{ font-size: 1.4rem; }}
h2 {{ font-size: 1.05rem; margin-top: 1.5rem; }}
table {{ border-collapse: collapse; margin-bottom: 0.5rem; width: 100%; max-width: 720px; }}
th, td {{ border: 1px solid #ddd; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem; }}
th {{ background: #f5f5f5; }}
.timestamp {{ color: #666; font-size: 0.85rem; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="timestamp">Snapshot generated {generated_at} - regenerate via the bundled MCP tools \
for live numbers.</p>
{sections}
</body>
</html>
"""


def _render_table(columns: list[str], rows: list[tuple]) -> str:
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in row) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody></table>"


def _render_bar_svg(rows: list[tuple], *, max_bars: int = 12) -> str:
    """A plain inline SVG bar chart for a 2-column (label, number) result.
    Returns '' when the shape isn't chartable - the table alone then stands."""
    if not rows or len(rows[0]) != 2:
        return ""
    pairs: list[tuple[str, float]] = []
    for label, value in rows[:max_bars]:
        try:
            pairs.append((str(label), float(value)))
        except (TypeError, ValueError):
            return ""
    top = max((v for _, v in pairs), default=1.0) or 1.0
    w, h, pad = 640, 200, 28
    bw = max(6.0, (w - 2 * pad) / max(len(pairs), 1) - 8)
    bars = []
    for i, (label, value) in enumerate(pairs):
        bh = max((value / top) * (h - 2 * pad), 1.0)
        x = pad + i * ((w - 2 * pad) / max(len(pairs), 1))
        y = h - pad - bh
        cx = x + bw / 2
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
            f'fill="#3b82f6" opacity="0.85" rx="2"/>'
            f'<text x="{cx:.1f}" y="{h - pad + 12:.0f}" font-size="9" '
            f'text-anchor="middle" fill="#666">{html.escape(label[:10])}</text>'
        )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" style="max-width:680px">'
        f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" stroke="#ccc"/>'
        + "".join(bars)
        + "</svg>"
    )


def generate_dashboard(pack_name: str, kpi_defs: KpiDefsFile, source: DataSource) -> str:
    # Safe to embed live results: every `kpi.sql` here is a compiled pack KPI
    # whose roles were bound only to non-denied columns (the role-guard in
    # forge_core.models.industry_pack.BASELINE_DENIED_ROLES + the pack's own
    # `denied_role_categories`, enforced in binding/resolver._is_denied). No
    # ad-hoc SQL, no `SELECT *` - so a denied column cannot be projected here.
    sections = []
    con = open_session(source)
    try:
        for kpi in kpi_defs.kpis:
            section = f"<h2>{html.escape(kpi.label)}</h2>"
            try:
                result = con.execute(kpi.sql).fetchdf()
                rows = list(result.itertuples(index=False, name=None))
                section += _render_bar_svg(rows) + _render_table(list(result.columns), rows)
            except Exception as exc:  # defensive - dry_run validation already gates packaging
                section += f"<p>Could not compute this KPI: {html.escape(str(exc))}</p>"
            sections.append(section)
    finally:
        con.close()

    return _TEMPLATE.format(
        title=f"{pack_name} - KPI Snapshot",
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        sections="\n".join(sections),
    )
