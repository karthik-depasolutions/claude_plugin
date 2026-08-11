"""Stage 6 — PACKAGE (assemble Claude Code plugin)."""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path
from typing import Any

from pipeline.kpi_engine import extract_kpi_names


def _server_py(kpi_names: list[str]) -> str:
    kpi_list = ", ".join(repr(k) for k in kpi_names)
    return textwrap.dedent(
        f'''\
        #!/usr/bin/env python3
        """MCP server — auto-packaged from validated KPI specs."""

        from __future__ import annotations

        import json
        import os
        import re
        import sys
        from pathlib import Path

        import duckdb
        import pandas as pd
        from mcp.server.fastmcp import FastMCP

        from kpi_logic import compute_kpi

        TABLE_NAME = "bookings"
        MAX_ROWS = 200
        SUPPORTED_KPIS = [{kpi_list}]
        FORBIDDEN = re.compile(
            r"\\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|DETACH|COPY|PRAGMA|VACUUM)\\b",
            re.IGNORECASE,
        )

        mcp = FastMCP("curelo-bookings")
        _df: pd.DataFrame | None = None
        _con: duckdb.DuckDBPyConnection | None = None


        def _csv_path() -> Path:
            env = os.environ.get("BOOKINGS_CSV")
            if env:
                return Path(env)
            return Path(__file__).resolve().parent.parent / "data" / "sample_bookings_mis.csv"


        def _load() -> tuple[pd.DataFrame, duckdb.DuckDBPyConnection]:
            global _df, _con
            if _df is None:
                path = _csv_path()
                if not path.exists():
                    raise FileNotFoundError(f"Bookings CSV not found: {{path}}")
                _df = pd.read_csv(path)
                _con = duckdb.connect(":memory:")
                _con.register(TABLE_NAME, _df)
            return _df, _con  # type: ignore[return-value]


        def _validate_select(sql: str) -> None:
            stripped = sql.strip().rstrip(";").strip()
            if not stripped:
                raise ValueError("Query is empty.")
            if FORBIDDEN.search(stripped):
                raise ValueError("Only read-only SELECT queries are allowed.")
            if not re.match(r"^SELECT\\b", stripped, re.IGNORECASE):
                raise ValueError("Only SELECT queries are allowed.")
            if ";" in stripped:
                raise ValueError("Multiple statements are not allowed.")


        @mcp.tool()
        def describe_schema() -> str:
            """Return column names, types, row count, and three sample rows."""
            df, _ = _load()
            sample = df.head(3).where(pd.notnull(df), None).to_dict(orient="records")
            payload = {{
                "table": TABLE_NAME,
                "row_count": len(df),
                "columns": [{{"name": c, "dtype": str(df[c].dtype)}} for c in df.columns],
                "sample_rows": sample,
            }}
            return json.dumps(payload, indent=2, default=str)


        @mcp.tool()
        def run_safe_query(sql: str) -> str:
            """Execute a read-only SELECT against bookings (max 200 rows)."""
            _, con = _load()
            try:
                _validate_select(sql)
            except ValueError as exc:
                return json.dumps({{"error": str(exc)}}, indent=2)
            try:
                result = con.execute(sql).fetchdf()
            except Exception as exc:
                return json.dumps({{"error": str(exc)}}, indent=2)
            truncated = len(result) > MAX_ROWS
            if truncated:
                result = result.head(MAX_ROWS)
            rows = result.where(pd.notnull(result), None).to_dict(orient="records")
            return json.dumps(
                {{"row_count": len(rows), "truncated": truncated, "max_rows": MAX_ROWS, "rows": rows}},
                indent=2,
                default=str,
            )


        @mcp.tool()
        def get_kpi(kpi_name: str) -> str:
            """Return a named KPI computed from the bookings dataset."""
            df, _ = _load()
            try:
                return json.dumps(compute_kpi(df, kpi_name), indent=2, default=str)
            except ValueError:
                return json.dumps(
                    {{"error": f"Unsupported kpi_name: {{kpi_name!r}}", "supported_kpis": SUPPORTED_KPIS}},
                    indent=2,
                )


        if __name__ == "__main__":
            try:
                _load()
            except Exception as exc:
                print(f"Failed to load bookings data: {{exc}}", file=sys.stderr)
                sys.exit(1)
            mcp.run()
        '''
    )


def _kpi_logic_py() -> str:
    src = Path(__file__).resolve().parent / "kpi_engine.py"
    return src.read_text(encoding="utf-8")


PLUGIN_JSON = {
    "name": "curelo-bookings-poc",
    "version": "0.1.0",
    "description": "Auto-generated diagnostic lab booking analytics plugin (Gemini pipeline POC)",
    "author": {"name": "Curelo", "email": "dev@curelo.com"},
    "keywords": ["bookings", "healthcare", "diagnostics", "analytics", "mcp"],
}

MARKETPLACE_JSON = {
    "name": "curelo-bookings-poc-marketplace",
    "description": "Curelo Gemini-generated booking analytics POC plugin",
    "owner": {"name": "Curelo"},
    "plugins": [
        {
            "name": "curelo-bookings-poc",
            "source": ".",
            "description": "Gemini-pipeline-generated booking MIS analytics plugin",
            "version": "0.1.0",
        }
    ],
}

MCP_JSON = {
    "mcpServers": {
        "curelo-bookings": {
            "command": "python",
            "args": ["${CLAUDE_PLUGIN_ROOT}/mcp_server/server.py"],
            "env": {"BOOKINGS_CSV": "${CLAUDE_PLUGIN_ROOT}/data/sample_bookings_mis.csv"},
        }
    }
}

MCP_REQUIREMENTS = "mcp[cli]>=1.2.0\npandas>=2.0.0\nduckdb>=1.0.0\n"


def _readme(generated: dict[str, Any], validation_report: dict, artifact_html: str) -> str:
    auto = [
        "SKILL.md (Gemini skill writer)",
        "commands/*.md (Gemini recipe generator)",
        "tool_specs.json logic → mcp_server/ (validated dry-run implementations)",
        "artifacts/dashboard.html template (Gemini artifact generator)",
    ]
    hand = [
        "healthcare_industry_pack.json",
        "Pipeline orchestrator (generate_plugin.py + pipeline/)",
        "MCP server shell + kpi_logic.py (from proven dry-run code, not raw Gemini Python)",
        "plugin.json, marketplace.json, .mcp.json",
    ]
    kpi_lines = []
    for k in validation_report.get("dry_run", {}).get("kpi_results", []):
        if k.get("status") != "fail":
            kpi_lines.append(f"- `{k['kpi_name']}`: validated in dry-run")

    return f"""# curelo-bookings-poc

**Auto-generated** Claude Code plugin produced by the Gemini profiling + generation pipeline.

## Validation status

Overall: **{validation_report.get('overall', 'unknown')}**

{kpi_lines and chr(10).join(kpi_lines) or '- See validation_report.json in project root'}

## Install (Claude Code)

```bash
pip install -r mcp_server/requirements.txt
```

```text
/plugin marketplace add ./curelo-bookings-poc
/plugin install curelo-bookings-poc@curelo-bookings-poc-marketplace
/reload-plugins
```

## Try it

- *What's our repeat customer rate?*
- `/curelo-bookings-poc:monthly-report`

## What was auto-generated vs hand-written

**Auto-generated by Gemini pipeline:**
{chr(10).join(f'- {x}' for x in auto)}

**Hand-written / deterministic:**
{chr(10).join(f'- {x}' for x in hand)}

## Dashboard artifact

See `artifacts/dashboard.html` for the Gemini-generated HTML template.

<details>
<summary>Embedded dashboard preview</summary>

```html
{artifact_html[:2000]}{'...' if len(artifact_html) > 2000 else ''}
```

</details>
"""


def package_plugin(
    csv_path: Path,
    generated: dict[str, Any],
    validation_report: dict,
    output_dir: Path,
) -> Path:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    kpi_names = extract_kpi_names(generated["tool_specs"])

    # Manifests
    claude = output_dir / ".claude-plugin"
    claude.mkdir()
    (claude / "plugin.json").write_text(json.dumps(PLUGIN_JSON, indent=2), encoding="utf-8")
    (claude / "marketplace.json").write_text(json.dumps(MARKETPLACE_JSON, indent=2), encoding="utf-8")
    (output_dir / ".mcp.json").write_text(json.dumps(MCP_JSON, indent=2), encoding="utf-8")

    # Skill + command
    skill_dir = output_dir / "skills" / "booking-analyst"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(generated["skill_md"], encoding="utf-8")

    cmd_dir = output_dir / "commands"
    cmd_dir.mkdir()
    (cmd_dir / generated["recipe_filename"]).write_text(generated["recipe_md"], encoding="utf-8")

    # MCP server
    mcp_dir = output_dir / "mcp_server"
    mcp_dir.mkdir()
    (mcp_dir / "server.py").write_text(_server_py(kpi_names), encoding="utf-8")
    (mcp_dir / "kpi_logic.py").write_text(_kpi_logic_py(), encoding="utf-8")
    (mcp_dir / "requirements.txt").write_text(MCP_REQUIREMENTS, encoding="utf-8")

    # Data + artifact
    data_dir = output_dir / "data"
    data_dir.mkdir()
    shutil.copy2(csv_path, data_dir / "sample_bookings_mis.csv")

    art_dir = output_dir / "artifacts"
    art_dir.mkdir()
    (art_dir / "dashboard.html").write_text(generated["artifact_html"], encoding="utf-8")

    (output_dir / "generated_tool_specs.json").write_text(
        json.dumps(generated["tool_specs"], indent=2), encoding="utf-8"
    )

    (output_dir / "README.md").write_text(
        _readme(generated, validation_report, generated["artifact_html"]),
        encoding="utf-8",
    )
    (output_dir / ".gitignore").write_text("__pycache__/\n*.py[cod]\n.venv/\nvenv/\n", encoding="utf-8")

    return output_dir
