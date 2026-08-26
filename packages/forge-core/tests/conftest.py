from __future__ import annotations

import os
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


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_llm: test makes real, billed LLM calls; runs only when GEMINI_API_KEY is set",
    )


@pytest.fixture(autouse=True)
def _no_live_llm_by_default(request, monkeypatch):
    """The agents (context discovery, binding, understanding) build
    ChatGoogleGenerativeAI straight off GEMINI_API_KEY instead of going
    through LLMProvider, so FORGE_LLM_CASSETTE_MODE does not gate them. Since
    the agent path became mandatory, a developer's local .env would otherwise
    turn most of this suite into real, billed, minutes-long network calls.

    Set empty rather than delete: provider lookups call load_dotenv(), which
    leaves a present-but-empty var alone but repopulates a deleted one.
    Tests that genuinely want the network opt in with @pytest.mark.live_llm."""
    if request.node.get_closest_marker("live_llm"):
        if not os.environ.get("GEMINI_API_KEY"):
            pytest.skip("GEMINI_API_KEY is not set - set it to run this live-agent test")
        return
    monkeypatch.setenv("GEMINI_API_KEY", "")


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
