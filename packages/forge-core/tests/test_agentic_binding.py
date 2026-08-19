"""Live test for the LangChain schema-binding agent (`forge_core.agentic`).

Genuinely calls Gemini through `langchain-google-genai` and lets the agent
use real tools (schema inspection, live data preview) - skipped without a
real `GEMINI_API_KEY`, same convention as `test_warehouse.py`'s
`requires_live_postgres`. Not part of the cassette-replay suite: an
agent's tool-calling trace isn't a single deterministic prompt/response
pair, so it isn't cassette-recordable the way the rest of the LLM call
sites are.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from forge_core.agentic import memory, propose_binding_with_agent
from forge_core.classification import load_pack
from forge_core.ingestion.registry import ingest
from forge_core.profiling import build_structural_only

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKS_ROOT = REPO_ROOT / "industry-packs"

load_dotenv(REPO_ROOT / ".env")

requires_live_llm = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY is not set - set it to run this live-agent test",
)


@requires_live_llm
def test_agent_binds_revenue_role_from_data_content_not_column_name(tmp_path: Path):
    # Column names carry zero signal on purpose - forces the agent to use
    # preview_column_values (real data) rather than pattern-matching a name,
    # unlike the deterministic scorer and single-shot LLM proposer this tier
    # only ever runs after both of those have already failed.
    csv_path = tmp_path / "col_a_col_b.csv"
    csv_path.write_text(
        "col_a,col_b\n120.50,alpha\n340.00,bravo\n999.99,charlie\n45.25,delta\n",
        encoding="utf-8",
    )
    data_source = ingest(csv_path)
    structural = build_structural_only(data_source)
    table_cols = structural.columns_for(data_source.tables[0].name)
    fact_table = data_source.table(data_source.tables[0].name)

    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")
    description = pack.canonical_roles["revenue_amount"]

    column = propose_binding_with_agent(
        "revenue_amount", description, table_cols, data_source, fact_table.physical_ref, tenant_id="_local"
    )

    assert column == "col_a", (
        f"expected the agent to pick the numeric monetary-looking column via real data "
        f"inspection, got {column!r}"
    )


@requires_live_llm
def test_agent_returns_none_rather_than_inventing_a_column(tmp_path: Path):
    csv_path = tmp_path / "no_match.csv"
    csv_path.write_text("shoe_size,favorite_color\n9,blue\n10,red\n", encoding="utf-8")
    data_source = ingest(csv_path)
    structural = build_structural_only(data_source)
    table_cols = structural.columns_for(data_source.tables[0].name)
    fact_table = data_source.table(data_source.tables[0].name)

    pack = load_pack(PACKS_ROOT / "healthcare-diagnostics")
    description = pack.canonical_roles["revenue_amount"]

    column = propose_binding_with_agent(
        "revenue_amount", description, table_cols, data_source, fact_table.physical_ref, tenant_id="_local"
    )

    assert column is None


def test_agent_skips_the_llm_entirely_on_an_exact_schema_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # No GEMINI_API_KEY needed and no network access needed: a prior decision
    # on the exact same fact-table shape should short-circuit before any
    # LangChain/Gemini import is even attempted.
    monkeypatch.setenv("FORGE_AGENT_MEMORY_DIR", str(tmp_path / "agent_memory"))
    csv_path = tmp_path / "revenue_region.csv"
    csv_path.write_text("revenue,region\n100,north\n200,south\n", encoding="utf-8")
    data_source = ingest(csv_path)
    structural = build_structural_only(data_source)
    table_cols = structural.columns_for(data_source.tables[0].name)
    fact_table = data_source.table(data_source.tables[0].name)

    fingerprint = memory.schema_fingerprint(table_cols)
    memory.record_decision("some-pack", "revenue_amount", fingerprint, "revenue", 0.9, "cached earlier", "_local")

    column = propose_binding_with_agent(
        "revenue_amount",
        "total revenue",
        table_cols,
        data_source,
        fact_table.physical_ref,
        pack_slug="some-pack",
        tenant_id="_local",
    )

    assert column == "revenue"
