"""U3 — agentic enrichment for DataUnderstanding.

Deterministic builder (U1/U2) settles ~80% of columns. This agent handles the
ambiguous residue: it investigates via the 6-tool surface (inspect/compare/
check_relationship/test_value_set/aggregate/sample_rows) plus terminology
search, then proposes enrichments with evidence. Abstention (no proposal)
is a valid outcome — the column stays as open_question for human review.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from forge_core.agentic.investigation_tools import build_investigation_tools
from forge_core.agentic.tools import build_terminology_search_tool
from forge_core.llm.provider import AgentCallRecorder
from forge_core.models.data_understanding import (
    BusinessQuestion,
    DataUnderstanding,
    Evidence,
    OpenQuestion,
    UnderstandingRole,
)
from forge_core.models.datasource import DataSource
from forge_core.models.schema_profile import StructuralProfile

DEFAULT_AGENT_MODEL = "gemini-2.5-flash"
MAX_AGENT_STEPS = 25

_SYSTEM_PROMPT = """You are enriching a deterministic data-understanding artifact.

You have ALREADY-BUILT deterministic facts: the DataUnderstanding JSON below is ground truth
computed without any LLM. Your job is ONLY to investigate the columns flagged as ambiguous
and propose precise, evidence-backed enrichments.

GROUND TRUTH — DataUnderstanding (deterministic):
{understanding_json}

STRUCTURAL FACTS (for reference):
{structural_json}

Rules:
1. For each ambiguous column listed in open_questions, decide if you can confidently enrich it.
   Use the investigation tools (inspect_column, compare_columns, check_relationship, test_value_set,
   aggregate, sample_rows) to gather evidence BEFORE proposing — never guess from the name alone.
   A numeric column with no currency fingerprint could be revenue or a score; only the distribution proves it.
2. Call submit_column_enrichment ONCE per column you can confidently enrich. Cite SPECIFIC evidence
   (tool output numbers, value distributions) that justifies your proposal — not a restatement of the name.
   Valid understanding_role values: {valid_roles}. Confidence 0.0-1.0 — be honest, low is okay.
   Skip a column entirely rather than guess when tools don't support a confident answer.
3. You may also propose 1-3 business questions this data can answer (questions that actually fit the
   observed tables/columns). Call submit_business_question for each — only if you have evidence the
   data supports it.
4. Call finish exactly once as your last action, after all enrichments.
5. Never invent a table/column name — only those in the DataUnderstanding above.
"""


def _valid_roles() -> str:
    return ", ".join(r.value for r in UnderstandingRole)


def enrich_data_understanding(
    data_understanding: DataUnderstanding,
    structural: StructuralProfile,
    data_source: DataSource,
    *,
    model_name: str | None = None,
    on_stats: Callable[[dict], None] | None = None,
) -> DataUnderstanding:
    """Enrich a deterministic DataUnderstanding via a tool-using agent.

    Never raises — on any failure returns the original understanding unchanged
    (with provenance.model unchanged). On success, merges agent proposals into
    a new DataUnderstanding (original is not mutated).
    """
    if not data_understanding.open_questions:
        return data_understanding

    # Collect valid columns for allowlisting
    valid_cols = {(c.table, c.name) for c in data_understanding.columns}
    # Also track table names for business questions
    valid_tables = {t.name for t in data_understanding.tables}

    enrichments: dict[tuple[str, str], dict[str, Any]] = {}
    extra_questions: list[BusinessQuestion] = []

    def submit_column_enrichment(
        table: str,
        column: str,
        business_name: str,
        description: str,
        understanding_role: str,
        unit: str | None = None,
        confidence: float = 0.5,
        evidence_summary: str = "",
    ) -> str:
        """Propose an enrichment for one ambiguous column. Cite evidence from tool outputs."""
        if (table, column) not in valid_cols:
            return f"ERROR: {table}.{column} is not a real column. Valid: {sorted(f'{t}.{c}' for t,c in valid_cols)[:10]}"
        if understanding_role not in {r.value for r in UnderstandingRole}:
            return f"ERROR: understanding_role must be one of {_valid_roles()}"
        if not business_name.strip() or not description.strip():
            return "ERROR: business_name and description must be non-empty."
        if not (0.0 <= confidence <= 1.0):
            return "ERROR: confidence must be 0.0-1.0"
        key = (table, column)
        if key in enrichments:
            return f"ERROR: already enriched {table}.{column} — one proposal per column."
        enrichments[key] = {
            "business_name": business_name.strip(),
            "description": description.strip(),
            "understanding_role": understanding_role,
            "unit": unit,
            "confidence": confidence,
            "evidence_summary": evidence_summary.strip(),
        }
        return "Recorded."

    def submit_business_question(
        question: str,
        sql_sketch: str | None = None,
        support: float = 0.5,
        tables: list[str] | None = None,
        columns: list[str] | None = None,
    ) -> str:
        """Propose a business question this data can answer (with optional SQL sketch)."""
        if not question.strip():
            return "ERROR: question must be non-empty"
        tbls = tables or []
        cols = columns or []
        for t in tbls:
            if t not in valid_tables:
                return f"ERROR: table {t!r} not in dataset. Valid: {sorted(valid_tables)}"
        for col in cols:
            # col expected as "table.column"
            if "." in col:
                t, c = col.split(".", 1)
                if (t, c) not in valid_cols:
                    return f"ERROR: column {col!r} not real"
            else:
                return f"ERROR: column {col!r} must be 'table.column'"
        if not (0.0 <= support <= 1.0):
            return "ERROR: support 0.0-1.0"
        extra_questions.append(
            BusinessQuestion(
                question=question.strip(),
                sql_sketch=sql_sketch,
                support=support,
                tables=tbls,
                columns=cols,
            )
        )
        return "Recorded."

    def finish() -> str:
        """Call exactly once when done enriching."""
        return "Done."

    recorder = AgentCallRecorder()
    try:
        from langchain.agents import create_agent
        from langchain_core.tools import StructuredTool
        from langchain_google_genai import ChatGoogleGenerativeAI

        # Build investigation tools bound to real schema
        inv_tools = build_investigation_tools(data_source, structural, denied_columns=None, evidence_sink=None)

        tools = [
            *inv_tools,
            build_terminology_search_tool(),
            StructuredTool.from_function(submit_column_enrichment),
            StructuredTool.from_function(submit_business_question),
            StructuredTool.from_function(finish),
        ]

        model = ChatGoogleGenerativeAI(
            model=model_name or os.environ.get("FORGE_LLM_AGENT_MODEL", DEFAULT_AGENT_MODEL),
            google_api_key=os.environ.get("GEMINI_API_KEY"),
            temperature=0.1,
        )

        # Trim understanding JSON to keep prompt budget sane: only ambiguous columns in full detail
        compact_understanding = data_understanding.model_dump(mode="json")
        # Redact physical/map_entry heavy fields for prompt
        for col in compact_understanding.get("columns", []):
            col.pop("physical", None)
            col.pop("map_entry", None)

        system_prompt = _SYSTEM_PROMPT.format(
            understanding_json=json.dumps(compact_understanding, indent=2, default=str)[:12000],
            structural_json=json.dumps(
                [{"table": c.table, "column": c.name, "dtype": c.dtype, "role": c.guessed_role.value} for c in structural.columns],
                indent=2,
            )[:4000],
            valid_roles=_valid_roles(),
        )

        agent = create_agent(model=model, tools=tools, system_prompt=system_prompt)
        agent.invoke(
            {"messages": [{"role": "user", "content": "Enrich the ambiguous columns now. Investigate with tools before proposing."}]},
            config={"recursion_limit": MAX_AGENT_STEPS, "callbacks": [recorder]},
        )
    except Exception:
        if on_stats is not None:
            on_stats(recorder.summary())
        return data_understanding

    if on_stats is not None:
        on_stats(recorder.summary())

    # U4 — validate agent-proposed business questions (sqlglot + dry-run) before merging
    if extra_questions:
        try:
            from forge_core.understanding.questions import validate_questions

            extra_questions = validate_questions(extra_questions, data_source)
        except Exception:
            pass

    if not enrichments and not extra_questions:
        return data_understanding

    # Merge enrichments into a new DataUnderstanding
    # Rebuild columns with enrichments applied
    new_columns = []
    for col in data_understanding.columns:
        key = (col.table, col.name)
        if key in enrichments:
            e = enrichments[key]
            # Map string role to enum
            try:
                uro = UnderstandingRole(e["understanding_role"])
            except ValueError:
                uro = col.understanding_role
            # Build new evidence entry from agent summary
            new_evidence = list(col.evidence)
            if e.get("evidence_summary"):
                new_evidence.append(Evidence(method="llm", description=e["evidence_summary"], confidence=e["confidence"]))
            new_col = col.model_copy(
                update={
                    "business_name": e["business_name"],
                    "description": e["description"],
                    "understanding_role": uro,
                    "unit": e["unit"] if e["unit"] else col.unit,
                    "evidence": new_evidence,
                    "confidence": e["confidence"],
                    "open_question": None,
                    "ambiguous": False,
                }
            )
            new_columns.append(new_col)
        else:
            new_columns.append(col)

    # Rebuild tables.columns to stay consistent
    new_tables = []
    cols_by_table: dict[str, list] = {}
    for c in new_columns:
        cols_by_table.setdefault(c.table, []).append(c)
    for t in data_understanding.tables:
        new_tables.append(t.model_copy(update={"columns": cols_by_table.get(t.name, [])}))

    # Glossary: update enriched entries
    new_glossary = dict(data_understanding.glossary)
    for (tbl, col), e in enrichments.items():
        key = f"{tbl}.{col}"
        new_glossary[key] = e["description"]

    # Open questions: remove those that were enriched
    remaining_open = [q for q in data_understanding.open_questions if (q.column and tuple(q.column.split(".", 1)) not in enrichments)]

    # Business questions: append extras (dedup by question text), then rank by support
    existing_qs = {q.question for q in data_understanding.business_questions}
    merged_bqs = list(data_understanding.business_questions)
    for q in extra_questions:
        if q.question not in existing_qs:
            merged_bqs.append(q)
    merged_bqs.sort(key=lambda x: (-x.support, x.question))
    merged_bqs = merged_bqs[:10]

    # Provenance: mark enriched
    new_prov = data_understanding.provenance.model_copy(
        update={"model": model_name or os.environ.get("FORGE_LLM_AGENT_MODEL", DEFAULT_AGENT_MODEL)}
    )

    return DataUnderstanding(
        source_fingerprint=data_understanding.source_fingerprint,
        tables=new_tables,
        columns=new_columns,
        domain=data_understanding.domain,
        business_questions=merged_bqs,
        open_questions=remaining_open,
        glossary=new_glossary,
        provenance=new_prov,
    )


__all__ = ["enrich_data_understanding"]
