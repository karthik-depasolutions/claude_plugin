"""Deterministic source fingerprint for cache invalidation / drift detection."""

from __future__ import annotations

import hashlib
import json

from forge_core.models.datasource import DataSource


def fingerprint_source(data_source: DataSource) -> str:
    payload = {
        "tables": [
            {
                "name": t.name,
                "row_count": t.row_count,
                "columns": sorted(c.name for c in t.columns),
            }
            for t in sorted(data_source.tables, key=lambda x: x.name)
        ],
        "kind": data_source.kind.value if hasattr(data_source.kind, "value") else str(data_source.kind),
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]
