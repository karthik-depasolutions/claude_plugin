"""Wraps a live MCP `ClientSession`'s tools as LangChain-compatible tools, so
a real chat model can drive the exact same MCP surface a generated plugin
ships to Claude Desktop - the plugin's own tool names, descriptions, and
input schemas, not a hand-maintained mirror of them that could drift.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from mcp.client.session import ClientSession
from pydantic import BaseModel, Field, create_model

_JSON_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _args_model(tool_name: str, schema: dict) -> type[BaseModel]:
    """Builds a throwaway Pydantic model from an MCP tool's JSON input
    schema, so LangChain can validate/structure the model's tool calls the
    same way it would for a natively-defined tool."""
    properties = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    fields: dict[str, Any] = {}
    for name, prop in properties.items():
        py_type = _JSON_TYPE_MAP.get(prop.get("type"), str)
        description = prop.get("description", "")
        if name in required:
            fields[name] = (py_type, Field(..., description=description))
        else:
            fields[name] = (py_type | None, Field(prop.get("default"), description=description))
    return create_model(f"{tool_name}_Args", **fields)  # type: ignore[call-overload]


def _result_text(result: Any) -> str:
    parts = [getattr(block, "text", "") for block in (getattr(result, "content", None) or [])]
    text = "\n".join(p for p in parts if p)
    if getattr(result, "is_error", False):
        return f"ERROR: {text or 'tool call failed'}"
    return text or "(empty result)"


def mcp_tools_as_langchain(session: ClientSession, mcp_tools: list) -> list[StructuredTool]:
    """`mcp_tools` is `(await session.list_tools()).tools`. Each MCP tool
    becomes an async-only StructuredTool whose call is a real
    `session.call_tool` - so a tool-selection or argument mistake by the
    model surfaces exactly as it would in a real Claude Desktop session."""
    lc_tools = []
    for tool in mcp_tools:
        args_model = _args_model(tool.name, tool.input_schema or {})

        def _make_coroutine(tool_name: str):
            async def _call(**kwargs: Any) -> str:
                result = await session.call_tool(
                    tool_name, {k: v for k, v in kwargs.items() if v is not None}
                )
                return _result_text(result)

            return _call

        lc_tools.append(
            StructuredTool.from_function(
                name=tool.name,
                description=tool.description or tool.name,
                args_schema=args_model,
                coroutine=_make_coroutine(tool.name),
            )
        )
    return lc_tools


__all__ = ["mcp_tools_as_langchain"]
