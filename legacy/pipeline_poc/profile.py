"""Stage 2 — PROFILE (deterministic + Gemini)."""

from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

from pipeline.gemini_client import generate_json

ROLE_PATTERNS: list[tuple[str, str]] = [
    (r"(^id$|_id$|^booking_id$|^customer_id$|^household_id$)", "identifier"),
    (r"(date|_at$|_on$)", "date"),
    (r"(amount|price|cost|inr|revenue|fee)", "currency"),
    (r"(is_|has_|flag|repeat|paid|status)", "boolean_flag"),
    (r"(name|phone|email|address)", "free_text"),
    (r"(city|category|type|gender|partner|package|status|payment)", "categorical"),
]


def _guess_role(name: str, series: pd.Series) -> str:
    lower = name.lower()
    for pattern, role in ROLE_PATTERNS:
        if re.search(pattern, lower):
            return role
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_bool_dtype(series):
        return "boolean_flag"
    if series.nunique(dropna=True) <= min(20, max(1, len(series) // 2)):
        return "categorical"
    return "free_text"


def build_structural_profile(df: pd.DataFrame, metadata: dict) -> dict:
    columns = []
    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        info: dict[str, Any] = {
            "name": col,
            "dtype": str(series.dtype),
            "null_percent": round(float(series.isna().mean() * 100), 2),
            "cardinality": int(series.nunique(dropna=True)),
            "guessed_role": _guess_role(col, series),
        }
        if len(non_null) and pd.api.types.is_numeric_dtype(series):
            info["min"] = float(non_null.min())
            info["max"] = float(non_null.max())
        elif len(non_null):
            sample_vals = non_null.astype(str).unique()[:5].tolist()
            info["sample_values"] = sample_vals
        columns.append(info)

    return {
        "source": metadata,
        "table_name": "bookings",
        "columns": columns,
    }


def gemini_semantic_profile(structural: dict, sample_rows: list[dict]) -> dict:
    prompt = f"""You are profiling a healthcare diagnostic lab bookings dataset.

Structural profile (computed deterministically):
{json.dumps(structural, indent=2)}

Sample rows (5 only — not the full dataset):
{json.dumps(sample_rows, indent=2, default=str)}

Return JSON with this exact shape:
{{
  "column_semantics": [
    {{
      "column": "column_name",
      "proposed_meaning": "short description",
      "confidence": 0.0 to 1.0
    }}
  ],
  "candidate_insights": [
    {{
      "insight": "actionable pattern or KPI idea",
      "confidence": 0.0 to 1.0,
      "columns": ["col1", "col2"],
      "suggested_kpi_name": "snake_case_name or null"
    }}
  ],
  "data_quality_flags": [
    {{
      "issue": "description",
      "severity": "low|medium|high",
      "columns": ["col1"]
    }}
  ]
}}

Only reference columns from the structural profile. Return 2-3 candidate_insights max.
"""
    return generate_json(prompt)


def build_schema_profile(df: pd.DataFrame, metadata: dict) -> dict:
    structural = build_structural_profile(df, metadata)
    sample_rows = df.head(5).where(pd.notnull(df), None).to_dict(orient="records")
    semantic = gemini_semantic_profile(structural, sample_rows)
    return {
        "structural_profile": structural,
        "gemini_semantic_profile": semantic,
        "sample_rows": sample_rows,
    }


def build_structural_only(df: pd.DataFrame, metadata: dict) -> dict:
    """Structural profile without Gemini — useful when API key is unavailable."""
    structural = build_structural_profile(df, metadata)
    sample_rows = df.head(5).where(pd.notnull(df), None).to_dict(orient="records")
    return {
        "structural_profile": structural,
        "gemini_semantic_profile": None,
        "sample_rows": sample_rows,
        "note": "Gemini semantic profile not run — set GEMINI_API_KEY and re-run pipeline",
    }
