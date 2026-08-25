from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _pii_protection_on_by_default(monkeypatch):
    """Production defaults FORGE_ENABLE_PII_PROTECTION to false during
    testing (see profiling/structural.py::_is_likely_pii) - that default is
    about a real user's .env-less environment, not this test suite, which
    was written assuming PII detection/denial/redaction is active and
    exercises that real mechanism throughout (test_redaction.py,
    test_packaging.py, ...). Autouse keeps every existing test's behavior
    unchanged; test_pii_protection_defaults_off in
    test_profiling_structural.py explicitly unsets this to prove the real
    production default independently."""
    monkeypatch.setenv("FORGE_ENABLE_PII_PROTECTION", "true")


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
