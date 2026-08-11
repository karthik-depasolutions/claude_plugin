"""Stage 1 — INGEST."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def ingest_csv(csv_path: Path) -> tuple[pd.DataFrame, dict]:
    df = pd.read_csv(csv_path)
    metadata = {
        "source_file": str(csv_path.resolve()),
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": list(df.columns),
        "file_size_bytes": csv_path.stat().st_size,
    }
    return df, metadata
