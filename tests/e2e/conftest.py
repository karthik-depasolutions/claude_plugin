from __future__ import annotations

from pathlib import Path

import pytest
from forge_core.testing import FakeLLMProvider

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fake_llm() -> FakeLLMProvider:
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
def saas_subscriptions_dir(fixtures_dir: Path) -> Path:
    """Three tables with real FK relationships (subscriptions->accounts,
    usage_events->subscriptions), SaaS vocabulary, no matching industry pack -
    a third structurally distinct shape for the genericity gate."""
    return fixtures_dir / "datasets" / "saas_subscriptions"
