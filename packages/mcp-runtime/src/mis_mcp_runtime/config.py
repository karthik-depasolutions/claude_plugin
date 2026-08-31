"""Loads the config/*.json files a generated plugin ships and fails closed on
anything malformed or missing — this runtime has no defaults that would let
it silently run against the wrong data or expose a denied column.

This package intentionally does NOT depend on forge_core: it ships and
versions independently (see docs/architecture.md §4.6), so it re-reads the
same plain JSON files forge_core.packaging writes, using its own lightweight
dataclasses rather than importing forge_core's pydantic models.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised for any missing/malformed config — this runtime fails closed."""


@dataclass(frozen=True)
class TableConfig:
    name: str
    physical_ref: str
    columns: list[str]


@dataclass(frozen=True)
class DataSourceConfig:
    kind: str
    duckdb_attach_sql: list[str]
    tables: list[TableConfig]
    read_only: bool = True


@dataclass(frozen=True)
class ColumnBindingConfig:
    role: str
    table_alias: str
    physical: str


@dataclass(frozen=True)
class BindingsConfig:
    allowed_tables: list[str]
    denied_columns: list[str]
    columns: list[ColumnBindingConfig] = field(default_factory=list)


@dataclass(frozen=True)
class CompiledKpiConfig:
    id: str
    label: str
    description: str
    unit: str
    sql: str
    assertions: list[str]
    result_columns: list[str]


@dataclass(frozen=True)
class RuntimeConfig:
    config_dir: Path
    data_dir: Path
    data_source: DataSourceConfig
    bindings: BindingsConfig
    kpis: list[CompiledKpiConfig]
    schema_summary: dict
    schema_model: dict
    max_query_rows: int
    query_timeout_seconds: int


def _resolve_dir(env_var: str, plugin_root_subpath: str, default_relative: str) -> Path:
    if env_var in os.environ:
        return Path(os.environ[env_var]).resolve()
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        return Path(plugin_root, plugin_root_subpath).resolve()
    return Path(default_relative).resolve()


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Required config file missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc


def load_runtime_config(
    config_dir: Path | None = None, data_dir: Path | None = None
) -> RuntimeConfig:
    config_dir = (
        Path(config_dir).resolve() if config_dir else _resolve_dir("MIS_MCP_CONFIG_DIR", "config", "./config")
    )
    data_dir = Path(data_dir).resolve() if data_dir else _resolve_dir("MIS_MCP_DATA_DIR", "data", "./data")

    ds_raw = _read_json(config_dir / "data_source.json")
    bindings_raw = _read_json(config_dir / "schema_bindings.json")
    kpis_raw = _read_json(config_dir / "kpi_defs.json")
    summary_path = config_dir / "schema_summary.json"
    schema_summary = _read_json(summary_path) if summary_path.exists() else {}
    model_path = config_dir / "schema_model.json"
    schema_model = _read_json(model_path) if model_path.exists() else {}

    try:
        tables = [
            TableConfig(name=t["name"], physical_ref=t["physical_ref"], columns=t["columns"])
            for t in ds_raw["tables"]
        ]
        data_source = DataSourceConfig(
            kind=ds_raw["kind"],
            duckdb_attach_sql=ds_raw["connection"]["duckdb_attach_sql"],
            read_only=ds_raw["connection"].get("read_only", True),
            tables=tables,
        )
        if not data_source.read_only:
            raise ConfigError("data_source.json must declare read_only=true; refusing to start.")

        columns = [
            ColumnBindingConfig(role=c["role"], table_alias=c["table_alias"], physical=c["physical"])
            for c in bindings_raw.get("columns", [])
        ]
        bindings = BindingsConfig(
            allowed_tables=bindings_raw["allowed_tables"],
            denied_columns=bindings_raw.get("denied_columns", []),
            columns=columns,
        )
        if not bindings.allowed_tables:
            raise ConfigError("schema_bindings.json has an empty allowed_tables list; refusing to start.")

        kpis = [
            CompiledKpiConfig(
                id=k["id"],
                label=k["label"],
                description=k["description"],
                unit=k["unit"],
                sql=k["sql"],
                assertions=k.get("assertions", []),
                result_columns=k.get("result_columns", []),
            )
            for k in kpis_raw.get("kpis", [])
        ]
    except KeyError as exc:
        raise ConfigError(f"Config file missing required field: {exc}") from exc

    guardrails = schema_summary.get("guardrails", {}) if isinstance(schema_summary, dict) else {}

    return RuntimeConfig(
        config_dir=config_dir,
        data_dir=data_dir,
        data_source=data_source,
        bindings=bindings,
        kpis=kpis,
        schema_summary=schema_summary,
        schema_model=schema_model,
        max_query_rows=int(guardrails.get("max_query_rows", 200)),
        query_timeout_seconds=int(guardrails.get("query_timeout_seconds", 10)),
    )
