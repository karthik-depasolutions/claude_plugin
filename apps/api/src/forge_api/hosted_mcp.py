"""Host the generic mis-mcp-runtime over Streamable HTTP so Claude Desktop
can auto-connect from a GitHub-installed plugin's `.mcp.json` (http URL).
Stdio `python run_server.py` is not used on this path."""

from __future__ import annotations

import secrets
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from forge_core.packaging.plugin_builder import mcp_token_path
from mis_mcp_runtime.config import RuntimeConfig, load_runtime_config
from mis_mcp_runtime.engine.duckdb_session import open_session
from mis_mcp_runtime.server import create_server
from starlette.types import Receive, Scope, Send

from forge_api.config import get_settings

_current_run_id: ContextVar[str | None] = ContextVar("hosted_mcp_run_id", default=None)
_sessions: dict[str, tuple[RuntimeConfig, Any]] = {}


def _output_dir(run_id: str) -> Path:
    base = get_settings().runs_dir / run_id
    nested = base / "output"
    return nested if nested.is_dir() else base


def _plugin_dirs(run_id: str) -> tuple[Path, Path]:
    output = _output_dir(run_id)
    nested = list(output.glob("*/config/data_source.json"))
    if nested:
        plugin = nested[0].parent.parent
        return plugin / "config", plugin / "data"
    if (output / "config" / "data_source.json").is_file():
        return output / "config", output / "data"
    raise FileNotFoundError(f"no packaged plugin config for run {run_id}")


def _token_ok(run_id: str, token: str) -> bool:
    path = mcp_token_path(_output_dir(run_id))
    if not path.is_file() or not token:
        return False
    expected = path.read_text(encoding="utf-8").strip()
    return secrets.compare_digest(expected, token)


def _get_state() -> tuple[RuntimeConfig, Any]:
    run_id = _current_run_id.get()
    if not run_id:
        raise RuntimeError("hosted MCP request is missing a run id")
    cached = _sessions.get(run_id)
    if cached is not None:
        return cached
    config_dir, data_dir = _plugin_dirs(run_id)
    config = load_runtime_config(config_dir, data_dir)
    con = open_session(config.data_source, config.data_dir)
    _sessions[run_id] = (config, con)
    return config, con


def build_mcp_starlette():  # noqa: ANN201 - Starlette from the MCP SDK
    server = create_server(get_state=_get_state)
    return server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        host="0.0.0.0",
    )


class HostedMCPGateway:
    """ASGI app mounted at `/mcp`. Path is `/{run_id}/{token}`."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        parts = [p for p in scope.get("path", "").split("/") if p]
        if parts[:1] == ["mcp"]:
            parts = parts[1:]
        if len(parts) < 2:
            await _plain(send, 404, b"not found")
            return
        run_id, token = parts[0], parts[1]
        if not _token_ok(run_id, token):
            await _plain(send, 401, b"unauthorized")
            return
        reset = _current_run_id.set(run_id)
        try:
            child = dict(scope)
            child["path"] = "/"
            child["raw_path"] = b"/"
            await self.inner(child, receive, send)
        finally:
            _current_run_id.reset(reset)


async def _plain(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": body})
