from __future__ import annotations

from pathlib import Path

import pytest
from forge_core.testing import FakeLLMProvider

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def fake_llm() -> FakeLLMProvider:
    """Deterministic in-process LLM provider - the understanding phase is
    mandatory, so every pipeline test needs one. See forge_core.testing."""
    return FakeLLMProvider()


@pytest.fixture
def fixtures_dir() -> Path:
    return REPO_ROOT / "fixtures"


@pytest.fixture
def bookings_csv(fixtures_dir: Path) -> Path:
    return fixtures_dir / "datasets" / "bookings.csv"


@pytest.fixture
def retail_orders_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "datasets" / "retail_orders"


@pytest.fixture
def edtech_sqlite(fixtures_dir: Path) -> Path:
    return fixtures_dir / "datasets" / "edtech.sqlite"


@pytest.fixture
def dirty_leads_csv(fixtures_dir: Path) -> Path:
    """100 hand-countable rows exercising every profiling.quality check code
    at once - see test_profiling_quality.py."""
    return fixtures_dir / "datasets" / "dirty_leads.csv"
