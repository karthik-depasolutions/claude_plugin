"""The MCP tool surface's discipline layer.

Every tool `server.py` exposes is wrapped by `tool_guard` so it (1) never
raises into the session, (2) never returns a connection string, a
filesystem path or an internal identifier in an error message, and (3)
emits one structured audit line per call to stderr - the MCP host's log.

That is the whole trust boundary for this runtime. There is no network
listener, no auth token and no multi-tenant state: the server speaks stdio
to one local client, every tool is read-only, and the SELECT-only guarantee
is enforced structurally in `security/sql_policy.py`. So the discipline that
applies here is error hygiene and observability, not access control - see
SECURITY.md for what is deliberately left out and why.

ponytail: plain stderr audit line, no hash chain. Add one only if these
plugins ever run somewhere the log itself is untrusted.
"""

from __future__ import annotations

import functools
import re
import sys
import time
from collections.abc import Callable
from typing import Any

import duckdb

from mis_mcp_runtime.security.allowlist import AllowlistError
from mis_mcp_runtime.security.limits import QueryTimeoutError
from mis_mcp_runtime.security.pii_policy import PiiPolicyError
from mis_mcp_runtime.security.sql_policy import SqlPolicyError


class ToolError(Exception):
    """Raised by a tool body to return a typed, client-safe error.

    `code` should be one of the stable codes below; `message` is shown to the
    client verbatim only for the "expected" codes, and replaced with a
    generic string otherwise.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Exception type -> stable error code, checked in order. Anything not listed
# is `internal_error` and its message is dropped entirely, because an
# unexpected exception is the case most likely to carry a DSN or a path.
_CODE_BY_TYPE: tuple[tuple[type[BaseException], str], ...] = (
    (SqlPolicyError, "denied"),
    (AllowlistError, "denied"),
    (PiiPolicyError, "denied"),
    (QueryTimeoutError, "timeout"),
    (KeyError, "not_found"),
    (LookupError, "not_found"),
    # A DuckDB execution error (unknown column, type mismatch, ...) is the
    # caller's SQL being wrong, not the server breaking - keep the (redacted)
    # message so the model can correct it. Checked before ValueError because
    # duckdb.Error subclasses it.
    (duckdb.Error, "query_failed"),
    (ValueError, "invalid_argument"),
)

# Codes whose message is safe to pass through (after redaction).
_SAFE_CODES = frozenset({"denied", "timeout", "not_found", "invalid_argument", "query_failed"})

_GENERIC_MESSAGE = "The tool could not complete the request."

# Crude but sufficient: a URI (with or without credentials), a Windows or
# POSIX absolute path, or the hashed physical table/view names the generator
# emits (e.g. client_sparda_leads_71fa35b970ae).
_REDACT_PATTERNS = (
    re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s\"']+", re.IGNORECASE),
    re.compile(r"(?:[A-Za-z]:\\|/)[^\s\"']{2,}"),
    re.compile(r"\b\w+_[0-9a-f]{12}\b"),
)

_MAX_MESSAGE_LEN = 500


def redact(text: str) -> str:
    """Strip anything that looks like a secret, a path or an internal id."""
    out = text
    for pattern in _REDACT_PATTERNS:
        out = pattern.sub("[redacted]", out)
    return out[:_MAX_MESSAGE_LEN]


def classify(exc: BaseException) -> tuple[str, str]:
    """Map an exception to (code, client-safe message)."""
    if isinstance(exc, ToolError):
        message = exc.message if exc.code in _SAFE_CODES else _GENERIC_MESSAGE
        return exc.code, redact(message)
    for exc_type, code in _CODE_BY_TYPE:
        if isinstance(exc, exc_type):
            return code, redact(str(exc)) or code
    return "internal_error", _GENERIC_MESSAGE


def _audit(tool: str, *, ok: bool, ms: int, code: str | None = None, rows: int | None = None) -> None:
    parts = [f"mcp.tool tool={tool}", f"ok={str(ok).lower()}", f"ms={ms}"]
    if rows is not None:
        parts.append(f"rows={rows}")
    if code:
        parts.append(f"code={code}")
    # stderr is the only log channel a stdio MCP server has.
    print(" ".join(parts), file=sys.stderr)  # noqa: T201


def tool_guard(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a server tool closure: forward the call, but turn any exception -
    and any ``{"error": <str>}`` the underlying tool returns itself - into a
    uniform ``{"error": {"code", "message"}}``, redact it, and log the call.

    Uses ``functools.wraps`` so the MCP server still introspects the wrapped
    function's real signature for its input schema.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - stopping every exception here is the point
            code, message = classify(exc)
            _audit(fn.__name__, ok=False, ms=_ms(started), code=code)
            return {"error": {"code": code, "message": message}}

        ms = _ms(started)
        if isinstance(result, dict) and isinstance(result.get("error"), str):
            _audit(fn.__name__, ok=False, ms=ms, code="query_failed")
            return {"error": {"code": "query_failed", "message": redact(result["error"])}}

        rows = result.get("row_count") if isinstance(result, dict) else None
        _audit(fn.__name__, ok=True, ms=ms, rows=rows)
        return result

    return wrapper


def _ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)
