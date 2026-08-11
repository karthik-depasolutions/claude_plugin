"""Shared enums and small value types used across every model module."""

from __future__ import annotations

from enum import StrEnum


class SourceKind(StrEnum):
    """Supported ingestion source kinds. Grows over time (see docs/architecture.md)."""

    CSV = "csv"
    TSV = "tsv"
    EXCEL = "excel"
    JSON = "json"
    NDJSON = "ndjson"
    PARQUET = "parquet"
    SQLITE = "sqlite"
    POSTGRES = "postgres"
    MYSQL = "mysql"
    SNOWFLAKE = "snowflake"
    BIGQUERY = "bigquery"


class ColumnRole(StrEnum):
    """Deterministic structural role guess for a column. Independent of any industry pack."""

    IDENTIFIER = "identifier"
    FOREIGN_KEY = "foreign_key"
    DATE = "date"
    DATETIME = "datetime"
    CURRENCY = "currency"
    NUMERIC = "numeric"
    BOOLEAN_FLAG = "boolean_flag"
    CATEGORICAL = "categorical"
    FREE_TEXT = "free_text"
    GEOGRAPHIC = "geographic"
    EMAIL = "email"
    PHONE = "phone"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    WARNING = "warning"
    ERROR = "error"


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStage(StrEnum):
    INGEST = "ingest"
    PROFILE = "profile"
    CLASSIFY = "classify"
    BIND = "bind"
    COMPILE_KPIS = "compile_kpis"
    GENERATE = "generate"
    VALIDATE = "validate"
    PACKAGE = "package"
    PUBLISH = "publish"
