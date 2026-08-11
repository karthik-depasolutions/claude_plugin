#!/usr/bin/env python3
"""Re-validate and package from existing generated_raw/ without calling Gemini."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline.ingest import ingest_csv
from pipeline.kpi_engine import dry_run_kpis
from pipeline.package import package_plugin
from pipeline.validate import fact_check_outputs

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "generated_raw"


def main() -> int:
    schema = json.loads((ROOT / "schema_profile.json").read_text(encoding="utf-8"))
    df, _ = ingest_csv(ROOT / "sample_bookings_mis.csv")

    recipe_files = [f for f in RAW.glob("*.md") if f.name != "skill.md"]
    generated = {
        "skill_md": (RAW / "skill.md").read_text(encoding="utf-8"),
        "tool_specs": json.loads((RAW / "tool_specs.json").read_text(encoding="utf-8")),
        "recipe_filename": recipe_files[0].name,
        "recipe_md": recipe_files[0].read_text(encoding="utf-8"),
        "artifact_html": (RAW / "dashboard.html").read_text(encoding="utf-8"),
    }

    fact = fact_check_outputs(schema, generated)
    kpi_runs = dry_run_kpis(df, generated["tool_specs"])
    kpi_fails = [k for k in kpi_runs if k["status"] == "fail"]
    dry_status = "fail" if kpi_fails else "pass"

    validation = {
        "fact_check": fact,
        "self_critique": {"check": "self_critique", "status": "pass", "note": "from prior run"},
        "dry_run": {"check": "dry_run", "status": dry_status, "kpi_results": kpi_runs},
        "overall": "fail" if fact["status"] == "fail" or dry_status == "fail" else "pass",
    }
    (ROOT / "validation_report.json").write_text(
        json.dumps(validation, indent=2, default=str), encoding="utf-8"
    )

    print(f"Validation: {validation['overall']}")
    for k in kpi_runs:
        print(f"  [{k['status'].upper()}] {k['kpi_name']}")

    if validation["overall"] == "fail":
        return 1

    package_plugin(ROOT / "sample_bookings_mis.csv", generated, validation, ROOT / "curelo-bookings-poc")
    print("Plugin packaged at curelo-bookings-poc/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
