"""U1 builder — deterministic DataUnderstanding from SchemaProfile + DataMap + DataSource.

No LLM calls. Every field is either computed or explicitly marked as open_question.
Phase U3 will add agentic enrichment that mutates this artifact.
"""

from __future__ import annotations

from datetime import UTC, datetime

from forge_core.models.common import ColumnRole
from forge_core.models.data_understanding import (
    BusinessQuestion,
    ColumnUnderstanding,
    DataUnderstanding,
    DomainAssessment,
    Evidence,
    OpenQuestion,
    Provenance,
    TableUnderstanding,
    TemporalProfile,
    UnderstandingRole,
    ValueCount,
)
from forge_core.models.datasource import DataSource
from forge_core.models.quality import DataReview
from forge_core.models.schema_profile import SchemaProfile
from forge_core.profiling.temporal import profile_temporal
from forge_core.runtime_session import open_session
from forge_core.understanding.fingerprint import fingerprint_source
from forge_core.understanding.glossary import business_name_for, description_for, unit_for


def _understanding_role_for(guessed: ColumnRole, fingerprint: str | None, col_name: str) -> UnderstandingRole:
    if guessed == ColumnRole.IDENTIFIER:
        return UnderstandingRole.IDENTIFIER
    if guessed in (ColumnRole.DATE, ColumnRole.DATETIME):
        return UnderstandingRole.TIMESTAMP
    if fingerprint in ("iso_date", "iso_datetime", "epoch"):
        return UnderstandingRole.TIMESTAMP
    if fingerprint in ("aadhaar", "pan", "phone", "uuid"):
        return UnderstandingRole.IDENTIFIER
    if fingerprint == "currency" or guessed == ColumnRole.CURRENCY:
        return UnderstandingRole.MEASURE
    if fingerprint == "percent":
        return UnderstandingRole.MEASURE
    if guessed == ColumnRole.NUMERIC:
        # U4 — be stricter: numeric without currency/percent fingerprint is only a measure
        # if its name hints at a countable/monetary value or it has high cardinality.
        # Columns like "age" (12 distinct, 0-60 range, no amount hint) are dimensions, not measures.
        lower = col_name.lower()
        measure_hints = ("amount", "price", "cost", "revenue", "fee", "salary", "total", "balance", "quantity", "qty", "count", "units", "value", "score", "discount", "tax", "payment", "order_total", "unit_price")
        if any(h in lower for h in measure_hints):
            return UnderstandingRole.MEASURE
        # Fallback: high-cardinality numeric (distinct >20) is likely a measure (e.g. large amounts)
        # Low-cardinality small-range numerics are dimensions
        # We don't have distinct_ratio here, but guessed NUMERIC with cardinality 12 and range 0-100 is likely age/score
        # For now, treat fingerprint-None numeric as MEASURE only if it looks like a continuous value
        # Use a simple heuristic: if the column name suggests age/score/rating, treat as dimension
        if any(h in lower for h in ("age", "year", "month", "day", "rating", "score", "level", "grade")):
            return UnderstandingRole.DIMENSION
        return UnderstandingRole.MEASURE
    if fingerprint in ("enum", "boolean_str"):
        # Small enum often a status
        if "status" in col_name.lower() or "state" in col_name.lower() or "flag" in col_name.lower():
            return UnderstandingRole.STATUS
        if fingerprint == "boolean_str":
            return UnderstandingRole.STATUS
        return UnderstandingRole.DIMENSION
    if guessed == ColumnRole.CATEGORICAL:
        if "status" in col_name.lower():
            return UnderstandingRole.STATUS
        return UnderstandingRole.DIMENSION
    if guessed == ColumnRole.FREE_TEXT:
        if fingerprint == "url":
            return UnderstandingRole.TEXT
        return UnderstandingRole.TEXT
    if guessed in (ColumnRole.EMAIL, ColumnRole.PHONE, ColumnRole.GEOGRAPHIC):
        return UnderstandingRole.DIMENSION
    if guessed == ColumnRole.BOOLEAN_FLAG:
        return UnderstandingRole.STATUS
    return UnderstandingRole.UNKNOWN


def build_data_understanding(
    profile: SchemaProfile,
    data_source: DataSource,
    *,
    data_review: DataReview | None = None,
    domain: DomainAssessment | None = None,
    model_name: str | None = None,
) -> DataUnderstanding:
    structural = profile.structural
    data_map = structural.data_map
    # Index map entries for quick lookup
    map_by_col: dict[str, object] = {}
    if data_map is not None:
        for entity in data_map.entities:
            for col in entity.columns:
                map_by_col[f"{entity.name}.{col.name}"] = col

    grain_by_table = {g.table: g for g in structural.grains}
    rels_by_table: dict[str, list] = {}
    for r in structural.relationships:
        rels_by_table.setdefault(r.from_table, []).append(r)
        rels_by_table.setdefault(r.to_table, []).append(r)

    findings_by_table: dict[str, list[str]] = {}
    if data_review is not None:
        for f in data_review.findings:
            # QualityFinding has .summary, .table, .column
            summary = getattr(f, "summary", None) or getattr(f, "message", None) or str(f)
            tbl = getattr(f, "table", None)
            col = getattr(f, "column", None)
            bucket = tbl or (col.split(".")[0] if col and "." in str(col) else "_global")
            findings_by_table.setdefault(bucket, []).append(summary)

    fingerprint = fingerprint_source(data_source)

    # U2.3 — compute temporal profiles once (needs live connection)
    temporal_by_table: dict[str, dict] = {}
    try:
        con_tmp = open_session(data_source)
        try:
            temporal_by_table = profile_temporal(data_source, structural.columns, con_tmp)
        finally:
            con_tmp.close()
    except Exception:
        temporal_by_table = {}

    tables: list[TableUnderstanding] = []
    columns: list[ColumnUnderstanding] = []
    glossary: dict[str, str] = {}
    open_questions: list[OpenQuestion] = []

    for table in data_source.tables:
        grain = grain_by_table.get(table.name)
        rels = [r for r in structural.relationships if r.from_table == table.name or r.to_table == table.name]

        # U2.3 — temporal from dedicated profiler (falls back to simple min/max)
        temporal: TemporalProfile | None = None
        tp = temporal_by_table.get(table.name)
        if tp:
            temporal = TemporalProfile(span=tp.get("span"), granularity=tp.get("granularity"), gaps=tp.get("gaps") or [])
        else:
            date_cols = [c for c in structural.columns if c.table == table.name and c.guessed_role in (ColumnRole.DATE, ColumnRole.DATETIME)]
            if date_cols:
                dc = date_cols[0]
                span = None
                if dc.min_value is not None or dc.max_value is not None:
                    span = f"{dc.min_value} to {dc.max_value}"
                temporal = TemporalProfile(span=span, granularity=None, gaps=[])

        table_cols: list[ColumnUnderstanding] = []
        for col in structural.columns:
            if col.table != table.name:
                continue
            key = f"{col.table}.{col.name}"
            map_entry = map_by_col.get(key)  # type: ignore[assignment]
            # Extract map fields if present
            fp = getattr(map_entry, "format_fingerprint", None) if map_entry else None
            p25 = getattr(map_entry, "p25", None) if map_entry else None
            p50 = getattr(map_entry, "p50", None) if map_entry else None
            p75 = getattr(map_entry, "p75", None) if map_entry else None
            top_values = getattr(map_entry, "top_values", []) if map_entry else []
            ambiguous = bool(getattr(map_entry, "ambiguous", False)) if map_entry else False

            uro = _understanding_role_for(col.guessed_role, fp, col.name)
            bname = business_name_for(col.name)
            desc = description_for(
                col_name=col.name,
                dtype=col.dtype,
                guessed_role=col.guessed_role,
                fingerprint=fp,
                cardinality=col.cardinality,
                row_count=table.row_count,
                top_values=top_values,
            )
            unit = unit_for(col.name, fp, col.dtype)

            vocab: list[ValueCount] | None = None
            if top_values and (fp == "enum" or (col.guessed_role == ColumnRole.CATEGORICAL and col.cardinality <= 20)):
                vocab = [ValueCount(value=v, count=n) for v, n in top_values]

            sensitivity = "pii" if col.is_likely_pii else "none"
            evidence = [
                Evidence(
                    method="deterministic",
                    description=f"dtype={col.dtype}, null%={col.null_percent}, cardinality={col.cardinality}, role={col.guessed_role.value}, fingerprint={fp}",
                    confidence=0.9 if not ambiguous else 0.5,
                )
            ]
            if top_values:
                evidence.append(
                    Evidence(
                        method="deterministic",
                        description=f"top_values={[v for v,_ in top_values[:3]]}",
                        confidence=0.85,
                    )
                )

            confidence = 0.5 if ambiguous else 0.85
            oq = None
            if ambiguous:
                oq = f"Ambiguous column {key}: role={col.guessed_role.value}, fingerprint={fp}, cardinality={col.cardinality}. Needs human review."
                open_questions.append(OpenQuestion(question=oq, column=key, reason="deterministic signals disagree"))

            cu = ColumnUnderstanding(
                table=col.table,
                name=col.name,
                dtype=col.dtype,
                guessed_role=col.guessed_role,
                format_fingerprint=fp,
                null_pct=col.null_percent,
                cardinality=col.cardinality,
                distinct_ratio=col.distinct_ratio,
                min_value=None if col.min_value is None else str(col.min_value),
                max_value=None if col.max_value is None else str(col.max_value),
                p25=p25,
                p50=p50,
                p75=p75,
                top_values=top_values,
                is_likely_pii=col.is_likely_pii,
                ambiguous=ambiguous,
                understanding_role=uro,
                business_name=bname,
                description=desc,
                unit=unit,
                vocabulary=vocab,
                sensitivity=sensitivity,
                evidence=evidence,
                confidence=confidence,
                open_question=oq,
                physical=col,
                map_entry=map_entry,  # type: ignore[arg-type]
            )
            table_cols.append(cu)
            columns.append(cu)
            glossary[key] = desc

        # Natural description for table
        gdesc = grain.description if grain else "unknown grain"
        nat = f"Table {table.name} has {table.row_count} rows; grain: {gdesc}. {len(table_cols)} columns."
        if temporal and temporal.span:
            nat += f" Temporal coverage: {temporal.span}."

        tq = findings_by_table.get(table.name, []) + findings_by_table.get("_global", [])
        # Deduplicate global findings across tables (only attach to first table or keep per table)
        # For U1, attach global only to first table to avoid repetition
        # Simpler: keep as is, downstream will deduplicate in glossary view

        tu = TableUnderstanding(
            name=table.name,
            row_count=table.row_count,
            grain=grain,
            relationships=rels,
            quality_issues=tq,
            temporal=temporal,
            natural_description=nat,
            columns=table_cols,
        )
        tables.append(tu)

    # U4 — business questions via deterministic heuristics + validation (sqlglot + dry-run)
    from forge_core.understanding.questions import generate_candidate_questions, validate_questions

    raw_bqs = generate_candidate_questions(columns, tables)
    # Validate via sqlglot + DuckDB (only survivors, ranked by support)
    try:
        bqs = validate_questions(raw_bqs, data_source)
    except Exception:
        bqs = raw_bqs
    # Fallback: if validation wiped everything but we had candidates, keep at least 1
    if not bqs and raw_bqs:
        bqs = raw_bqs[:1]

    provenance = Provenance(
        model=model_name,
        generated_at=datetime.now(UTC).isoformat(),
        source_fingerprint=fingerprint,
        token_counts=None,
    )

    return DataUnderstanding(
        source_fingerprint=fingerprint,
        tables=tables,
        columns=columns,
        domain=domain or DomainAssessment(),
        business_questions=bqs,
        open_questions=open_questions,
        glossary=glossary,
        provenance=provenance,
    )
