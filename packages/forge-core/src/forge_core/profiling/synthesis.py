"""Stage 2g - semantic synthesis (mandatory LLM pass).

Turns the deterministic `StructuralProfile` into a `SchemaModel`: the
knowledge pack the generated plugin ships and the MCP client reads. Two
passes:

  1. per table  - purpose, role, grain, per-column meaning + enum decode
  2. global     - overview, caveats, pattern notes, and a cookbook of
                  natural-language question -> vetted SQL

Then two deterministic gates the model output must pass:
  - fact-check : every table/column named must exist in the structural
                 profile; unknown references are dropped
  - cookbook   : every SQL string is executed once against the real data;
                 a failure gets one repair attempt, then the entry is dropped

Results are cached on disk keyed by a hash of the structural facts, so an
unchanged schema is never re-synthesized.
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from forge_core.llm.provider import LLMError, LLMProvider
from forge_core.models.datasource import DataSource, TableDescriptor
from forge_core.models.industry_pack import IndustryPack
from forge_core.models.quality import QualityFinding
from forge_core.models.schema_model import (
    ColumnDoc,
    CookbookEntry,
    PatternNote,
    RelationshipDoc,
    SchemaModel,
    TableDoc,
)
from forge_core.models.schema_profile import RelationshipCandidate, StructuralProfile
from forge_core.profiling.quality import analyze_quality
from forge_core.runtime_session import open_session

SAMPLE_ROWS_PER_TABLE = 12
_DEFAULT_CACHE_DIR = Path("generated/.cache/schema_model")
_VALID_ROLES = {"fact", "dimension", "lookup", "junction", "log", "staging", "unknown"}
_SYNTHESIS_WORKERS = max(1, int(os.environ.get("FORGE_SYNTHESIS_WORKERS", "4")))


def _user_context_block(user_context: list[dict[str, str]]) -> str:
    """The owner's own answers to the pre-synthesis clarification questions."""
    pairs = [
        f"Q: {c.get('question', '').strip()}\nA: {c.get('answer', '').strip()}"
        for c in user_context
        if c.get("answer", "").strip()
    ]
    if not pairs:
        return ""
    return (
        "\nUSER CLARIFICATIONS (the data owner's own answers - treat these as authoritative "
        "over your own guesses):\n" + "\n\n".join(pairs) + "\n"
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def build_schema_model(
    structural: StructuralProfile,
    source: DataSource,
    provider: LLMProvider,
    *,
    pack: IndustryPack | None = None,
    quality_findings: list[QualityFinding] | None = None,
    user_context: list[dict[str, str]] | None = None,
    use_cache: bool = True,
    cache_dir: Path | None = None,
) -> SchemaModel:
    user_context = user_context or []
    schema_hash = _schema_hash(structural, source, user_context)
    cache_dir = cache_dir or Path(os.environ.get("FORGE_SCHEMA_MODEL_CACHE_DIR", _DEFAULT_CACHE_DIR))
    if use_cache:
        cached = _read_cache(cache_dir, schema_hash)
        if cached is not None:
            return cached

    if quality_findings is None:
        con = open_session(source)
        try:
            quality_findings, _ = analyze_quality(source, structural, con)
        finally:
            con.close()

    # The per-table LLM calls are independent - fan them across a small thread
    # pool (Gemini calls are blocking HTTP) so a wide schema isn't N serial
    # round-trips. _factcheck_table stays sequential; it's pure and cheap.
    context_block = _user_context_block(user_context)

    def _one(table: TableDescriptor) -> dict[str, Any]:
        try:
            return _synthesize_table(table, structural, provider, context_block)
        except Exception:  # noqa: BLE001 - one bad table degrades to a backfilled doc, never crashes synthesis
            return {}

    if len(source.tables) > 1 and _SYNTHESIS_WORKERS > 1:
        with ThreadPoolExecutor(max_workers=min(_SYNTHESIS_WORKERS, len(source.tables))) as ex:
            raws = list(ex.map(_one, source.tables))
    else:
        raws = [_one(t) for t in source.tables]
    table_docs = [_factcheck_table(r, structural, t.name) for r, t in zip(raws, source.tables, strict=True)]

    overview, caveats, patterns, cookbook_raw = _synthesize_global(
        table_docs, structural, source, quality_findings, provider, pack=pack, context_block=context_block
    )
    cookbook = _validate_cookbook(cookbook_raw, source, provider)

    model = SchemaModel(
        schema_hash=schema_hash,
        generated_by=_provider_name(provider),
        overview=overview,
        caveats=caveats,
        tables=table_docs,
        relationships=[_rel_doc(r) for r in structural.relationships],
        patterns=patterns,
        value_sets=structural.value_sets,
        statistics=structural.patterns.model_dump(),
        quality_findings=[f.model_dump(mode="json") for f in quality_findings],
        cookbook=cookbook,
    )
    if use_cache:
        _write_cache(cache_dir, schema_hash, model)
    return model


# --------------------------------------------------------------------------- #
# Pass 1 - per table
# --------------------------------------------------------------------------- #
_TABLE_PROMPT = """You are documenting one table of a business database for an analytics assistant.

TABLE: {table}
ROW COUNT: {row_count}
DETECTED GRAIN: {grain}

COLUMNS (deterministic facts - only these columns exist):
{columns}

VALUE SETS (complete distinct values for low-cardinality columns):
{value_sets}

SAMPLE ROWS (real data, at most {n} rows):
{samples}
{context}
Return ONLY JSON with this exact shape:
{{
  "purpose": "one sentence: what one row of this table represents",
  "role": "fact|dimension|lookup|junction|log|staging|unknown",
  "grain_prose": "one row per <X>",
  "columns": [
    {{"name": "<exact column name from above>", "meaning": "plain-English meaning",
      "enum": {{"raw_value": "decoded label"}} or null,
      "example": "a representative value or null",
      "confidence": "high|medium|low"}}
  ]
}}
Include a "columns" entry for EVERY column listed above - for a self-explanatory column a
single short clause is fine. When a value set is given for a coded column, fill in "enum" with
each value decoded. Never invent a column that is not listed."""

_ROLE_MEANING = {
    "identifier": "Unique identifier.",
    "foreign_key": "Reference to another table's key.",
    "date": "A date.",
    "datetime": "A timestamp.",
    "currency": "A monetary amount.",
    "numeric": "A numeric measure.",
    "boolean_flag": "A yes/no flag.",
    "categorical": "A category label.",
    "geographic": "A geographic value (city / region / country).",
    "email": "An email address.",
    "phone": "A phone number.",
    "free_text": "Free-form text.",
}


def _synthesize_table(
    table: TableDescriptor, structural: StructuralProfile, provider: LLMProvider, context: str = ""
) -> dict[str, Any]:
    cols = structural.columns_for(table.name)
    col_lines = "\n".join(
        f'- {c.name} | dtype={c.dtype} | role={c.guessed_role.value} | '
        f'null%={c.null_percent} | distinct={c.cardinality}'
        for c in cols
    )
    value_sets = {
        k.split(".", 1)[1]: v for k, v in structural.value_sets.items() if k.startswith(f"{table.name}.")
    }
    grain = next(
        (g.description for g in structural.grains if g.table == table.name), "unknown"
    )
    prompt = _TABLE_PROMPT.format(
        table=table.name,
        row_count=table.row_count,
        grain=grain,
        columns=col_lines or "(none)",
        value_sets=json.dumps(value_sets, indent=2, default=str) or "{}",
        n=SAMPLE_ROWS_PER_TABLE,
        samples=json.dumps(table.sample_rows[:SAMPLE_ROWS_PER_TABLE], indent=2, default=str),
        context=context,
    )
    try:
        return provider.generate_json(prompt)
    except LLMError:
        return {}


def _factcheck_table(raw: dict[str, Any], structural: StructuralProfile, table_name: str) -> TableDoc:
    real_cols = structural.columns_for(table_name)
    valid = {c.name for c in real_cols}
    by_name: dict[str, ColumnDoc] = {}
    for item in raw.get("columns", []) or []:
        if not isinstance(item, dict) or item.get("name") not in valid or item["name"] in by_name:
            continue
        try:
            by_name[item["name"]] = ColumnDoc.model_validate(item)
        except ValueError:
            continue

    # Every real column gets a doc - backfill the ones the model skipped or
    # botched, so the shipped dictionary is complete rather than ~30% covered.
    for col in real_cols:
        if col.name in by_name:
            continue
        # No pseudo-enum: the full value list ships in SchemaModel.value_sets;
        # a raw->raw map would just be noise dressed as a decode.
        by_name[col.name] = ColumnDoc(
            name=col.name,
            meaning=_ROLE_MEANING.get(col.guessed_role.value, "Column value."),
            example=(col.sample_values[0] if col.sample_values else None),
            confidence="low",
        )

    role = raw.get("role", "unknown")
    return TableDoc(
        name=table_name,
        purpose=str(raw.get("purpose") or f"Records in {table_name}."),
        role=role if role in _VALID_ROLES else "unknown",
        grain_prose=str(raw.get("grain_prose") or ""),
        columns=[by_name[c.name] for c in real_cols],
    )


# --------------------------------------------------------------------------- #
# Pass 2 - global
# --------------------------------------------------------------------------- #
_GLOBAL_PROMPT = """You are writing the orientation pack for a business database an analytics
assistant will query. You are given per-table documentation, the full column list of each table,
detected relationships, raw statistical patterns, and data-quality findings.

TABLES (write SQL against the `query_name` - that is the exact identifier the database exposes;
`columns` is the complete, authoritative list of columns for that table):
{tables}

RELATIONSHIPS (deterministically verified; empty means the tables are not related):
{relationships}

RAW PATTERNS (deterministic facts - numbers only, your job is to interpret them):
{patterns}

DATA-QUALITY FINDINGS (deterministic - each is a real measured issue; turn the ones that would
mislead a query into caveats or "quality"-kind pattern notes):
{quality}
{pack_block}{context}
Return ONLY JSON with this exact shape:
{{
  "overview": "3-5 sentences: what this database is, the core entities, how (or whether) they connect",
  "caveats": ["short imperative warnings a query must respect"],
  "patterns": [
    {{"kind": "temporal|correlation|dependency|redundancy|quality|segment",
      "finding": "what was found", "evidence": "the numbers", "directive": "what to do about it",
      "affects": ["table.column"]}}
  ],
  "cookbook": [
    {{"question": "a natural-language question a user would ask",
      "sql": "a single runnable DuckDB SELECT; FROM/JOIN must use a query_name above and only "
             "columns listed for that table",
      "tables": ["query_name(s) it touches"], "notes": "any caveat that applies"}}
  ]
}}
Write 15-40 cookbook entries: at least one per table, plus the common aggregations, time trends,
and (where a relationship exists) joins. Every SQL string must be a single SELECT. Never invent a
column that is not in that table's `columns` list."""


def _synthesize_global(
    table_docs: list[TableDoc],
    structural: StructuralProfile,
    source: DataSource,
    quality_findings: list[QualityFinding],
    provider: LLMProvider,
    *,
    pack: IndustryPack | None = None,
    context_block: str = "",
) -> tuple[str, list[str], list[PatternNote], list[dict[str, Any]]]:
    docs_by_name = {t.name: t for t in table_docs}
    table_entries = []
    for t in source.tables:
        doc = docs_by_name.get(t.name) or TableDoc(name=t.name, purpose="")
        table_entries.append(
            {
                "table": t.name,
                "query_name": t.physical_ref,
                "purpose": doc.purpose,
                "role": doc.role,
                "grain": doc.grain_prose,
                "columns": [c.name for c in structural.columns_for(t.name)],
                "documented_columns": [c.model_dump() for c in doc.columns],
            }
        )
    tables_block = json.dumps(table_entries, indent=2, default=str)
    rels_block = json.dumps(
        [r.model_dump() for r in structural.relationships], indent=2, default=str
    )
    patterns_block = structural.patterns.model_dump_json(indent=2)
    quality_block = json.dumps(
        [
            {"code": f.code, "table": f.table, "column": f.column,
             "severity": f.severity.value, "summary": f.summary}
            for f in quality_findings
        ],
        indent=2,
        default=str,
    )
    pack_block = (
        f"\nINDUSTRY CONTEXT: this looks like {pack.name} data. Vocabulary: "
        f"{', '.join(v.term for v in pack.vocabulary)}.\n"
        if pack
        else ""
    )
    prompt = _GLOBAL_PROMPT.format(
        tables=tables_block,
        relationships=rels_block,
        patterns=patterns_block,
        quality=quality_block,
        pack_block=pack_block,
        context=context_block,
    )
    try:
        raw = provider.generate_json(prompt)
    except LLMError:
        raw = {}

    overview = str(raw.get("overview") or "")
    caveats = [str(c) for c in (raw.get("caveats") or []) if str(c).strip()]
    patterns: list[PatternNote] = []
    for item in raw.get("patterns", []) or []:
        try:
            patterns.append(PatternNote.model_validate(item))
        except ValueError:
            continue
    cookbook_raw = [c for c in (raw.get("cookbook") or []) if isinstance(c, dict)]
    return overview, caveats, patterns, cookbook_raw


# --------------------------------------------------------------------------- #
# Cookbook validation
# --------------------------------------------------------------------------- #
_REPAIR_PROMPT = """This DuckDB SELECT failed.

SQL:
{sql}

ERROR:
{error}

TABLES (use these exact query names) AND THEIR COLUMNS:
{columns}

Return ONLY JSON: {{"sql": "<a corrected single SELECT using only the names above>"}}.
If it cannot be fixed, return {{"sql": ""}}."""


def _validate_cookbook(
    raw_entries: list[dict[str, Any]], source: DataSource, provider: LLMProvider
) -> list[CookbookEntry]:
    if not raw_entries:
        return []
    columns_ref = json.dumps(
        {t.physical_ref: [c.name for c in t.columns] for t in source.tables}, default=str
    )
    out: list[CookbookEntry] = []
    con = open_session(source)
    try:
        for item in raw_entries:
            sql = str(item.get("sql") or "").strip()
            question = str(item.get("question") or "").strip()
            if not sql or not question:
                continue
            ok, err = _try_sql(con, sql)
            if not ok:
                repaired = _repair_sql(provider, sql, err, columns_ref)
                if repaired:
                    ok, err = _try_sql(con, repaired)
                    if ok:
                        sql = repaired
            if not ok:
                continue
            out.append(
                CookbookEntry(
                    question=question,
                    sql=sql,
                    tables=[str(t) for t in (item.get("tables") or [])],
                    notes=str(item.get("notes") or ""),
                    verified=True,
                )
            )
    finally:
        con.close()
    return out


def _try_sql(con: Any, sql: str) -> tuple[bool, str]:
    """(ran_ok, error_message). A SELECT that executes returns (True, "")."""
    try:
        con.execute(sql).fetchmany(1)
        return True, ""
    except Exception as exc:  # noqa: BLE001 - any DuckDB error means "not runnable", not a crash here
        return False, str(exc)


def _repair_sql(provider: LLMProvider, sql: str, error: str, columns_ref: str) -> str:
    try:
        raw = provider.generate_json(
            _REPAIR_PROMPT.format(sql=sql, error=error or "unknown", columns=columns_ref)
        )
    except LLMError:
        return ""
    return str(raw.get("sql") or "").strip()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _rel_doc(r: RelationshipCandidate) -> RelationshipDoc:
    return RelationshipDoc(
        from_ref=f"{r.from_table}.{r.from_column}",
        to_ref=f"{r.to_table}.{r.to_column}",
        strength=r.strength,
        cardinality="N:1",
    )


def _provider_name(provider: LLMProvider) -> str:
    return getattr(provider, "model", None) or getattr(
        getattr(provider, "_wrapped", None), "model", None
    ) or provider.__class__.__name__


def _schema_hash(
    structural: StructuralProfile, source: DataSource, user_context: list[dict[str, str]] | None = None
) -> str:
    payload = {
        "columns": [
            (c.table, c.name, c.dtype, c.guessed_role.value, c.cardinality, c.null_percent)
            for c in structural.columns
        ],
        "relationships": [
            (r.from_table, r.from_column, r.to_table, r.to_column) for r in structural.relationships
        ],
        "value_sets": structural.value_sets,
        "patterns": structural.patterns.model_dump(),
        "source": source.id,
        "user_context": sorted(
            (c.get("question", ""), c.get("answer", "")) for c in (user_context or [])
        ),
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _read_cache(cache_dir: Path, schema_hash: str) -> SchemaModel | None:
    path = cache_dir / f"{schema_hash.split(':', 1)[-1]}.json"
    try:
        return SchemaModel.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cache(cache_dir: Path, schema_hash: str, model: SchemaModel) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{schema_hash.split(':', 1)[-1]}.json").write_text(
            model.model_dump_json(indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # cache is best-effort; a read-only FS just means we re-synthesize next time


__all__ = ["build_schema_model"]
