"""The per-run source-credential overlay (forge_core.ingestion.credentials).

The whole point is that two runs in one process can resolve *different*
values for the same env-var name without touching os.environ or each other.
"""

from __future__ import annotations

import os

from forge_core.ingestion.credentials import remember, resolve_env, set_credentials

VAR = "FORGE_SOURCE_DB_URL"


def test_overlay_wins_over_environ_and_restores():
    os.environ.pop(VAR, None)
    os.environ[VAR] = "postgresql://env/base"
    try:
        assert resolve_env(VAR) == "postgresql://env/base"
        with set_credentials({VAR: "postgresql://run-a/db"}):
            assert resolve_env(VAR) == "postgresql://run-a/db"
        # overlay gone -> back to os.environ, which is untouched
        assert resolve_env(VAR) == "postgresql://env/base"
    finally:
        os.environ.pop(VAR, None)


def test_two_scopes_are_independent():
    seen: dict[str, str | None] = {}
    with set_credentials({VAR: "postgresql://run-a"}):
        seen["a"] = resolve_env(VAR)
    with set_credentials({VAR: "postgresql://run-b"}):
        seen["b"] = resolve_env(VAR)
    assert seen == {"a": "postgresql://run-a", "b": "postgresql://run-b"}
    assert VAR not in os.environ or os.environ.get(VAR) not in ("postgresql://run-a", "postgresql://run-b")


def test_remember_writes_into_the_active_overlay_not_environ():
    os.environ.pop(VAR, None)
    with set_credentials({}):  # an overlay is active but empty
        remember(VAR, "postgresql://discovered/mid-ingest")
        assert resolve_env(VAR) == "postgresql://discovered/mid-ingest"
    assert VAR not in os.environ  # never leaked to the process env


def test_remember_falls_back_to_environ_without_an_overlay():
    os.environ.pop(VAR, None)
    try:
        remember(VAR, "postgresql://cli/style")
        assert os.environ[VAR] == "postgresql://cli/style"
        assert resolve_env(VAR) == "postgresql://cli/style"
    finally:
        os.environ.pop(VAR, None)


def test_empty_or_none_mapping_is_a_noop():
    os.environ.pop(VAR, None)
    with set_credentials(None):
        assert resolve_env(VAR) is None
    with set_credentials({}):
        assert resolve_env(VAR) is None
