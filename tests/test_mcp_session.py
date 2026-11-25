from __future__ import annotations

import json

import pytest
import requests

from tools.mcp_session import parse_response_document, select_stream_message
from tools.scenario_runner import ensure_session_context, preview_step_request, run_step
from tools.scenario_schema import ScenarioValidationError, validate_scenario_document


def _allow_public_dns(monkeypatch) -> None:
    import ipaddress

    monkeypatch.setattr(
        "tools.scenario_runner.resolve_host_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )


def _response(status: int, body: str, headers: dict[str, str], url: str) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.url = url
    response.encoding = "utf-8"
    response._content = body.encode("utf-8")
    for key, value in headers.items():
        response.headers[key] = value
    return response


def test_sse_payload_selects_the_matching_jsonrpc_id():
    body = "\n".join(
        [
            "event: message",
            'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":1}}',
            "",
            "event: message",
            'data: {"jsonrpc":"2.0","id":2,"result":{"ok":true}}',
            "",
            "event: message",
            'data: {"jsonrpc":"2.0","id":9,"result":{"ok":false}}',
            "",
        ]
    )
    response = _response(200, body, {"Content-Type": "text/event-stream"}, "https://mcp.example.org/mcp")
    parsed = parse_response_document(response, request_id=2)
    assert parsed["result"] == {"ok": True}
    messages = [
        {"jsonrpc": "2.0", "method": "notifications/progress"},
        {"jsonrpc": "2.0", "id": 2, "result": {"ok": True}},
    ]
    assert select_stream_message(messages)["id"] == 2


def test_session_header_is_replayed_and_sse_is_json(monkeypatch):
    calls: list[dict] = []

    def fake_request(self, method, url, **kwargs):  # noqa: ARG001
        payload = kwargs.get("json") or {}
        calls.append({"headers": dict(kwargs.get("headers") or {}), "json": payload})
        if payload.get("method") == "initialize":
            return _response(
                200,
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}}),
                {"Content-Type": "application/json", "Mcp-Session-Id": "sess-1"},
                url,
            )
        sse = 'data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"ada"}]}}\n\n'
        return _response(200, sse, {"Content-Type": "text/event-stream"}, url)

    _allow_public_dns(monkeypatch)
    monkeypatch.setattr("requests.Session.request", fake_request)
    context = ensure_session_context({"vars": {}, "env": {}}, session_header_names=["Mcp-Session-Id"])
    base = {
        "path": "https://mcp.example.org/mcp",
        "headers": {"Authorization": "Bearer token", "Accept": "application/json, text/event-stream"},
        "expected_status": 200,
    }
    first = run_step(
        requests.Session(),
        {**base, "name": "initialize", "method": "POST", "json": {"jsonrpc": "2.0", "id": 1, "method": "initialize"}},
        "",
        context,
        {},
    )
    second = run_step(
        requests.Session(),
        {
            **base,
            "name": "whoami",
            "method": "POST",
            "json": {"jsonrpc": "2.0", "id": 2, "method": "tools/call"},
            "save": {"who": "{{ json.result.content.0.text }}"},
        },
        "",
        context,
        {},
    )

    assert first[0] and second[0]
    assert calls[1]["headers"]["Mcp-Session-Id"] == "sess-1"
    assert "Mcp-Session-Id" not in calls[0]["headers"]
    assert context["vars"]["who"] == "ada"


def test_mcp_step_opens_one_session_and_reuses_it(monkeypatch):
    calls: list[dict] = []

    def fake_request(self, method, url, **kwargs):  # noqa: ARG001
        payload = kwargs.get("json") or {}
        headers = dict(kwargs.get("headers") or {})
        calls.append({"method": payload.get("method"), "headers": headers, "json": payload})
        rpc = payload.get("method")
        if rpc == "initialize":
            version = payload["params"]["protocolVersion"]
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"protocolVersion": version, "serverInfo": {"name": "demo", "version": "0"}},
                }
            )
            return _response(
                200,
                body,
                {"Content-Type": "application/json", "Mcp-Session-Id": "sess-1"},
                url,
            )
        if rpc == "notifications/initialized":
            return _response(202, "", {}, url)
        if rpc == "tools/call":
            sse = (
                "event: message\n"
                f'data: {{"jsonrpc":"2.0","id":{payload["id"]},"result":{{"content":[{{"type":"text","text":"ada"}}]}}}}\n\n'
            )
            return _response(200, sse, {"Content-Type": "text/event-stream"}, url)
        body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {"tools": [{"name": "whoami"}]}})
        return _response(200, body, {"Content-Type": "application/json"}, url)

    _allow_public_dns(monkeypatch)
    monkeypatch.setattr("requests.Session.request", fake_request)
    context = {"vars": {"query": "hi"}, "env": {}}
    http = requests.Session()
    whoami = {
        "name": "whoami",
        "method": "MCP",
        "path": "https://mcp.example.org/mcp",
        "headers": {"Authorization": "Bearer token"},
        "mcp": {
            "protocol_version": "2024-11-05",
            "tool": "whoami",
            "arguments": {"q": "{{ vars.query }}"},
        },
        "save": {"who": "{{ json.result.content.0.text }}", "sid": "{{ headers.Mcp-Session-Id }}"},
    }
    listed = {
        "name": "tools",
        "method": "MCP",
        "path": "https://mcp.example.org/mcp",
        "headers": {"Authorization": "Bearer token"},
        "mcp": {"method": "tools/list"},
    }
    first = run_step(http, whoami, "", context, {})
    second = run_step(http, listed, "", context, {})

    assert first[0] and second[0]
    assert [item["method"] for item in calls] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
        "tools/list",
    ]
    assert "Mcp-Session-Id" not in calls[0]["headers"]
    assert calls[1]["headers"]["Mcp-Session-Id"] == "sess-1"
    assert calls[2]["headers"]["Mcp-Session-Id"] == "sess-1"
    assert calls[3]["headers"]["Mcp-Session-Id"] == "sess-1"
    assert calls[1]["headers"]["MCP-Protocol-Version"] == "2024-11-05"
    assert "id" not in calls[1]["json"]
    assert calls[2]["json"]["params"] == {"name": "whoami", "arguments": {"q": "hi"}}
    assert calls[3]["json"]["params"] == {}
    assert calls[3]["json"]["method"] == "tools/list"
    assert calls[2]["headers"]["Accept"] == "application/json, text/event-stream"
    assert context["vars"]["who"] == "ada"
    assert context["vars"]["sid"] == "sess-1"
    assert second[3]["result"]["tools"][0]["name"] == "whoami"


def test_mcp_jsonrpc_error_fails_unless_asserted(monkeypatch):
    def fake_request(self, method, url, **kwargs):  # noqa: ARG001
        payload = kwargs.get("json") or {}
        rpc = payload.get("method")
        if rpc == "initialize":
            body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2025-03-26"}})
            return _response(200, body, {"Content-Type": "application/json", "Mcp-Session-Id": "sess-1"}, url)
        if rpc == "notifications/initialized":
            return _response(202, "", {}, url)
        body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "error": {"code": -32602, "message": "bad"}})
        return _response(200, body, {"Content-Type": "application/json"}, url)

    _allow_public_dns(monkeypatch)
    monkeypatch.setattr("requests.Session.request", fake_request)
    step = {
        "method": "MCP",
        "path": "https://mcp.example.org/mcp",
        "mcp": {"tool": "whoami"},
    }
    failed = run_step(requests.Session(), step, "", {"vars": {}, "env": {}}, {})
    assert failed[0] is False
    asserted = {**step, "expected_json_contains": {"error": {"code": -32602}}}
    passed = run_step(requests.Session(), asserted, "", {"vars": {}, "env": {}}, {})
    assert passed[0] is True


def test_mcp_404_starts_a_new_session_once(monkeypatch):
    calls: list[str] = []
    tool_calls = {"n": 0}

    def fake_request(self, method, url, **kwargs):  # noqa: ARG001
        payload = kwargs.get("json") or {}
        rpc = str(payload.get("method") or "")
        calls.append(rpc)
        headers = kwargs.get("headers") or {}
        if rpc == "initialize":
            session_id = "sess-b" if calls.count("initialize") > 1 else "sess-a"
            body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2025-03-26"}})
            return _response(200, body, {"Content-Type": "application/json", "Mcp-Session-Id": session_id}, url)
        if rpc == "notifications/initialized":
            return _response(202, "", {}, url)
        tool_calls["n"] += 1
        if tool_calls["n"] == 1:
            assert headers.get("Mcp-Session-Id") == "sess-a"
            return _response(404, "", {}, url)
        assert headers.get("Mcp-Session-Id") == "sess-b"
        body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {"ok": True}})
        return _response(200, body, {"Content-Type": "application/json"}, url)

    _allow_public_dns(monkeypatch)
    monkeypatch.setattr("requests.Session.request", fake_request)
    result = run_step(
        requests.Session(),
        {"method": "MCP", "path": "https://mcp.example.org/mcp", "mcp": {"tool": "whoami"}},
        "",
        {"vars": {}, "env": {}},
        {},
    )
    assert result[0] is True
    assert calls == [
        "initialize",
        "notifications/initialized",
        "tools/call",
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]


def test_mcp_preview_describes_the_tool_call():
    preview = preview_step_request(
        steps=[{
            "method": "MCP",
            "path": "https://mcp.example.org/mcp",
            "mcp": {"tool": "whoami", "arguments": {}},
        }],
        step_index=0,
        base_url="",
        context={"vars": {}, "env": {}},
        random_generators={},
    )
    assert "initialize" in preview["curl"]
    assert "tools/call" in preview["curl"]
    assert "whoami" in preview["curl"]
    assert preview["request"]["method"] == "POST"


def test_mcp_schema_accepts_a_tool_call_and_rejects_a_typo():
    validate_scenario_document(
        {
            "steps": [
                {
                    "method": "MCP",
                    "path": "/mcp",
                    "mcp": {"tool": "whoami", "arguments": {}, "protocol_version": "2024-11-05"},
                }
            ]
        }
    )
    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document({"steps": [{"method": "MCP", "mcp": {"tool": "whoami", "nope": 1}}]})
    assert any("nope" in item for item in caught.value.errors)
    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document({"steps": [{"method": "MCP", "path": "/mcp"}]})
    assert any("mcp.tool" in item for item in caught.value.errors)
