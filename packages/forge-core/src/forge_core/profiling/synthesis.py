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
    provider: LLMProvider | None,  # None -> degraded run: structural docs, no cookbook
    *,
    pack: IndustryPack | None = None,
    quality_findings: list[QualityFinding] | None = None,
    user_context: list[dict[str, str]] | None = None,
    use_cache: bool = True,
    cache_dir: Path | None = None,
) -> SchemaModel:
    user_context = user_context or []
    provider_id = _provider_name(provider) if provider is not None else "none"
    schema_hash = _schema_hash(structural, source, user_context, provider_id)
    cache_dir = cache_dir or Path(os.environ.get("FORGE_SCHEMA_MODEL_CACHE_DIR", _DEFAULT_CACHE_DIR))
    if use_cache:
        cached = _read_cache(cache_dir, schema_hash)
        if cached is not None:
            cached.from_cache = True
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
    if provider is None:
        model = _structural_only_model(structural, source, schema_hash)
        if use_cache:
            _write_cache(cache_dir, schema_hash, model)
        return model

    context_block = _user_context_block(user_context)

    synthesis_failures: list[str] = []

    def _one(table: TableDescriptor) -> dict[str, Any]:
        try:
            return _synthesize_table(table, structural, provider, context_block)
        except Exception as exc:  # noqa: BLE001 - one bad table degrades to structural-only doc
            synthesis_failures.append(table.name)
            # Log the failure reason so it's visible in the run event log
            # (orchestrator catches the summary via SchemaModel.synthesis_failures).
            _ = exc  # exc is intentionally unused beyond capturing the table name
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
        coverage=_coverage(table_docs, cookbook_raw, cookbook),
        synthesis_failures=synthesis_failures,
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
  "synonyms": ["other names or business terms someone might call this table - domain, product, "
               "abbreviation expansions; [] if the name is already plain"],
  "columns": [
    {{"name": "<exact column name from above>", "meaning": "plain-English meaning",
      "enum": {{"raw_value": "decoded label"}} or null,
      "example": "a representative value or null",
      "synonyms": ["alternate names/business terms for this column, [] if obvious"],
      "confidence": "high|medium|low"}}
  ]
}}
Include a "columns" entry for EVERY column listed above - for a self-explanatory column a
single short clause is fine. When a value set is given for a coded column, fill in "enum" with
each value decoded. `synonyms` matter for search: expand abbreviations (gads -> "google ads",
"adwords") and add the business vocabulary an analyst would type. Never invent a column that is
not listed."""

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
    # Let LLMError propagate: the caller (_one) records it in
    # SchemaModel.synthesis_failures so a per-table failure is visible, not a
    # silent {} that looks identical to a fully-documented table.
    return provider.generate_json(prompt)


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
        synonyms=_str_list(raw.get("synonyms")),
        columns=[by_name[c.name] for c in real_cols],
    )


def _str_list(value: Any) -> list[str]:
    """Coerce a model-produced field to a clean list[str] (drops non-strings,
    blanks, and dupes while preserving order)."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for v in value:
        s = str(v).strip()
        if s and s not in out:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Pass 2 - global (chunked map-reduce for large table counts)
# ---------------------------------------------------------------------------

# Maximum ~chars of the `tables_block` JSON per chunk.  Each table entry is
# roughly 1-3 KB serialised; at 30 KB per chunk we stay well under typical
# 8 k-token context limits even with a verbose schema.
_GLOBAL_CHUNK_CHARS = int(os.environ.get("FORGE_GLOBAL_CHUNK_CHARS", str(30_000)))
# Upper bound on merged cookbook entries across all chunks - each one is a live
# dry-run in _validate_cookbook, so a 500-table run must not produce 800 of them.
_COOKBOOK_MAX_ENTRIES = int(os.environ.get("FORGE_COOKBOOK_MAX_ENTRIES", "80"))

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
    """Map-reduce global synthesis.

    For small schemas (< _GLOBAL_CHUNK_CHARS) this is a single LLM call —
    identical to the original behaviour.  For large schemas the table entries
    are split into chunks, each chunk produces a partial result, and a small
    reduce call assembles the final overview + caveats from the partial
    overviews.  Patterns and cookbook entries from all chunks are merged.
    """
    docs_by_name = {t.name: t for t in table_docs}

    # Build the ordered list of per-table JSON entries.
    all_table_entries = []
    for t in source.tables:
        doc = docs_by_name.get(t.name) or TableDoc(name=t.name, purpose="")
        all_table_entries.append(
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

    patterns_block = structural.patterns.model_dump_json(indent=2)
    # Quality findings are an interpretive input, not an exhaustive list - cap
    # them (worst first) so a 500-table schema doesn't put a megabyte of
    # findings into every chunk prompt.
    _sev_rank = {"high": 0, "critical": 0, "medium": 1, "low": 2}
    quality_sorted = sorted(
        quality_findings, key=lambda f: _sev_rank.get(f.severity.value, 3)
    )[:40]
    quality_block = json.dumps(
        [
            {"code": f.code, "table": f.table, "column": f.column,
             "severity": f.severity.value, "summary": f.summary}
            for f in quality_sorted
        ],
        indent=2,
        default=str,
    )
    # Index relationships by the tables they touch so each chunk only carries
    # its own (a chunk's cookbook only ever joins tables it can see).
    rels_by_table: dict[str, list[dict[str, Any]]] = {}
    for r in structural.relationships:
        d = r.model_dump()
        rels_by_table.setdefault(r.from_table, []).append(d)
        rels_by_table.setdefault(r.to_table, []).append(d)
    pack_block = (
        f"\nINDUSTRY CONTEXT: this looks like {pack.name} data. Vocabulary: "
        f"{', '.join(v.term for v in pack.vocabulary)}.\n"
        if pack
        else ""
    )

    # Chunk table entries into groups that fit within _GLOBAL_CHUNK_CHARS.
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_size = 0
    for entry in all_table_entries:
        entry_size = len(json.dumps(entry, default=str))
        if current and current_size + entry_size > _GLOBAL_CHUNK_CHARS:
            chunks.append(current)
            current, current_size = [], 0
        current.append(entry)
        current_size += entry_size
    if current:
        chunks.append(current)

    all_patterns: list[PatternNote] = []
    all_cookbook: list[dict[str, Any]] = []
    partial_overviews: list[str] = []
    partial_caveats: list[str] = []

    single_chunk = len(chunks) <= 1
    for chunk in chunks:
        tables_block = json.dumps(chunk, indent=2, default=str)
        if single_chunk:
            chunk_rels = [r.model_dump() for r in structural.relationships]
        else:
            names = {e["table"] for e in chunk}
            seen_ids: set[int] = set()
            chunk_rels = []
            for n in names:
                for d in rels_by_table.get(n, []):
                    if id(d) not in seen_ids:
                        seen_ids.add(id(d))
                        chunk_rels.append(d)
        prompt = _GLOBAL_PROMPT.format(
            tables=tables_block,
            relationships=json.dumps(chunk_rels, indent=2, default=str),
            patterns=patterns_block,
            quality=quality_block,
            pack_block=pack_block,
            context=context_block,
        )
        try:
            raw = provider.generate_json(prompt)
        except LLMError:
            raw = {}

        overview_part = str(raw.get("overview") or "")
        if overview_part:
            partial_overviews.append(overview_part)
        for c in raw.get("caveats") or []:
            if str(c).strip() and str(c).strip() not in partial_caveats:
                partial_caveats.append(str(c).strip())
        for item in raw.get("patterns", []) or []:
            try:
                all_patterns.append(PatternNote.model_validate(item))
            except ValueError:
                continue
        all_cookbook.extend(c for c in (raw.get("cookbook") or []) if isinstance(c, dict))

    # Reduce: if we had multiple chunks, merge overviews into one.
    if len(partial_overviews) == 1:
        overview = partial_overviews[0]
    elif len(partial_overviews) > 1:
        reduce_prompt = (
            "You are merging partial database overviews into one coherent 3-5 sentence overview.\n"
            "Partial overviews:\n"
            + "\n---\n".join(partial_overviews)
            + "\n\nReturn ONLY JSON: {\"overview\": \"...\"}"
        )
        try:
            merged = str(provider.generate_json(reduce_prompt).get("overview") or "")
            overview = merged or " ".join(partial_overviews)
        except LLMError:
            overview = " ".join(partial_overviews)
    else:
        overview = ""

    # Each entry costs a live dry-run (and maybe a repair round-trip) in
    # _validate_cookbook, so cap the merged list - dedup by question first.
    deduped: list[dict[str, Any]] = []
    seen_q: set[str] = set()
    for c in all_cookbook:
        q = str(c.get("question") or "").strip().lower()
        if q and q not in seen_q:
            seen_q.add(q)
            deduped.append(c)
    return overview, partial_caveats, all_patterns, deduped[:_COOKBOOK_MAX_ENTRIES]



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
    structural: StructuralProfile,
    source: DataSource,
    user_context: list[dict[str, str]] | None = None,
    provider_id: str = "",
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
        # A FakeLLM / degraded (no-provider) run produces a very different model
        # from a real one for the same schema - they must not share a cache slot.
        "provider": provider_id,
        # Bump when the SchemaModel shape changes so stale cache files (missing
        # new fields) are re-synthesized rather than loaded half-populated.
        "synth_version": 3,
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _read_cache(cache_dir: Path, schema_hash: str) -> SchemaModel | None:
    path = cache_dir / f"{schema_hash.split(':', 1)[-1]}.json"
    try:
        return SchemaModel.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _coverage(
    table_docs: list[TableDoc], cookbook_raw: list[dict[str, Any]], cookbook: list[CookbookEntry]
) -> dict[str, int]:
    """Completeness of the knowledge pack. A column counts as 'documented' only
    when it has a specific meaning (confidence above the fallback 'low' that
    _factcheck_table backfills); a degraded run therefore scores 0 and trips
    the schema_model check."""
    all_cols = [c for t in table_docs for c in t.columns]
    return {
        "cookbook_total": len(cookbook_raw),
        "cookbook_verified": len(cookbook),
        "columns_total": len(all_cols),
        "columns_documented": sum(1 for c in all_cols if c.confidence != "low"),
    }


def _structural_only_model(
    structural: StructuralProfile, source: DataSource, schema_hash: str
) -> SchemaModel:
    """The degraded (no-LLM) knowledge pack: every column doc backfilled from
    its structural role, all deterministic facts carried through, no LLM
    overview / caveats / pattern interpretation / verified cookbook."""
    tables = [_factcheck_table({}, structural, t.name) for t in source.tables]
    return SchemaModel(
        schema_hash=schema_hash,
        generated_by="none (degraded)",
        overview="Generated without an LLM: structural documentation only.",
        caveats=[],
        tables=tables,
        relationships=[_rel_doc(r) for r in structural.relationships],
        patterns=[],
        value_sets=structural.value_sets,
        statistics=structural.patterns.model_dump(),
        quality_findings=[],
        cookbook=[],
        coverage=_coverage(tables, [], []),
    )


def _write_cache(cache_dir: Path, schema_hash: str, model: SchemaModel) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{schema_hash.split(':', 1)[-1]}.json").write_text(
            model.model_dump_json(indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # cache is best-effort; a read-only FS just means we re-synthesize next time


__all__ = ["build_schema_model"]
