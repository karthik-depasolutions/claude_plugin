"""U1 — understanding package: builds the DataUnderstanding artifact.

Deterministic only in this phase; agentic enrichment (Phase U3) will
add specialist sessions that mutate this artifact in place.
"""

from forge_core.understanding.builder import build_data_understanding

__all__ = ["build_data_understanding"]
