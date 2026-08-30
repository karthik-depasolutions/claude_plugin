"""Stage 2b — the deterministic half of the data-quality review (Layer 1,
facts with evidence — see models/quality.py's module docstring for how this
differs from `SemanticProfile.data_quality_flags`, which is an LLM claim).

`sample_values` on `ColumnProfile` (structural.py) is 5 arbitrary, unordered
distinct values and only populated for non-numeric columns — it cannot
answer "how dominant is this value" or "are these two spellings the same
thing". This module adds exactly the one query type structural.py doesn't
run: a value-frequency histogram, batched one scan per table in the same
style as `mis_mcp_runtime.tools.get_data_profile` (indexed aliases, never
column-derived; `fetchdf()` + named lookup rather than positional tuple
offsets, since which aggregates get emitted varies per column).

Every ordering here is a *total* order (count desc, then value asc) because
finding ids and their evidence must be byte-identical across a pipeline
replay — see orchestrator.py's pause: a resumed run must never regenerate
`RunRecord.data_review`, and definitely must never reorder it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import duckdb

from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.common import Severity
from forge_core.models.datasource import DataSource, TableDescriptor
from forge_core.models.quality import DataQuestion, DataReview, QualityFinding, ValueCount
from forge_core.models.schema_profile import ColumnProfile, SemanticProfile, StructuralProfile
from forge_core.profiling.structural import _NUMERIC_DUCKDB_TYPES, _TEMPORAL_DUCKDB_TYPES, _base_type

# A column with more distinct values than this is either free text or a
# genuine identifier-shaped field - a histogram over it is noise, not signal.
TOP_VALUE_CARDINALITY_CAP = 50
# ponytail: whole-table scan per eligible column set; add USING SAMPLE +
# SETSEED if a customer table blows this - rejected sampling for now because
# it breaks exact percentages and isn't reproducible across a replay, which
# this whole module depends on.
MAX_ROWS_FOR_FREQUENCY = 5_000_000
DOMINANT_VALUE_THRESHOLD = 0.60
HIGH_NULL_THRESHOLD = 40.0
MIXED_TYPE_MIN_RATIO = 0.05
MIXED_TYPE_MAX_RATIO = 0.95
MAX_TOP_VALUES = 5
# Numeric outlier detection (Tukey's IQR rule) - the one gap the cardinality-capped
# categorical checks above can't cover: continuous columns (amounts, durations) that
# are exactly the high-cardinality case _is_eligible excludes.
OUTLIER_IQR_MULTIPLIER = 1.5
MIN_NUMERIC_COUNT_FOR_OUTLIERS = 20
OUTLIER_FRACTION_THRESHOLD = 0.01

Histogram = dict[str, int]


def _is_eligible(col: ColumnProfile) -> bool:
    # Deliberately NOT gated on is_likely_identifier: that flag is a naming
    # heuristic only (any "*_id" column), so a true high-cardinality row id
    # (lead_id, booking_id) is already excluded by the cardinality cap below -
    # cardinality there is essentially row_count, always far past the cap.
    # The cap alone is the real gate; adding the name-based flag on top of it
    # only ever *removes* something the cap wouldn't have: a low-cardinality
    # "*_id"-named dimension like campaign_id or agent_id, which is exactly
    # the kind of column this analysis exists to find dominance in.
    return (
        not col.is_likely_pii
        and 0 < col.cardinality <= TOP_VALUE_CARDINALITY_CAP
        and _base_type(col.dtype) not in _TEMPORAL_DUCKDB_TYPES
    )


def _is_numeric(col: ColumnProfile) -> bool:
    return _base_type(col.dtype) in _NUMERIC_DUCKDB_TYPES


def _is_numeric_eligible(col: ColumnProfile) -> bool:
    # No cardinality cap (unlike _is_eligible) - continuous columns are exactly
    # the high-cardinality case the categorical histogram check excludes.
    return not col.is_likely_pii and not col.is_likely_identifier and col.cardinality > 1 and _is_numeric(col)


def _looks_numeric(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _ordered(hist: Histogram) -> list[tuple[str, int]]:
    """Count desc, value asc - the one ordering every finding's evidence uses."""
    return sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))


def _top_values(hist: Histogram, total: int) -> list[ValueCount]:
    return [
        ValueCount(value=v, count=c, percent=round(c / total * 100.0, 1))
        for v, c in _ordered(hist)[:MAX_TOP_VALUES]
    ]


def _frequency_scan(
    con: duckdb.DuckDBPyConnection, table: TableDescriptor, eligible: list[ColumnProfile]
) -> dict[str, tuple[Histogram | None, int | None]]:
    """One scan for the whole table. Returns {column_name: (histogram, numlike_count)}
    - numlike_count is None for already-numeric columns, where a TRY_CAST
    check would be meaningless."""
    if not eligible:
        return {}

    aggregates: list[str] = []
    wants_numlike: list[bool] = []
    for i, col in enumerate(eligible):
        quoted = f'"{col.name}"'
        aggregates.append(f'histogram(CAST({quoted} AS VARCHAR)) AS hist_{i}')
        textual = not _is_numeric(col)
        wants_numlike.append(textual)
        if textual:
            aggregates.append(f'COUNT(*) FILTER (WHERE TRY_CAST({quoted} AS DOUBLE) IS NOT NULL) AS numlike_{i}')

    sql = f"SELECT {', '.join(aggregates)} FROM {table.physical_ref}"
    df = con.execute(sql).fetchdf()
    row = df.iloc[0]

    result: dict[str, tuple[Histogram | None, int | None]] = {}
    for i, col in enumerate(eligible):
        hist = row[f"hist_{i}"] or None  # histogram() returns None (not {}) for an all-NULL column
        numlike = int(row[f"numlike_{i}"]) if wants_numlike[i] else None
        result[col.name] = (hist, numlike)
    return result


def _quantile_scan(
    con: duckdb.DuckDBPyConnection, table: TableDescriptor, cols: list[ColumnProfile]
) -> dict[str, tuple[float, float, int]]:
    """One scan for the whole table's eligible numeric columns: {column_name: (q1, q3, non_null_count)}."""
    if not cols:
        return {}
    aggregates: list[str] = []
    for i, col in enumerate(cols):
        quoted = f'"{col.name}"'
        aggregates.append(f'quantile_cont({quoted}, 0.25) AS q1_{i}')
        aggregates.append(f'quantile_cont({quoted}, 0.75) AS q3_{i}')
        aggregates.append(f'COUNT({quoted}) AS n_{i}')
    sql = f"SELECT {', '.join(aggregates)} FROM {table.physical_ref}"
    row = con.execute(sql).fetchdf().iloc[0]

    result: dict[str, tuple[float, float, int]] = {}
    for i, col in enumerate(cols):
        q1, q3 = row[f"q1_{i}"], row[f"q3_{i}"]
        if q1 is None or q3 is None:  # all-NULL column
            continue
        result[col.name] = (float(q1), float(q3), int(row[f"n_{i}"]))
    return result


def _outlier_count_scan(
    con: duckdb.DuckDBPyConnection, table: TableDescriptor, bounds: dict[str, tuple[float, float]]
) -> dict[str, int]:
    """One scan for outlier counts, given IQR bounds already computed per column."""
    if not bounds:
        return {}
    names = list(bounds)
    aggregates = [
        f'COUNT(*) FILTER (WHERE "{name}" < {bounds[name][0]!r} OR "{name}" > {bounds[name][1]!r}) AS oc_{i}'
        for i, name in enumerate(names)
    ]
    sql = f"SELECT {', '.join(aggregates)} FROM {table.physical_ref}"
    row = con.execute(sql).fetchdf().iloc[0]
    return {name: int(row[f"oc_{i}"]) for i, name in enumerate(names)}


def _check_high_null(col: ColumnProfile) -> QualityFinding | None:
    if col.null_percent < HIGH_NULL_THRESHOLD:
        return None
    return QualityFinding(
        id=f"high_null:{col.table}.{col.name}",
        code="high_null",
        severity=Severity.MEDIUM,
        table=col.table,
        column=col.name,
        summary=f'"{col.name}" is empty for {col.null_percent:.1f}% of rows.',
    )


def _check_single_value(col: ColumnProfile, hist: Histogram | None) -> QualityFinding | None:
    if col.cardinality != 1:
        return None
    if hist:
        value, count = next(iter(hist.items()))
        summary = f'"{col.name}" is always "{value}" ({count:,} row(s)).'
        top_values = [ValueCount(value=value, count=count, percent=100.0)]
    else:
        summary = f'"{col.name}" has only one distinct value across all rows.'
        top_values = []
    return QualityFinding(
        id=f"single_value:{col.table}.{col.name}",
        code="single_value",
        severity=Severity.LOW,
        table=col.table,
        column=col.name,
        summary=summary,
        top_values=top_values,
    )


def _check_dominant_value(col: ColumnProfile, hist: Histogram) -> QualityFinding | None:
    total = sum(hist.values())
    if total == 0:
        return None
    ordered = _ordered(hist)
    top_value, top_count = ordered[0]
    fraction = top_count / total
    if fraction < DOMINANT_VALUE_THRESHOLD:
        return None
    return QualityFinding(
        id=f"dominant_value:{col.table}.{col.name}",
        code="dominant_value",
        severity=Severity.MEDIUM,
        table=col.table,
        column=col.name,
        summary=f'{fraction * 100:.1f}% of {total:,} non-null rows in "{col.name}" are "{top_value}".',
        top_values=_top_values(hist, total),
    )


def _check_inconsistent_format(col: ColumnProfile, hist: Histogram) -> QualityFinding | None:
    groups: dict[str, list[str]] = {}
    for raw_value in hist:
        groups.setdefault(raw_value.strip().lower(), []).append(raw_value)
    clusters = [variants for variants in groups.values() if len(variants) > 1]
    if not clusters:
        return None

    total = sum(hist.values())

    def cluster_count(variants: list[str]) -> int:
        return sum(hist[v] for v in variants)

    ordered_clusters = sorted(clusters, key=lambda variants: (-cluster_count(variants), min(variants)))
    examples = ", ".join(
        "/".join(sorted(variants, key=lambda v: (-hist[v], v))[:3]) for variants in ordered_clusters[:3]
    )
    flat_values = sorted(
        (v for variants in clusters for v in variants), key=lambda v: (-hist[v], v)
    )[:MAX_TOP_VALUES]
    return QualityFinding(
        id=f"inconsistent_format:{col.table}.{col.name}",
        code="inconsistent_format",
        severity=Severity.LOW,
        table=col.table,
        column=col.name,
        summary=f'"{col.name}" has multiple spellings of the same value: {examples}.',
        top_values=[ValueCount(value=v, count=hist[v], percent=round(hist[v] / total * 100.0, 1)) for v in flat_values],
    )


def _check_mixed_types(col: ColumnProfile, hist: Histogram, numlike_count: int | None) -> QualityFinding | None:
    if numlike_count is None:
        return None
    total = sum(hist.values())
    if total == 0:
        return None
    ratio = numlike_count / total
    if not (MIXED_TYPE_MIN_RATIO <= ratio <= MIXED_TYPE_MAX_RATIO):
        return None
    ordered = _ordered(hist)
    label_examples = [v for v, _ in ordered if not _looks_numeric(v)][:2]
    numeric_examples = [v for v, _ in ordered if _looks_numeric(v)][:2]
    return QualityFinding(
        id=f"mixed_types:{col.table}.{col.name}",
        code="mixed_types",
        severity=Severity.HIGH,
        table=col.table,
        column=col.name,
        summary=(
            f'"{col.name}" mixes text labels ({", ".join(label_examples)}) with '
            f'numeric-looking values ({", ".join(numeric_examples)}) - {ratio * 100:.1f}% look numeric.'
        ),
        top_values=_top_values(hist, total),
    )


def _check_numeric_outliers(
    col: ColumnProfile, q1: float, q3: float, non_null_count: int, outlier_count: int
) -> QualityFinding | None:
    if non_null_count < MIN_NUMERIC_COUNT_FOR_OUTLIERS or outlier_count == 0:
        return None
    fraction = outlier_count / non_null_count
    if fraction < OUTLIER_FRACTION_THRESHOLD:
        return None
    iqr = q3 - q1
    lower, upper = q1 - OUTLIER_IQR_MULTIPLIER * iqr, q3 + OUTLIER_IQR_MULTIPLIER * iqr
    return QualityFinding(
        id=f"numeric_outlier:{col.table}.{col.name}",
        code="numeric_outlier",
        severity=Severity.MEDIUM,
        table=col.table,
        column=col.name,
        summary=(
            f'{outlier_count:,} of {non_null_count:,} non-null values ({fraction * 100:.1f}%) in '
            f'"{col.name}" fall outside the typical range ({lower:,.2f} to {upper:,.2f}, by the IQR rule).'
        ),
    )


_SEVERITY_RANK = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


def analyze_quality(
    data_source: DataSource, structural: StructuralProfile, con: duckdb.DuckDBPyConnection
) -> tuple[list[QualityFinding], list[str]]:
    """Findings only - see profiling.quality module docstring. Takes an
    already-open connection, mirroring build_structural_profile(data_source, con)."""
    findings: list[QualityFinding] = []
    skipped_tables: list[str] = []

    for table in data_source.tables:
        cols = structural.columns_for(table.name)

        # Free checks - no query, already on ColumnProfile from pass 1.
        for col in cols:
            found = _check_high_null(col)
            if found:
                findings.append(found)

        if table.row_count > MAX_ROWS_FOR_FREQUENCY:
            skipped_tables.append(table.name)
            # single_value with no histogram still works from cardinality alone.
            for col in cols:
                if col.cardinality == 1:
                    found = _check_single_value(col, hist=None)
                    if found:
                        findings.append(found)
            continue

        eligible = [c for c in cols if _is_eligible(c)]
        scan = _frequency_scan(con, table, eligible)

        for col in eligible:
            hist, numlike = scan.get(col.name, (None, None))
            if col.cardinality == 1:
                found = _check_single_value(col, hist)
                if found:
                    findings.append(found)
                continue  # a single-value column can't also be dominant/inconsistent/mixed
            if hist is None:
                continue
            for check in (
                lambda c=col, h=hist: _check_dominant_value(c, h),
                lambda c=col, h=hist: _check_inconsistent_format(c, h),
                lambda c=col, h=hist, n=numlike: _check_mixed_types(c, h, n),
            ):
                found = check()
                if found:
                    findings.append(found)

        numeric_eligible = [c for c in cols if _is_numeric_eligible(c)]
        quantiles = _quantile_scan(con, table, numeric_eligible)
        bounds = {
            name: (q1 - OUTLIER_IQR_MULTIPLIER * (q3 - q1), q3 + OUTLIER_IQR_MULTIPLIER * (q3 - q1))
            for name, (q1, q3, _n) in quantiles.items()
        }
        outlier_counts = _outlier_count_scan(con, table, bounds)
        for col in numeric_eligible:
            if col.name not in quantiles:
                continue
            q1, q3, non_null_count = quantiles[col.name]
            found = _check_numeric_outliers(col, q1, q3, non_null_count, outlier_counts.get(col.name, 0))
            if found:
                findings.append(found)

    findings.sort(key=lambda f: (_SEVERITY_RANK[f.severity], f.code, f.table, f.column))
    return findings, skipped_tables


# Findings are already severity-sorted by analyze_quality, so slicing here
# means "the 5 most severe findings get a question" without a second sort.
MAX_QUESTIONS = 5
GENERAL_NOTES_ID = "general_notes"

# One question per code, filled in with just the column name - the finding's
# own `summary` (already deterministic English with the real numbers) rides
# alongside as `DataQuestion.context`, so the template itself stays generic
# rather than re-deriving numbers it doesn't need to.
_FALLBACK_QUESTIONS: dict[str, str] = {
    "dominant_value": 'What does the dominant value in "{column}" mean, and should those rows count as a real outcome?',
    "high_null": 'Is it expected for "{column}" to be empty this often, or is it only collected sometimes?',
    "inconsistent_format": 'Should the different spellings in "{column}" be treated as the same value?',
    "mixed_types": 'What do the numeric-looking values mixed into "{column}" mean?',
    "single_value": 'Is the single value in "{column}" a placeholder, or is it meaningful?',
    "numeric_outlier": 'Are the unusually large or small values in "{column}" real, or data-entry errors?',
}


def _fallback_question(finding: QualityFinding) -> DataQuestion:
    template = _FALLBACK_QUESTIONS[finding.code]
    return DataQuestion(id=finding.id, question=template.format(column=finding.column), context=finding.summary)


def _build_question_prompt(findings: list[QualityFinding], hints: list[str]) -> str:
    findings_json = json.dumps(
        [{"id": f.id, "table": f.table, "column": f.column, "summary": f.summary} for f in findings], indent=2
    )
    hints_block = ""
    if hints:
        hints_block = (
            "\n\nUNVERIFIED HINTS (an earlier model's guesses about this data - may be wrong, "
            "never state as fact, only use them to phrase a sharper question):\n" + json.dumps(hints, indent=2)
        )
    return (
        "You are helping a business owner explain their own data before an analytics plugin is "
        "built on top of it.\n\n"
        "DETERMINISTIC FINDINGS (computed from the real rows - every number is a fact):\n"
        f"{findings_json}"
        f"{hints_block}\n\n"
        "For each finding id above, write ONE question (max 25 words) asking what that pattern "
        "MEANS for their business. Quote the real column name. Never mention a column or value "
        "that does not appear above. One thing per question. Ask what the data means, not what "
        "they should do about it.\n\n"
        'Respond with strict JSON: {"questions": [{"finding_id": "<copied exactly>", "question": "..."}]}.'
    )


def _llm_question_text(findings: list[QualityFinding], provider: LLMProvider, hints: list[str]) -> dict[str, str]:
    """finding_id -> question text, for ids the model actually addressed.
    Never raises - every caller degrades to the deterministic fallback for
    anything missing, exactly like skills.py/agents.py/commands.py do for
    their own LLM calls. The model never assigns an id itself: only finding
    ids we sent are accepted, so a resumed run's answers (keyed to these
    same ids) can never orphan even if this were ever called twice."""
    try:
        raw: Any = provider.generate_json(_build_question_prompt(findings, hints))
    except LLMError:
        return {}
    if not isinstance(raw, dict):
        return {}

    known_ids = {f.id for f in findings}
    result: dict[str, str] = {}
    for item in raw.get("questions", []):
        if not isinstance(item, dict):
            continue
        finding_id = item.get("finding_id")
        text = item.get("question")
        if finding_id in known_ids and isinstance(text, str) and text.strip():
            result.setdefault(finding_id, text.strip())  # first wins if the model repeats an id
    return result


def generate_questions(
    findings: list[QualityFinding], provider: LLMProvider | None, hints: list[str] | None = None
) -> list[DataQuestion]:
    top_findings = findings[:MAX_QUESTIONS]
    llm_text = _llm_question_text(top_findings, provider, hints or []) if provider else {}

    questions = [
        DataQuestion(id=f.id, question=llm_text[f.id], context=f.summary) if f.id in llm_text else _fallback_question(f)
        for f in top_findings
    ]

    # Only offered when there's already something to review - an unprompted
    # "anything else?" with no findings would pause a clean run for nothing,
    # which decision 1 (pause only when there's something to say) forbids.
    if questions:
        questions.append(
            DataQuestion(id=GENERAL_NOTES_ID, question="Anything else about this data an analyst should know?")
        )
    return questions


def build_data_review(
    data_source: DataSource,
    structural: StructuralProfile,
    con: duckdb.DuckDBPyConnection,
    *,
    provider: LLMProvider | None = None,
    semantic: SemanticProfile | None = None,
) -> DataReview:
    findings, skipped_tables = analyze_quality(data_source, structural, con)
    hints = [flag.issue for flag in semantic.data_quality_flags] if semantic else []
    questions = generate_questions(findings, provider, hints)
    return DataReview(
        generated_at=datetime.now(UTC).isoformat(),
        findings=findings,
        questions=questions,
        skipped_tables=skipped_tables,
    )


__all__ = ["analyze_quality", "build_data_review", "generate_questions"]
