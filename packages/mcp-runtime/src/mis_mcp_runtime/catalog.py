"""Runtime reader for the sharded `config/catalog/` an agent_schema plugin ships.

The generator (`forge_core.packaging.catalog_shard`) writes:

    config/catalog/
      index.json        - table listing + shard manifest + BM25 idf weights
      shard_0000.json    - ~20 full TableDoc entries
      shard_0001.json    - ...

This module is the runtime half: pure-Python BM25 scoring over `index.json`
plus lazy per-shard loading, so a 500-table plugin never loads more than the
index (a few hundred KB) plus the one shard a lookup needs.

It is deliberately standalone - `mis_mcp_runtime` never imports `forge_core`
(see config.py), so the small tokenizer / BM25 kernel is duplicated here
rather than shared. Keep it in sync with `forge_core.packaging.catalog_shard`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_BM25_K1 = 1.5
_BM25_B = 0.75

_SPLIT_RE = re.compile(r"[_\s\-.]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _tokenize(text: str) -> list[str]:
    expanded = _CAMEL_RE.sub(" ", text)
    return [t for t in _SPLIT_RE.split(expanded.lower()) if len(t) > 1]


class Catalog:
    """Lazily-loaded view over one plugin's `config/catalog/` directory."""

    def __init__(self, catalog_dir: Path) -> None:
        self._dir = catalog_dir
        self._index: dict[str, Any] | None = None
        self._shard_cache: dict[int, dict[str, Any]] = {}

    # -- loading ---------------------------------------------------------------
    @property
    def index(self) -> dict[str, Any]:
        if self._index is None:
            try:
                self._index = json.loads((self._dir / "index.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._index = {"tables": [], "shards": [], "bm25_idf": {}}
        return self._index

    def _shard(self, shard_id: int) -> dict[str, Any]:
        if shard_id not in self._shard_cache:
            try:
                self._shard_cache[shard_id] = json.loads(
                    (self._dir / f"shard_{shard_id:04d}.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                self._shard_cache[shard_id] = {"tables": []}
        return self._shard_cache[shard_id]

    # -- queries ------------------------------------------------------------
    def table_count(self) -> int:
        return int(self.index.get("table_count", len(self.index.get("tables", []))))

    def domains(self) -> list[dict[str, Any]]:
        return list(self.index.get("domains", []))

    def list_tables(self) -> list[dict[str, str]]:
        return [
            {
                "name": t.get("name", ""),
                "role": t.get("role", "unknown"),
                "purpose": t.get("purpose", ""),
            }
            for t in self.index.get("tables", [])
        ]

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        """BM25-rank table entries against a natural-language query."""
        terms = _tokenize(query)
        if not terms:
            return []
        idf: dict[str, float] = self.index.get("bm25_idf", {})
        avg_dl: float = float(self.index.get("avg_doc_length", 1.0)) or 1.0
        scored: list[tuple[float, dict[str, Any]]] = []
        for entry in self.index.get("tables", []):
            tf_map: dict[str, int] = entry.get("bm25_tf", {})
            dl = sum(tf_map.values()) or 1
            score = 0.0
            for term in terms:
                w = idf.get(term)
                if w is None:
                    continue
                tf = tf_map.get(term, 0)
                if not tf:
                    continue
                norm = (tf * (_BM25_K1 + 1)) / (
                    tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avg_dl)
                )
                score += w * norm
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for score, entry in scored[:limit]:
            out.append(
                {
                    "table": entry.get("name", ""),
                    "role": entry.get("role", "unknown"),
                    "purpose": entry.get("purpose", ""),
                    "synonyms": entry.get("synonyms", []),
                    "score": round(score, 3),
                }
            )
        return out

    def get_table(self, name: str) -> dict[str, Any] | None:
        """Full TableDoc for one table (loads only its shard)."""
        entry = next((t for t in self.index.get("tables", []) if t.get("name") == name), None)
        if entry is None:
            return None
        shard = self._shard(int(entry.get("shard", 0)))
        return next((t for t in shard.get("tables", []) if t.get("name") == name), None)

    def relationships(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads((self._dir / "relationships.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return raw if isinstance(raw, list) else raw.get("relationships", [])

    def join_path(self, from_table: str, to_table: str, *, max_hops: int = 4) -> list[dict[str, str]]:
        """BFS over the (mostly declared) FK graph. Returns an ordered edge
        list [{from, from_column, to, to_column}], or [] if unreachable."""
        edges: dict[str, list[dict[str, str]]] = {}
        for r in self.relationships():
            a = str(r.get("from_ref", r.get("from_table", ""))).split(".")
            b = str(r.get("to_ref", r.get("to_table", ""))).split(".")
            if len(a) != 2 or len(b) != 2:
                # relationships.json may already be {from_table, from_column, ...}
                ft, fc = r.get("from_table", ""), r.get("from_column", "")
                tt, tc = r.get("to_table", ""), r.get("to_column", "")
            else:
                (ft, fc), (tt, tc) = a, b
            if not (ft and tt):
                continue
            fwd = {"from": ft, "from_column": fc, "to": tt, "to_column": tc}
            rev = {"from": tt, "from_column": tc, "to": ft, "to_column": fc}
            edges.setdefault(ft, []).append(fwd)
            edges.setdefault(tt, []).append(rev)

        queue: list[tuple[str, list[dict[str, str]]]] = [(from_table, [])]
        seen = {from_table}
        while queue:
            node, path = queue.pop(0)
            if node == to_table:
                return path
            if len(path) >= max_hops:
                continue
            for e in edges.get(node, []):
                if e["to"] not in seen:
                    seen.add(e["to"])
                    queue.append((e["to"], [*path, e]))
        return []


def load_catalog(catalog_dir: Path | None) -> Catalog | None:
    if catalog_dir is None or not (catalog_dir / "index.json").exists():
        return None
    return Catalog(catalog_dir)


if __name__ == "__main__":  # tiny self-check
    import tempfile

    d = Path(tempfile.mkdtemp()) / "catalog"
    d.mkdir(parents=True)
    (d / "index.json").write_text(
        json.dumps(
            {
                "table_count": 2,
                "avg_doc_length": 6,
                "bm25_idf": {"order": 1.0, "customer": 1.0, "spend": 1.0},
                "tables": [
                    {"name": "orders", "role": "fact", "purpose": "", "shard": 0,
                     "bm25_tf": {"order": 3, "customer": 1}},
                    {"name": "ad_spend", "role": "fact", "purpose": "", "shard": 0,
                     "bm25_tf": {"spend": 3}},
                ],
            }
        ),
        encoding="utf-8",
    )
    (d / "shard_0000.json").write_text(
        json.dumps({"tables": [{"name": "orders", "purpose": "one row per order"}]}), encoding="utf-8"
    )
    c = Catalog(d)
    assert c.search("customer orders")[0]["table"] == "orders", c.search("customer orders")
    assert c.search("ad spend")[0]["table"] == "ad_spend"
    doc = c.get_table("orders")
    assert doc is not None and doc["purpose"] == "one row per order"
    assert c.get_table("nope") is None
    print("catalog self-check ok")
