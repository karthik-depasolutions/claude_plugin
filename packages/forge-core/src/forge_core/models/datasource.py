"""Unified representation of an ingested customer data source.

Every ingestion adapter (files.py, sqlite.py, and future db adapters) must
produce one of these regardless of the underlying source kind. Everything
downstream — profiling, binding, compilation — depends only on this contract,
never on the original file format or connection details.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from forge_core.models.common import SourceKind


class ConnectionContract(BaseModel):
    """How the runtime should (re)connect to this data at query time.

    For files, `duckdb_attach_sql` registers the file(s) as DuckDB views. For
    live databases, `read_only` must be true and `duckdb_attach_sql` uses
    DuckDB's scanner/attach extensions. The generator never stores credentials;
    `credential_env_vars` names the environment variables the runtime resolves
    at connection time.
    """

    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    duckdb_attach_sql: list[str] = Field(
        default_factory=list,
        description="DuckDB statements (CREATE VIEW / ATTACH) that make every table queryable.",
    )
    read_only: bool = True
    credential_env_vars: list[str] = Field(default_factory=list)
    original_paths: list[str] = Field(
        default_factory=list, description="Absolute source paths, for provenance only."
    )


class RawColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    raw_dtype: str
    ordinal: int


class TableDescriptor(BaseModel):
    """One table (or one flat file treated as a table)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Logical table name, unique within the DataSource.")
    physical_ref: str = Field(description="What DuckDB should query, e.g. a view name or path.")
    row_count: int
    columns: list[RawColumn]
    sample_rows: list[dict[str, Any]] = Field(
        default_factory=list, description="A small, capped sample for profiling only."
    )
    source_path: str | None = None


class DataSource(BaseModel):
    """Output of Stage 1 — INGEST. Fully self-describing, source-format-agnostic."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Stable id for this ingestion run, e.g. a content hash.")
    kind: SourceKind
    tables: list[TableDescriptor]
    connection: ConnectionContract
    total_row_count: int
    ingested_at: str = Field(description="ISO-8601 timestamp.")

    def table(self, name: str) -> TableDescriptor:
        for t in self.tables:
            if t.name == name:
                return t
        raise KeyError(f"No table named {name!r} in DataSource {self.id!r}")
