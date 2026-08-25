"""U4 — business question synthesis + validation.

Deterministic heuristics generate candidates; every sql_sketch is validated
via sqlglot + DuckDB dry-run. Only survivors are kept, ranked by support.
"""

from __future__ import annotations

import duckdb
import sqlglot

from forge_core.models.data_understanding import BusinessQuestion, UnderstandingRole
from forge_core.models.datasource import DataSource
from forge_core.runtime_session import open_session


def generate_candidate_questions(
    columns: list,
    tables: list,
) -> list[BusinessQuestion]:
    """Deterministic heuristics: measure×timestamp, measure×dimension, dimension-only,
    status-filtered, etc. Limited to 8-10 candidates, each with sql_sketch.
    """
    measures = [c for c in columns if c.understanding_role == UnderstandingRole.MEASURE and not c.is_likely_pii]
    dimensions = [c for c in columns if c.understanding_role in (UnderstandingRole.DIMENSION, UnderstandingRole.STATUS) and not c.is_likely_pii]
    timestamps = [c for c in columns if c.understanding_role == UnderstandingRole.TIMESTAMP]
    # Also consider status columns separately for filtered questions
    statuses = [c for c in dimensions if c.understanding_role == UnderstandingRole.STATUS]

    candidates: list[BusinessQuestion] = []

    # 1. Trends: each measure × each timestamp (keep first 2 combos)
    for m in measures[:2]:
        for t in timestamps[:1]:
            # Use same table only for U4 deterministic; cross-table joins left to LLM/agent
            if m.table != t.table:
                continue
            candidates.append(
                BusinessQuestion(
                    question=f"What is the trend of {m.business_name} over time?",
                    sql_sketch=f'SELECT date_trunc(\'month\', "{t.name}"), SUM("{m.name}") FROM {t.table} GROUP BY 1 ORDER BY 1',
                    support=0.75,
                    tables=[t.table],
                    columns=[f"{m.table}.{m.name}", f"{t.table}.{t.name}"],
                )
            )
            # Also weekly granularity variant if enough data
            candidates.append(
                BusinessQuestion(
                    question=f"What is the weekly trend of {m.business_name}?",
                    sql_sketch=f'SELECT date_trunc(\'week\', "{t.name}"), SUM("{m.name}") FROM {t.table} GROUP BY 1 ORDER BY 1',
                    support=0.6,
                    tables=[t.table],
                    columns=[f"{m.table}.{m.name}", f"{t.table}.{t.name}"],
                )
            )

    # 2. Breakdowns: each measure × each dimension (top 3 dimensions)
    for m in measures[:1]:
        for d in dimensions[:3]:
            if m.table != d.table:
                continue
            # Skip high-cardinality dimensions that would produce too many groups
            if d.cardinality > 50:
                continue
            candidates.append(
                BusinessQuestion(
                    question=f"How does {m.business_name} break down by {d.business_name}?",
                    sql_sketch=f'SELECT "{d.name}", SUM("{m.name}") FROM {d.table} GROUP BY 1 ORDER BY 2 DESC LIMIT 20',
                    support=0.7,
                    tables=[d.table],
                    columns=[f"{m.table}.{m.name}", f"{d.table}.{d.name}"],
                )
            )

    # 3. Distribution: each dimension alone (top 2)
    for d in dimensions[:2]:
        if d.cardinality > 100:
            continue
        candidates.append(
            BusinessQuestion(
                question=f"What is the distribution of {d.business_name}?",
                sql_sketch=f'SELECT "{d.name}", COUNT(*) AS cnt FROM {d.table} GROUP BY 1 ORDER BY 2 DESC',
                support=0.65,
                tables=[d.table],
                columns=[f"{d.table}.{d.name}"],
            )
        )

    # 4. Filtered: status-filtered measure (e.g. Completed vs Cancelled)
    for m in measures[:1]:
        for s in statuses[:1]:
            if m.table != s.table or not s.vocabulary:
                continue
            # Pick most frequent status value as filter example
            top_val = s.vocabulary[0].value if s.vocabulary else None
            if top_val:
                candidates.append(
                    BusinessQuestion(
                        question=f"What is {m.business_name} for {s.business_name} = {top_val!r}?",
                        sql_sketch=f'SELECT SUM("{m.name}") FROM {m.table} WHERE "{s.name}" = \'{top_val}\'',
                        support=0.6,
                        tables=[m.table],
                        columns=[f"{m.table}.{m.name}", f"{s.table}.{s.name}"],
                    )
                )

    # 5. Top-N: largest entities by measure
    for m in measures[:1]:
        for d in dimensions[:1]:
            if m.table != d.table:
                continue
            if d.cardinality < 5:
                continue
            candidates.append(
                BusinessQuestion(
                    question=f"Which {d.business_name} have the highest {m.business_name}?",
                    sql_sketch=f'SELECT "{d.name}", SUM("{m.name}") AS total FROM {d.table} GROUP BY 1 ORDER BY total DESC LIMIT 10',
                    support=0.6,
                    tables=[d.table],
                    columns=[f"{m.table}.{m.name}", f"{d.table}.{d.name}"],
                )
            )

    # Deduplicate by question text, keep first
    seen: set[str] = set()
    deduped: list[BusinessQuestion] = []
    for q in candidates:
        if q.question not in seen:
            seen.add(q.question)
            deduped.append(q)
    return deduped[:10]


def _translate_sketch(sketch: str, data_source: DataSource) -> str:
    """Replace logical table names in a sketch with physical_refs so DuckDB can execute it.

    The sketches are authored against logical names (e.g. FROM bookings) but the
    DuckDB session exposes physical views (e.g. src_bookings). Without translation
    the dry-run fails with 'Table with name bookings does not exist'.
    """
    import re

    physical_map = {t.name: t.physical_ref for t in data_source.tables}
    translated = sketch
    for logical, physical in physical_map.items():
        # FROM / JOIN <table> — handle quoted and unquoted, case-insensitive
        # physical is already a valid DuckDB identifier (view name or quoted)
        translated = re.sub(
            rf'\bFROM\s+\"?{re.escape(logical)}\"?\b',
            f"FROM {physical}",
            translated,
            flags=re.IGNORECASE,
        )
        translated = re.sub(
            rf'\bJOIN\s+\"?{re.escape(logical)}\"?\b',
            f"JOIN {physical}",
            translated,
            flags=re.IGNORECASE,
        )
    return translated


def validate_questions(
    questions: list[BusinessQuestion],
    data_source: DataSource,
) -> list[BusinessQuestion]:
    """Validate each sql_sketch via sqlglot parse + DuckDB dry-run.
    Returns only survivors, each with support possibly adjusted by row coverage.
    """
    survivors: list[BusinessQuestion] = []
    try:
        con = open_session(data_source)
    except Exception:
        # If we can't open, return questions without sketches (keep question text)
        return [q for q in questions if q.sql_sketch is None]

    try:
        for q in questions:
            if q.sql_sketch is None:
                survivors.append(q)
                continue
            sketch = q.sql_sketch.strip()
            # Translate logical -> physical for execution, but keep original for storage
            exec_sketch = _translate_sketch(sketch, data_source)
            # 1. sqlglot parse (duckdb dialect) on original (logical) — checks syntax
            try:
                sqlglot.parse_one(sketch, read="duckdb")
            except Exception:
                # Also try translated, in case logical name was the parse issue
                try:
                    sqlglot.parse_one(exec_sketch, read="duckdb")
                except Exception:
                    continue
            # 2. DuckDB dry-run with LIMIT 1 to check executability
            try:
                con.execute(f"SELECT * FROM ({exec_sketch}) AS _q LIMIT 1").fetchall()
            except Exception:
                continue
            # 3. Optionally adjust support by checking if query returns rows
            try:
                rows = con.execute(f"SELECT COUNT(*) FROM ({exec_sketch}) AS _q").fetchone()
                cnt = rows[0] if rows else 0
                if cnt == 0:
                    q = q.model_copy(update={"support": round(q.support * 0.5, 2)})
                survivors.append(q)
            except Exception:
                survivors.append(q)
    finally:
        try:
            con.close()
        except Exception:
            pass

    # Rank by support desc, then question text
    survivors.sort(key=lambda x: (-x.support, x.question))
    return survivors


__all__ = ["generate_candidate_questions", "validate_questions"]
