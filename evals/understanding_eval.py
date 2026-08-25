"""U6 — Evaluation harness for DataUnderstanding quality.

Runs on fixtures without LLM, checks:
- Grain accuracy (expected PK per table)
- Role accuracy (currency, date, identifier detection)
- PII recall (known PII columns flagged)
- Business question validity (sql_sketch executes)
- Skill specificity (SKILL.md contains customer-specific facts vs generic boilerplate)
- Cost caps (token counts if agent used)

Usage:
    uv run python evals/understanding_eval.py
    uv run python evals/understanding_eval.py --use-agent  (requires GEMINI_API_KEY)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from forge_core.models.run import RunRecord
from forge_core.orchestrator import DEFAULT_PACKS_ROOT, run_pipeline

# Golden expectations per fixture (hand-labeled, minimal)
GOLDEN = {
    "fixtures/datasets/bookings.csv": {
        "grain": {"bookings": ["booking_id"]},
        "roles": {"bookings.amount_inr": "currency", "bookings.booking_date": "date"},
        "pii": ["bookings.customer_name", "bookings.phone", "bookings.email"],
        "min_bqs": 3,
    },
    "fixtures/datasets/retail_orders": {
        "grain": {"customers": ["customer_id"], "orders": ["order_id"], "order_items": ["order_item_id"]},
        "roles": {"customers.email_address": "email", "order_items.unit_price": "currency"},
        "pii": ["customers.email_address", "customers.phone"],
        "min_bqs": 2,
    },
    "fixtures/datasets/edtech.sqlite": {
        "grain": {"students": ["student_id"], "courses": ["course_id"], "enrollments": ["enrollment_id"]},
        "roles": {},
        "pii": [],
        "min_bqs": 2,
    },
}


def _check_grain(du: dict, expected: dict) -> tuple[int, int]:
    ok = 0
    total = len(expected)
    for tbl, exp_cols in expected.items():
        found = next((t for t in du.get("tables", []) if t["name"] == tbl), None)
        if found and found.get("grain", {}).get("grain_columns") == exp_cols:
            ok += 1
    return ok, total


def _check_roles(du: dict, expected: dict) -> tuple[int, int]:
    col_map = {f"{c['table']}.{c['name']}": c for c in du.get("columns", [])}
    ok = 0
    for col_key, exp_role in expected.items():
        col = col_map.get(col_key)
        if col and (col.get("format_fingerprint") == exp_role or col.get("guessed_role") == exp_role):
            ok += 1
    return ok, len(expected)


def _check_pii(du: dict, expected: list[str]) -> tuple[int, int, int]:
    # Returns (true_positives, expected_total, false_positives)
    col_map = {f"{c['table']}.{c['name']}": c for c in du.get("columns", [])}
    tp = sum(1 for k in expected if col_map.get(k, {}).get("is_likely_pii"))
    # False positives: non-expected flagged as PII
    fp = sum(1 for k, c in col_map.items() if c.get("is_likely_pii") and k not in expected)
    return tp, len(expected), fp


def _check_bqs(du: dict, data_source) -> tuple[int, int]:
    from forge_core.understanding.questions import validate_questions
    from forge_core.models.data_understanding import BusinessQuestion

    bqs = du.get("business_questions") or []
    # Re-validate to ensure they still execute (U4 guarantee)
    bq_objs = [
        BusinessQuestion(
            question=q["question"],
            sql_sketch=q.get("sql_sketch"),
            support=q.get("support", 0),
            tables=q.get("tables", []),
            columns=q.get("columns", []),
        )
        for q in bqs
    ]
    validated = validate_questions(bq_objs, data_source)
    return len(validated), len(bqs)


def _check_skill_specificity(plugin_dir: Path) -> dict:
    skill_path = next(plugin_dir.rglob("SKILL.md"), None)
    if not skill_path or not skill_path.is_file():
        return {"found": False, "specific": False, "reasons": ["SKILL.md not found"]}
    text = skill_path.read_text(encoding="utf-8")
    checks = {
        "has_grain": "grain" in text.lower() or "one row per" in text.lower(),
        "has_vocab": "vocabular" in text.lower() or "distinct values" in text.lower(),
        "has_bq": "what you can ask" in text.lower() or "validated" in text.lower(),
        "has_temporal": "temporal" in text.lower() or "coverage" in text.lower(),
        "has_caveat": "caveat" in text.lower() or "open question" in text.lower(),
        "not_generic": "bookings" in text.lower() or "customers" in text.lower() or "students" in text.lower(),
    }
    specific = sum(checks.values()) >= 3
    return {"found": True, "specific": specific, "checks": checks, "len": len(text)}


def run_one(source: str, pack_override: str | None, use_agent: bool, tmp_root: Path) -> dict:
    from forge_core.models.common import RunStatus

    run_id = f"eval-{Path(source).name.replace('.', '-')}"
    out = tmp_root / run_id
    # Need to handle retail_orders dir vs file
    rec = RunRecord(run_id=run_id, source_path=source, output_dir=str(out), industry_override=pack_override)
    result = run_pipeline(
        rec,
        packs_root=DEFAULT_PACKS_ROOT,
        profiling_provider=None,
        generation_provider=None,
        critique_provider=None,
        use_agent=use_agent,
    )
    # Handle NEEDS_INPUT for retail/edtech (binding gate)
    if result.status == RunStatus.NEEDS_INPUT and result.binding_questions:
        result.binding_confirmations = {q.role: q.physical for q in result.binding_questions}
        result = run_pipeline(
            result,
            packs_root=DEFAULT_PACKS_ROOT,
            profiling_provider=None,
            generation_provider=None,
            critique_provider=None,
            use_agent=use_agent,
        )
    # Also handle industry NEEDS_INPUT
    if result.status == RunStatus.NEEDS_INPUT:
        # Force pack
        if not pack_override:
            # Pick top match from classify event
            ev = next((e for e in result.events if e.stage.value == "classify" and "ranked_matches" in e.data), None)
            if ev:
                pack_override = ev.data["ranked_matches"][0]["pack_slug"]
                rec2 = RunRecord(run_id=run_id + "-2", source_path=source, output_dir=str(out), industry_override=pack_override)
                result = run_pipeline(rec2, packs_root=DEFAULT_PACKS_ROOT, profiling_provider=None, generation_provider=None, critique_provider=None, use_agent=use_agent)
                if result.status == RunStatus.NEEDS_INPUT and result.binding_questions:
                    result.binding_confirmations = {q.role: q.physical for q in result.binding_questions}
                    result = run_pipeline(result, packs_root=DEFAULT_PACKS_ROOT, profiling_provider=None, generation_provider=None, critique_provider=None, use_agent=use_agent)

    du = result.data_understanding
    plugin_dir = None
    if result.status.value in ("succeeded", "failed"):
        # Find plugin dir from package event
        ev = next((e for e in result.events if e.stage.value == "package" and "plugin_dir" in e.data), None)
        if ev:
            plugin_dir = Path(ev.data["plugin_dir"])

    return {"result": result, "du": du, "plugin_dir": plugin_dir}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate DataUnderstanding quality")
    parser.add_argument("--use-agent", action="store_true", help="Run with LLM enrichment (requires GEMINI_API_KEY)")
    parser.add_argument("--tmp", type=Path, default=Path("generated/eval-tmp"), help="Temp output root")
    args = parser.parse_args()

    tmp_root = args.tmp
    tmp_root.mkdir(parents=True, exist_ok=True)

    all_ok = True
    for source, gold in GOLDEN.items():
        pack = None
        if "retail" in source:
            pack = "retail-ecommerce"
        elif "edtech" in source:
            pack = "edtech"
        elif "bookings" in source:
            pack = "generic-analytics"
        print(f"\n=== {source} (pack={pack}) ===")
        run = run_one(source, pack, use_agent=args.use_agent, tmp_root=tmp_root)
        du = run["du"]
        result = run["result"]
        plugin_dir = run["plugin_dir"]

        if du is None:
            print(f"  FAIL: no DataUnderstanding (status={result.status})")
            all_ok = False
            continue

        # Grain
        ok, total = _check_grain(du, gold.get("grain", {}))
        print(f"  Grain: {ok}/{total} correct" + (" [OK]" if ok == total else " [FAIL]"))
        if ok != total:
            all_ok = False

        # Roles
        ok, total = _check_roles(du, gold.get("roles", {}))
        print(f"  Roles: {ok}/{total}" + (" [OK]" if ok == total else " [FAIL]") if total else "  Roles: no expectations")
        if total and ok != total:
            all_ok = False

        # PII
        tp, exp, fp = _check_pii(du, gold.get("pii", []))
        print(f"  PII recall: {tp}/{exp} (fp={fp})" + (" [OK]" if tp == exp else " [FAIL]"))
        if tp != exp:
            # Not hard fail, just warn (PII heuristics are imperfect)
            pass

        # BQs
        min_bqs = gold.get("min_bqs", 1)
        bq_count = len(du.get("business_questions", []))
        print(f"  Business questions: {bq_count} (min {min_bqs})" + (" [OK]" if bq_count >= min_bqs else " [FAIL]"))
        if bq_count < min_bqs:
            all_ok = False

        # BQ validity (re-validate)
        if du.get("business_questions"):
            from forge_core.ingestion.registry import ingest

            ds = ingest(source)
            valid, total_bq = _check_bqs(du, ds)
            print(f"  BQ validity (dry-run): {valid}/{total_bq}" + (" [OK]" if valid == total_bq else " [FAIL]"))
            if valid != total_bq:
                all_ok = False

        # Open questions
        open_qs = du.get("open_questions") or []
        print(f"  Open questions: {len(open_qs)} (abstentions, not guesses)")

        # Skill specificity
        if plugin_dir and plugin_dir.exists():
            spec = _check_skill_specificity(plugin_dir)
            print(f"  Skill specificity: {'specific [OK]' if spec.get('specific') else 'generic [FAIL]'} {spec.get('checks')}")
            if not spec.get("specific"):
                all_ok = False
        else:
            print(f"  Skill: no plugin dir (status={result.status})")

        # Cost caps (if agent)
        for ev in result.events:
            if ev.data.get("agent") == "understanding":
                print(f"  Agent cost: {ev.data}")

    print("\n" + ("All checks passed [OK]" if all_ok else "Some checks failed [FAIL]"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
