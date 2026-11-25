"""MCP Streamable HTTP helpers for scenario steps.

A scenario MCP step speaks JSON-RPC on one URL: initialize, then
notifications/initialized, then tools/call or another method. The server may
return Mcp-Session-Id; later calls on that URL send it back. Bodies are either
JSON or an SSE stream of JSON-RPC messages.
"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_ACCEPT = "application/json, text/event-stream"
MCP_SESSION_HEADER = "Mcp-Session-Id"
MCP_PROTOCOL_HEADER = "MCP-Protocol-Version"

_MANAGED_HEADERS = {
    "content-type",
    "accept",
    "mcp-session-id",
    "mcp-protocol-version",
}
_MISSING = object()


def parse_sse_messages(body: str) -> list[Any]:
    """Return JSON payloads from SSE `data:` lines."""
    text = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    messages: list[Any] = []
    for block in re.split(r"\n\n+", text):
        data_lines: list[str] = []
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        payload = "\n".join(data_lines).strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            messages.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return messages


def select_stream_message(messages: list[Any], request_id: Any = None) -> Any:
    """Pick the JSON-RPC response for this request, or the last payload."""
    if not messages:
        return None
    dicts = [message for message in messages if isinstance(message, dict)]
    if request_id is not None:
        wanted = str(request_id)
        matched = [message for message in dicts if "id" in message and str(message.get("id")) == wanted]
        replies = [message for message in matched if "result" in message or "error" in message]
        if replies:
            return replies[-1]
        if matched:
            return matched[-1]
    responses = [message for message in dicts if "result" in message or "error" in message]
    if responses:
        return responses[-1]
    return messages[-1]


def parse_response_document(response: requests.Response | None, *, request_id: Any = None) -> Any:
    """Parse a JSON body or an SSE stream into one JSON value."""
    if response is None:
        return None
    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    body = response.text or ""
    stripped = body.strip()
    if not stripped:
        return None
    looks_like_sse = (
        content_type == "text/event-stream"
        or stripped.startswith("data:")
        or stripped.startswith("event:")
    )
    if not looks_like_sse:
        try:
            return response.json()
        except Exception:
            pass
    if looks_like_sse or "\ndata:" in f"\n{stripped}":
        messages = parse_sse_messages(body)
        if messages:
            return select_stream_message(messages, request_id)
    try:
        return json.loads(stripped)
    except Exception:
        return None


def mcp_session_key(url: str, headers: dict[str, Any] | None) -> str:
    """Separate sessions per endpoint and Authorization value."""
    auth = ""
    for key, value in (headers or {}).items():
        if str(key).lower() == "authorization":
            auth = str(value or "")
            break
    return f"{url}\n{auth}"


def mcp_request_headers(
    base: dict[str, Any] | None,
    state: dict[str, Any] | None,
    *,
    include_protocol: bool,
) -> dict[str, str]:
    """Headers for one MCP POST. Session and protocol headers come from state."""
    headers: dict[str, str] = {}
    for key, value in (base or {}).items():
        if str(key).lower() in _MANAGED_HEADERS:
            continue
        headers[str(key)] = "" if value is None else str(value)
    headers["Content-Type"] = "application/json"
    headers["Accept"] = MCP_ACCEPT
    session_id = ""
    protocol = ""
    if isinstance(state, dict):
        session_id = str(state.get("session_id") or "").strip()
        protocol = str(state.get("protocol_version") or "").strip()
    if session_id:
        headers[MCP_SESSION_HEADER] = session_id
    if include_protocol and protocol:
        headers[MCP_PROTOCOL_HEADER] = protocol
    return headers


def _jsonrpc_message(method: str, params: Any, request_id: int) -> dict[str, Any]:
    """A request includes id and params. A notification does not."""
    notify = method.startswith("notifications/")
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if not notify:
        payload["id"] = request_id
    if params is not _MISSING:
        payload["params"] = params
    elif not notify:
        payload["params"] = {}
    return payload


def build_mcp_rpc(spec: Any, body: Any, request_id: int) -> dict[str, Any]:
    """Build a JSON-RPC request from mcp.tool, mcp.method, or a json body."""
    rendered = spec if isinstance(spec, dict) else {}
    tool = rendered.get("tool")
    if isinstance(tool, str) and tool.strip():
        arguments = rendered.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool.strip(), "arguments": arguments},
        }
    method = rendered.get("method")
    if isinstance(method, str) and method.strip():
        params = rendered.get("params") if "params" in rendered else _MISSING
        return _jsonrpc_message(method.strip(), params, request_id)
    if isinstance(body, dict):
        raw_method = body.get("method")
        if isinstance(raw_method, str) and raw_method.strip():
            params = body.get("params") if "params" in body else _MISSING
            return _jsonrpc_message(raw_method.strip(), params, request_id)
    raise ValueError("MCP step needs mcp.tool, mcp.method, or json.method")


def absorb_session_id(state: dict[str, Any], response: requests.Response | None) -> None:
    if response is None:
        return
    value = str(response.headers.get(MCP_SESSION_HEADER) or "").strip()
    if value:
        state["session_id"] = value


def negotiated_protocol(parsed: Any, fallback: str) -> str:
    if isinstance(parsed, dict):
        result = parsed.get("result")
        if isinstance(result, dict):
            version = result.get("protocolVersion")
            if isinstance(version, str) and version.strip():
                return version.strip()
    return fallback


def jsonrpc_error(parsed: Any) -> bool:
    return isinstance(parsed, dict) and "error" in parsed and "result" not in parsed
