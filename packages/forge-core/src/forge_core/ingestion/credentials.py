"""Per-run source-credential resolution.

A `${VAR}` placeholder in a source string or a `duckdb_attach_sql` statement
(see `forge_core.ingestion.registry` and `forge_core.runtime_session`) is
resolved through `resolve_env`, which checks a **context-scoped overlay**
first and only then falls back to `os.environ`.

Why: the API runs many pipelines concurrently as worker threads in one
process. If each run stashed its own `postgresql://…` credential in
`os.environ["FORGE_SOURCE_DB_URL"]`, two runs against different databases
would clobber each other. `set_credentials({...})` binds a mapping for the
duration of a `with` block; `asyncio.to_thread` copies the context, so a
mapping set in the async request handler is visible on the worker thread
that actually runs the pipeline.

The CLI (one run, one process) sets nothing here and keeps using
`os.environ`, which is exactly the fallback. The shipped MCP runtime
(`mis_mcp_runtime`) never imports this module - a customer's plugin has no
overlay and reads its credential straight from its own environment.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Iterator, Mapping

_OVERLAY: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "forge_source_credentials", default=None
)


@contextlib.contextmanager
def set_credentials(mapping: Mapping[str, str] | None) -> Iterator[None]:
    """Bind `mapping` (env-var name -> value) as the credential overlay for
    the duration of the block. Nested blocks stack; the outer mapping is
    restored on exit.

    `None` is a no-op - the CLI, which uses `os.environ` directly. An empty
    `{}` still binds an (empty, mutable) overlay: a run with no known source
    credential yet still gets isolated resolution, and `remember` writes into
    the overlay rather than leaking to `os.environ`.
    """
    if mapping is None:
        yield
        return
    token = _OVERLAY.set(dict(mapping))
    try:
        yield
    finally:
        _OVERLAY.reset(token)


def resolve_env(name: str) -> str | None:
    """The overlay value for `name`, else `os.environ`, else None."""
    overlay = _OVERLAY.get()
    if overlay is not None and name in overlay:
        return overlay[name]
    return os.environ.get(name)


def remember(name: str, value: str) -> None:
    """Record `name=value` for the rest of *this* run: into the active overlay
    when one is bound (the API - keeps it off `os.environ`, so a concurrent
    run isn't affected), else into `os.environ` (the CLI). Used by adapters
    that discover a credential mid-ingest and need later `open_session` calls
    in the same run to resolve the same `${VAR}` placeholder."""
    overlay = _OVERLAY.get()
    if overlay is not None:
        overlay[name] = value
    else:
        os.environ[name] = value


__all__ = ["remember", "resolve_env", "set_credentials"]
