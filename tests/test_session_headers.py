from __future__ import annotations

import json

import pytest
import requests

from tools.scenario_runner import ensure_session_context, parse_response_document, run_step
from tools.scenario_schema import validate_scenario_document


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


def test_sse_body_is_parsed_as_json():
    body = "\n".join(
        [
            "event: message",
            'data: {"progress": 1}',
            "",
            "data: {\"result\": {\"ok\": true}}",
            "",
        ]
    )
    response = _response(200, body, {"Content-Type": "text/event-stream"}, "https://api.example.org/v1")
    assert parse_response_document(response) == {"result": {"ok": True}}


def test_named_session_and_protocol_headers_are_replayed(monkeypatch):
    calls: list[dict] = []

    def fake_request(self, method, url, **kwargs):  # noqa: ARG001
        headers = dict(kwargs.get("headers") or {})
        calls.append(headers)
        if len(calls) == 1:
            return _response(
                200,
                json.dumps({"ok": True}),
                {
                    "Content-Type": "application/json",
                    "X-Session-Id": "sess-1",
                    "X-Protocol-Version": "2024-11-05",
                },
                url,
            )
        sse = 'data: {"result": {"name": "ada"}}\n\n'
        return _response(200, sse, {"Content-Type": "text/event-stream"}, url)

    _allow_public_dns(monkeypatch)
    monkeypatch.setattr("requests.Session.request", fake_request)
    context = ensure_session_context(
        {"vars": {}, "env": {}},
        session_header_names=["X-Session-Id", "X-Protocol-Version"],
    )
    first = {
        "method": "POST",
        "path": "https://api.example.org/start",
        "json": {"hello": True},
        "expected_status": 200,
    }
    second = {
        "method": "POST",
        "path": "https://api.example.org/next",
        "json": {"again": True},
        "expected_status": 200,
        "save": {"who": "{{ json.result.name }}"},
    }
    assert run_step(requests.Session(), first, "", context, {})[0]
    assert run_step(requests.Session(), second, "", context, {})[0]
    assert "X-Session-Id" not in calls[0]
    assert calls[1]["X-Session-Id"] == "sess-1"
    assert calls[1]["X-Protocol-Version"] == "2024-11-05"
    assert context["vars"]["who"] == "ada"
    assert context["vars"]["X-Session-Id"] == "sess-1"


def test_step_can_skip_stored_session_headers(monkeypatch):
    calls: list[dict] = []

    def fake_request(self, method, url, **kwargs):  # noqa: ARG001
        calls.append(dict(kwargs.get("headers") or {}))
        return _response(
            200,
            "{}",
            {"Content-Type": "application/json", "X-Session-Id": "sess-1"},
            url,
        )

    _allow_public_dns(monkeypatch)
    monkeypatch.setattr("requests.Session.request", fake_request)
    context = ensure_session_context({"vars": {}, "env": {}}, session_header_names=["X-Session-Id"])
    step = {"method": "GET", "path": "https://api.example.org/item", "expected_status": 200}
    assert run_step(requests.Session(), step, "", context, {})[0]
    skipped = {**step, "session_headers": False}
    assert run_step(requests.Session(), skipped, "", context, {})[0]
    assert calls[1].get("X-Session-Id") is None


def test_schema_accepts_a_session_header_name_or_list():
    validate_scenario_document({"session_headers": "X-Session-Id", "steps": []})
    validate_scenario_document(
        {"session_headers": ["X-Session-Id", "X-Protocol-Version"], "steps": [{"session_headers": False}]}
    )
