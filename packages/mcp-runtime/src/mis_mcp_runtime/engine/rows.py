"""DataFrame -> JSON-serializable rows, in one place.

Every tool that returns rows goes through `to_json_rows`. It exists because
DuckDB's `.fetchdf()` hands back numpy scalars (`numpy.int64`, `numpy.bool_`)
and `NaN`/`NaT`, none of which `json.dumps` can encode - and an MCP tool that
raises during result serialization fails the whole call, after the query has
already run.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd


def json_safe(value: Any) -> Any:
    """Unwrap a numpy scalar to its Python equivalent and map NaN to None."""
    if hasattr(value, "item") and not isinstance(value, (bytes, str)):
        try:
            return json_safe(value.item())
        except (AttributeError, ValueError):
            pass
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def to_json_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """The one conversion every row-returning tool uses."""
    return [
        {key: json_safe(val) for key, val in row.items()}
        for row in df.where(df.notnull(), None).to_dict(orient="records")
    ]


__all__ = ["json_safe", "to_json_rows"]
