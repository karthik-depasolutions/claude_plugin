"""Phase 2 - semantic synthesis: the mandatory LLM pass that builds the
SchemaModel, plus its two deterministic gates (fact-check, cookbook dry-run)
and the on-disk cache.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from forge_core.ingestion.registry import ingest
from forge_core.profiling import build_structural_only
from forge_core.profiling.synthesis import build_schema_model
from forge_core.testing import FakeLLMProvider


def _structural_and_source(path: Path):
    ds = ingest(path)
    return build_structural_only(ds), ds


def test_build_schema_model_is_fact_checked(bookings_csv: Path, tmp_path: Path):
    structural, ds = _structural_and_source(bookings_csv)

    model = build_schema_model(structural, ds, FakeLLMProvider(), cache_dir=tmp_path)

    assert model.schema_hash.startswith("sha256:")
    real_tables = {t.name for t in ds.tables}
    real_columns = {c.name for c in structural.columns}
    for table_doc in model.tables:
        assert table_doc.name in real_tables
        for col_doc in table_doc.columns:
            assert col_doc.name in real_columns


def test_every_column_gets_a_doc(bookings_csv: Path, tmp_path: Path):
    structural, ds = _structural_and_source(bookings_csv)

    # A model that documents nothing - backfill must still produce one doc per column.
    class _Empty(FakeLLMProvider):
        def generate_json(self, prompt: str, *, system: str | None = None):
            out = super().generate_json(prompt, system=system)
            if "grain_prose" in prompt.lower() and '"cookbook"' not in prompt.lower():
                out["columns"] = []
            return out

    model = build_schema_model(structural, ds, _Empty(), cache_dir=tmp_path)
    for t in ds.tables:
        real = [c.name for c in structural.columns_for(t.name)]
        doc = model.table(t.name)
        assert [c.name for c in doc.columns] == real  # complete, in schema order
        assert all(c.meaning for c in doc.columns)


def test_raw_statistics_and_quality_findings_are_shipped(bookings_csv: Path, tmp_path: Path):
    structural, ds = _structural_and_source(bookings_csv)

    model = build_schema_model(structural, ds, FakeLLMProvider(), cache_dir=tmp_path)

    assert {"correlations", "temporal", "functional_dependencies", "redundancies",
            "mismatches", "segments"} <= set(model.statistics)
    assert isinstance(model.quality_findings, list)
    if model.quality_findings:
        f = model.quality_findings[0]
        assert {"code", "table", "column", "severity", "summary"} <= set(f)
    assert isinstance(model.value_sets, dict)


def test_hallucinated_columns_are_dropped(bookings_csv: Path, tmp_path: Path):
    structural, ds = _structural_and_source(bookings_csv)

    class _Hallucinating(FakeLLMProvider):
        def generate_json(self, prompt: str, *, system: str | None = None) -> dict[str, Any]:
            out = super().generate_json(prompt, system=system)
            if "grain_prose" in prompt.lower() and '"cookbook"' not in prompt.lower():
                out.setdefault("columns", []).append(
                    {"name": "totally_made_up", "meaning": "nope", "enum": None,
                     "example": None, "confidence": "high"}
                )
            return out

    model = build_schema_model(structural, ds, _Hallucinating(), cache_dir=tmp_path)
    all_col_names = {c.name for t in model.tables for c in t.columns}
    assert "totally_made_up" not in all_col_names


def test_cookbook_entries_must_execute(bookings_csv: Path, tmp_path: Path):
    structural, ds = _structural_and_source(bookings_csv)
    fact = ds.tables[0].physical_ref

    class _WithCookbook(FakeLLMProvider):
        def generate_json(self, prompt: str, *, system: str | None = None) -> dict[str, Any]:
            out = super().generate_json(prompt, system=system)
            if '"cookbook"' in prompt.lower() and '"caveats"' in prompt.lower():
                out["cookbook"] = [
                    {"question": "How many bookings?", "sql": f'SELECT COUNT(*) AS n FROM {fact}',
                     "tables": ["bookings"], "notes": ""},
                    {"question": "broken", "sql": "SELECT * FROM does_not_exist", "tables": [], "notes": ""},
                ]
            return out

    model = build_schema_model(structural, ds, _WithCookbook(), cache_dir=tmp_path)
    assert [e.question for e in model.cookbook] == ["How many bookings?"]
    assert all(e.verified for e in model.cookbook)


def test_schema_model_is_cached_by_schema_hash(bookings_csv: Path, tmp_path: Path):
    structural, ds = _structural_and_source(bookings_csv)

    class _Counting(FakeLLMProvider):
        calls = 0

        def generate_json(self, prompt: str, *, system: str | None = None) -> dict[str, Any]:
            type(self).calls += 1
            return super().generate_json(prompt, system=system)

    provider = _Counting()
    build_schema_model(structural, ds, provider, cache_dir=tmp_path)
    first = _Counting.calls
    assert first > 0
    build_schema_model(structural, ds, provider, cache_dir=tmp_path)
    assert _Counting.calls == first  # second call served entirely from cache
