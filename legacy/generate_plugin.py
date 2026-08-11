#!/usr/bin/env python3
"""Gemini-driven plugin generation pipeline — POC for Curelo."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from pipeline.classify import classify_industry
from pipeline.generate import run_generation
from pipeline.ingest import ingest_csv
from pipeline.package import package_plugin
from pipeline.profile import build_schema_profile, build_structural_only
from pipeline.validate import run_validation

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "sample_bookings_mis.csv"
PACK_PATH = ROOT / "healthcare_industry_pack.json"
SCHEMA_OUT = ROOT / "schema_profile.json"
GENERATED_RAW = ROOT / "generated_raw"
VALIDATION_OUT = ROOT / "validation_report.json"
PLUGIN_OUT = ROOT / "curelo-bookings-poc"


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def log(stage: int, title: str, detail: str = "") -> None:
    line = f"\n{'=' * 60}\nSTAGE {stage}: {title}\n{'=' * 60}"
    print(line)
    if detail:
        print(detail)


def main() -> int:
    _load_dotenv()
    print("Curelo Plugin Generation Pipeline")
    print(f"Project root: {ROOT}")

    if not CSV_PATH.exists():
        print(f"ERROR: Missing {CSV_PATH}", file=sys.stderr)
        return 1
    if not PACK_PATH.exists():
        print(f"ERROR: Missing {PACK_PATH}", file=sys.stderr)
        return 1

    # Stage 1
    log(1, "INGEST", f"Loading {CSV_PATH.name}...")
    df, metadata = ingest_csv(CSV_PATH)
    print(f"  Loaded {metadata['row_count']} rows, {metadata['column_count']} columns")

    # Stage 2
    log(2, "PROFILE", "Structural profiling + Gemini semantic analysis...")
    partial = build_structural_only(df, metadata)
    SCHEMA_OUT.write_text(json.dumps(partial, indent=2, default=str), encoding="utf-8")
    print(f"  Saved structural profile to {SCHEMA_OUT}")
    try:
        schema_profile = build_schema_profile(df, metadata)
    except RuntimeError as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        print("  Tip: copy .env.example to .env and set GEMINI_API_KEY", file=sys.stderr)
        return 1
    SCHEMA_OUT.write_text(json.dumps(schema_profile, indent=2, default=str), encoding="utf-8")
    print(f"  Saved {SCHEMA_OUT}")
    insights = schema_profile.get("gemini_semantic_profile", {}).get("candidate_insights", [])
    print(f"  Gemini proposed {len(insights)} candidate insight(s)")

    # Stage 3
    log(3, "CLASSIFY", f"Loading {PACK_PATH.name}...")
    industry_pack = classify_industry(PACK_PATH)

    # Stage 4
    log(4, "GENERATE", "Four Gemini calls → generated_raw/")
    generated = run_generation(schema_profile, industry_pack, GENERATED_RAW)
    print(f"  Raw outputs saved to {GENERATED_RAW}/")

    # Stage 5
    log(5, "VALIDATE", "Fact-check + self-critique + KPI dry-run...")
    validation_report = run_validation(df, schema_profile, generated)
    VALIDATION_OUT.write_text(json.dumps(validation_report, indent=2, default=str), encoding="utf-8")
    print(f"  Saved {VALIDATION_OUT}")
    print(f"  Overall validation: {validation_report['overall']}")

    if validation_report["fact_check"]["issues"]:
        print("  Fact-check warnings:")
        for issue in validation_report["fact_check"]["issues"]:
            print(f"    - {issue}")

    critique = validation_report["self_critique"]["critique"]
    print(f"  Self-critique: {critique.get('overall_assessment')} ({len(critique.get('findings', []))} findings)")

    for kpi in validation_report["dry_run"]["kpi_results"]:
        status = kpi["status"].upper()
        line = f"    [{status}] {kpi['kpi_name']}"
        if kpi.get("result"):
            line += f" → {json.dumps(kpi['result'], default=str)[:120]}"
        if kpi.get("error"):
            line += f" → ERROR: {kpi['error']}"
        print(line)

    # Stage 6
    log(6, "PACKAGE", f"Assembling plugin at {PLUGIN_OUT.name}/...")
    if validation_report["overall"] == "fail":
        print("  HARD VALIDATION FAILURES — packaging skipped.")
        print("  Fix issues in generated_raw/ and re-run, or inspect validation_report.json.")
        return 2

    plugin_path = package_plugin(CSV_PATH, generated, validation_report, PLUGIN_OUT)
    print(f"  Plugin packaged at {plugin_path.resolve()}")

    log(0, "DONE", "Pipeline complete. Next steps:")
    print("  1. Review generated_raw/ for Gemini's unvalidated first pass")
    print("  2. Review validation_report.json")
    print("  3. claude plugin validate ./curelo-bookings-poc")
    print("  4. /plugin marketplace add ./curelo-bookings-poc")
    return 0


if __name__ == "__main__":
    sys.exit(main())
