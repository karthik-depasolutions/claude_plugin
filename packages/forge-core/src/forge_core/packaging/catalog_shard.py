"""catalog_shard — shard-based schema catalog for large databases.

Replaces the monolithic ``config/schema_model.json`` with N on-disk
shards in ``config/catalog/`` and a lightweight index so the MCP runtime
can:

1. Return a lightweight table-listing from the index without loading
   every shard.
2. Return detailed docs for a subset of tables (``get_table_schema``
   tool) by loading only the relevant shard.
3. Score and rank tables against a natural-language question using
   pure-Python BM25 so the MCP runtime can answer
   ``search_schema(query, top_k)`` entirely offline.

On-disk layout (all written inside the plugin's ``config/`` directory)
-----------------------------------------------------------------------
config/
  catalog/
    index.json          — table listing + shard manifest + BM25 IDF weights
    shard_0000.json     — first  ~20 tables + their full docs
    shard_0001.json     — second ~20 tables …
    …

``index.json`` format
---------------------
{
  "catalog_version": 1,
  "tables": [
    {"name": "orders", "purpose": "…", "role": "fact",
     "shard": 0, "bm25_tf": {"order": 3, "id": 2}},
    …
  ],
  "shards": [
    {"id": 0, "file": "shard_0000.json", "tables": ["orders", …]},
    …
  ],
  "bm25_idf": {"order": 1.23, "id": 0.91, …}
}

BM25 scoring
------------
We use the Robertson–Sparck-Jones IDF with BM25+ term frequency
saturation.  All corpus arithmetic is pure-Python (no third-party
library) to ensure offline operation inside the MCP runtime sandbox.

The corpus is built per-table from:
  table name (weight ×3) + column names (×2) + purpose / grain prose (×1)
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CATALOG_VERSION = 1
DEFAULT_TABLES_PER_SHARD = 20
BM25_K1 = 1.5
BM25_B = 0.75


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[_\s\-\.]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _tokenize(text: str) -> list[str]:
    """Normalise and split to lowercase tokens, handling snake_case & CamelCase."""
    # Expand CamelCase first, then split on punctuation/whitespace.
    expanded = _CAMEL_RE.sub(" ", text)
    raw = _SPLIT_RE.split(expanded.lower())
    return [t for t in raw if len(t) > 1]


def _term_frequencies(tokens: list[str]) -> dict[str, int]:
    tf: dict[str, int] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    return tf


# ---------------------------------------------------------------------------
# Public: write shards
# ---------------------------------------------------------------------------

def write_catalog_shards(
    schema_model: dict[str, Any],
    config_dir: Path,
    *,
    tables_per_shard: int = DEFAULT_TABLES_PER_SHARD,
) -> None:
    """Write the sharded catalog under ``config_dir/catalog/``.

    Parameters
    ----------
    schema_model:
        The full ``SchemaModel.model_dump(mode='json')`` dict.
    config_dir:
        The plugin's ``config/`` directory (must already exist or be
        writable; we create ``catalog/`` inside it).
    tables_per_shard:
        How many tables per shard file.  Tweak via
        ``FORGE_CATALOG_TABLES_PER_SHARD`` env var in the orchestrator.
    """
    catalog_dir = config_dir / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)

    tables_docs: list[dict[str, Any]] = schema_model.get("tables", [])
    if not tables_docs:
        return

    # ---- Build per-table BM25 token frequency vectors -------------------
    table_entries: list[dict[str, Any]] = []
    corpus_tf: list[dict[str, int]] = []
    doc_lengths: list[int] = []

    for tdoc in tables_docs:
        name: str = tdoc.get("name", "")
        purpose: str = tdoc.get("purpose", "") + " " + tdoc.get("grain_prose", "")
        cols: list[dict[str, Any]] = tdoc.get("columns", [])
        col_names: list[str] = [c.get("name", "") for c in cols]
        tbl_synonyms: list[str] = [str(s) for s in (tdoc.get("synonyms") or [])]
        col_synonyms: list[str] = [
            str(s) for c in cols for s in (c.get("synonyms") or [])
        ]
        col_meanings = " ".join(str(c.get("meaning", "")) for c in cols)

        # Weights: name & synonyms dominate (that is what a user types), then
        # column names, then descriptive prose. Synonyms are the single most
        # important signal for abbreviated warehouse names ("v_gads_*").
        tokens: list[str] = (
            _tokenize(name) * 3
            + [t for s in tbl_synonyms for t in _tokenize(s)] * 3
            + [t for cn in col_names for t in _tokenize(cn)] * 2
            + [t for s in col_synonyms for t in _tokenize(s)] * 2
            + _tokenize(purpose)
            + _tokenize(col_meanings)
        )
        tf = _term_frequencies(tokens)
        corpus_tf.append(tf)
        doc_lengths.append(len(tokens))

        table_entries.append({
            "name": name,
            "purpose": purpose.strip(),
            "role": tdoc.get("role", "unknown"),
            "grain_prose": tdoc.get("grain_prose", ""),
            "synonyms": tbl_synonyms,
            "shard": -1,  # filled below
            "bm25_tf": tf,
        })

    # ---- Compute IDF over corpus -----------------------------------------
    N = len(table_entries)
    df: dict[str, int] = {}
    for tf in corpus_tf:
        for term in tf:
            df[term] = df.get(term, 0) + 1

    bm25_idf: dict[str, float] = {
        term: math.log((N - freq + 0.5) / (freq + 0.5) + 1)
        for term, freq in df.items()
    }

    avg_dl = sum(doc_lengths) / N if N else 1.0

    # ---- Split into shards and write each shard -------------------------
    shards_manifest: list[dict[str, Any]] = []
    for shard_id, start in enumerate(range(0, N, tables_per_shard)):
        chunk_docs = tables_docs[start : start + tables_per_shard]
        chunk_names = [d.get("name", "") for d in chunk_docs]
        for idx in range(start, start + len(chunk_docs)):
            table_entries[idx]["shard"] = shard_id

        shard_filename = f"shard_{shard_id:04d}.json"
        shard_data = {
            "catalog_version": CATALOG_VERSION,
            "shard_id": shard_id,
            "tables": chunk_docs,
        }
        (catalog_dir / shard_filename).write_text(
            json.dumps(shard_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        shards_manifest.append({
            "id": shard_id,
            "file": shard_filename,
            "tables": chunk_names,
        })

    # ---- Sidecar files the runtime tools read --------------------------
    relationships: list[dict[str, Any]] = schema_model.get("relationships", []) or []
    (catalog_dir / "relationships.json").write_text(
        json.dumps(relationships, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (catalog_dir / "cookbook.json").write_text(
        json.dumps(schema_model.get("cookbook", []) or [], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    domains = _cluster_domains(tables_docs, relationships)

    # ---- Write index.json -----------------------------------------------
    index: dict[str, Any] = {
        "catalog_version": CATALOG_VERSION,
        "table_count": N,
        "avg_doc_length": round(avg_dl, 2),
        "tables": table_entries,
        "shards": shards_manifest,
        "domains": domains,
        "bm25_idf": {k: round(v, 6) for k, v in bm25_idf.items()},
    }
    (catalog_dir / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _cluster_domains(
    tables_docs: list[dict[str, Any]], relationships: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Group tables into subject areas by connected components of the FK graph.
    Tables the synthesis pass tagged role=staging|log are dropped (plumbing,
    not analysis surface). Each domain is named for its highest-degree table.
    Deterministic - no LLM. Isolated tables each form a singleton domain."""
    roles = {t.get("name", ""): t.get("role", "unknown") for t in tables_docs}
    names = [n for n, r in roles.items() if r not in ("staging", "log") and n]
    idx = {n: i for i, n in enumerate(names)}
    parent = list(range(len(names)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        if a in idx and b in idx:
            ra, rb = find(idx[a]), find(idx[b])
            if ra != rb:
                parent[ra] = rb

    degree: dict[str, int] = dict.fromkeys(names, 0)
    for r in relationships:
        ft = str(r.get("from_ref", r.get("from_table", ""))).split(".")[0]
        tt = str(r.get("to_ref", r.get("to_table", ""))).split(".")[0]
        if ft and tt:
            union(ft, tt)
            if ft in degree:
                degree[ft] += 1
            if tt in degree:
                degree[tt] += 1

    groups: dict[int, list[str]] = {}
    for n in names:
        groups.setdefault(find(idx[n]), []).append(n)

    out: list[dict[str, Any]] = []
    for members in sorted(groups.values(), key=len, reverse=True):
        hub = max(members, key=lambda m: (degree.get(m, 0), m))
        out.append({
            "name": hub,
            "table_count": len(members),
            "tables": sorted(members),
        })
    return out


# ---------------------------------------------------------------------------
# Public: BM25 search (used by the MCP tool at runtime)
# ---------------------------------------------------------------------------

def bm25_search(
    query: str,
    index: dict[str, Any],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Return the top-k table entries from ``index.json`` ranked by BM25.

    Parameters
    ----------
    query:
        Natural-language search string (e.g. "daily active users by country").
    index:
        The parsed ``index.json`` dict.
    top_k:
        Maximum number of results.

    Returns
    -------
    list of table-entry dicts from ``index["tables"]``, best-match first.
    Each entry has ``name``, ``purpose``, ``role``, ``grain_prose``, ``shard``.
    """
    query_terms = _tokenize(query)
    if not query_terms:
        return []

    bm25_idf: dict[str, float] = index.get("bm25_idf", {})
    avg_dl: float = index.get("avg_doc_length", 1.0)
    table_entries: list[dict[str, Any]] = index.get("tables", [])

    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in table_entries:
        tf_map: dict[str, int] = entry.get("bm25_tf", {})
        dl = sum(tf_map.values()) or 1
        score = 0.0
        for term in query_terms:
            if term not in bm25_idf:
                continue
            tf = tf_map.get(term, 0)
            idf = bm25_idf[term]
            # BM25+ formula
            tf_norm = (tf * (BM25_K1 + 1)) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / avg_dl))
            score += idf * (tf_norm + 1)  # +1 is the BM25+ floor
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [entry for _score, entry in scored[:top_k]]


# ---------------------------------------------------------------------------
# Public: load a shard at runtime
# ---------------------------------------------------------------------------

def load_shard(catalog_dir: Path, shard_id: int) -> dict[str, Any]:
    """Load one shard file and return its parsed content."""
    path = catalog_dir / f"shard_{shard_id:04d}.json"
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "CATALOG_VERSION",
    "bm25_search",
    "load_shard",
    "write_catalog_shards",
]
