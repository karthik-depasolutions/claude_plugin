"""Dispatches a source to the right adapter. Adding a new source kind (e.g.
MySQL) means adding one adapter here — nothing else in the pipeline
changes, because everything downstream only knows `DataSource`.

A source is one of two shapes: a filesystem path (file/directory/SQLite -
dispatched by `Path.exists()` + `IngestionAdapter.supports(Path)`), or a
live-database connection string (dispatched by URL scheme, *before* ever
touching the filesystem — `Path("postgresql://...").exists()` would just be
a slow, confusing way to get `False`, and on Windows `Path(...)` would also
mangle the `//` and `/` in the connection string).

Credential safety: callers that accept a source from a user (the CLI, the
API) must call `prepare_source_for_persistence()` *once*, before doing
anything else with it - never pass a raw connection string to `RunRecord`,
a log line, or a JSON response. `ingest()` transparently resolves the
`${VAR}` placeholder it hands back, so the rest of the pipeline never
needs to know the difference.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from forge_core.ingestion.base import IngestionAdapter, stable_source_id
from forge_core.ingestion.files import FileAdapter
from forge_core.ingestion.postgres import PostgresAdapter, redact
from forge_core.ingestion.sqlite import SqliteAdapter
from forge_core.models.datasource import DataSource

_PATH_ADAPTERS: list[IngestionAdapter] = [SqliteAdapter(), FileAdapter()]
_CONNECTION_STRING_ADAPTERS = [PostgresAdapter()]
_PLACEHOLDER_PATTERN = re.compile(r"^\$\{(\w+)\}$")


def _connection_string_adapter_for(source: str) -> PostgresAdapter | None:
    for adapter in _CONNECTION_STRING_ADAPTERS:
        if adapter.supports(source):
            return adapter
    return None


def prepare_source_for_persistence(raw_source: str) -> str:
    """Call this exactly once, right when a source is first accepted from a
    user (a CLI argument, an API request body) - *before* it's stored on a
    `RunRecord`, logged, or persisted to the jobs database.

    A plain filesystem path is resolved to an absolute path, as before. A
    live-database connection string is instead stashed in this process's
    environment and replaced with a `${VAR}` placeholder: the only thing
    that's safe to log, persist, and echo back over the API is the env var's
    *name*, never the credential itself.
    """
    adapter = _connection_string_adapter_for(raw_source)
    if adapter is not None:
        os.environ[adapter.credential_env_var] = raw_source
        return f"${{{adapter.credential_env_var}}}"
    return str(Path(raw_source).resolve())


def default_run_id(raw_source: str) -> str:
    """A short, filesystem-safe id to default a run's name to - `.stem` for
    a path, or a stable hash of the (redacted) connection string for a live
    database, which has no filename to borrow one from."""
    adapter = _connection_string_adapter_for(raw_source)
    if adapter is not None:
        return f"live-db-{stable_source_id(redact(raw_source))[:10]}"
    return Path(raw_source).stem


def _resolve_placeholder(source: str) -> str:
    match = _PLACEHOLDER_PATTERN.match(source)
    if not match:
        return source
    var_name = match.group(1)
    value = os.environ.get(var_name)
    if not value:
        raise ValueError(f"Source references ${{{var_name}}}, but that environment variable isn't set.")
    return value


def ingest(source: str | Path) -> DataSource:
    source_str = _resolve_placeholder(str(source))

    adapter = _connection_string_adapter_for(source_str)
    if adapter is not None:
        return adapter.ingest(source_str)

    path = Path(source_str)
    if not path.exists():
        raise FileNotFoundError(f"Data source not found: {path}")

    for path_adapter in _PATH_ADAPTERS:
        if path_adapter.supports(path):
            return path_adapter.ingest(path)

    raise ValueError(
        f"No ingestion adapter supports {path}. "
        "Supported: CSV, TSV, Excel, JSON, NDJSON, Parquet, SQLite "
        "(single file, or a directory of files for multi-table sources), "
        "or a postgresql:// connection string."
    )
