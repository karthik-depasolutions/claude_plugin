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


def generate_dashboard(pack_name: str, kpi_defs: KpiDefsFile, source: DataSource) -> str:
    sections = []
    con = open_session(source)
    try:
        for kpi in kpi_defs.kpis:
            section = f"<h2>{html.escape(kpi.label)}</h2>"
            try:
                result = con.execute(kpi.sql).fetchdf()
                rows = list(result.itertuples(index=False, name=None))
                section += _render_table(list(result.columns), rows)
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
