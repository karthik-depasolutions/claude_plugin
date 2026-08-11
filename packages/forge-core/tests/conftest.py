from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


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
