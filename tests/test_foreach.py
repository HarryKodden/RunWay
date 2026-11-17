from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from tools.scenario_runner import (
    missing_environment_dependencies,
    preview_step_request,
    render_template,
    resolve_foreach_items,
    run_step,
)
from tools.scenario_schema import ScenarioValidationError, validate_scenario_document


def test_resolve_foreach_items_from_constants_and_literal():
    context = {
        "vars": {},
        "env": {"uris": ["https://a.example", "https://b.example"]},
        "constants": {
            "wallets": [
                {"client_id": "one", "redirect_uri": ["unime://callback"]},
                {"client_id": "two", "redirect_uri": ["https://app.example/cb"]},
            ]
        },
    }
    assert resolve_foreach_items({"foreach": "{{ constants.wallets }}"}, context, {}) == context["constants"]["wallets"]
    assert resolve_foreach_items({"foreach": "{{ env.uris }}"}, context, {}) == [
        "https://a.example",
        "https://b.example",
    ]
    assert resolve_foreach_items({"foreach": ["x", "y"]}, context, {}) == ["x", "y"]
    assert resolve_foreach_items({"foreach": "{{ constants.missing }}"}, context, {}) == []
    assert resolve_foreach_items({"method": "GET"}, context, {}) is None


def test_render_template_item_and_index_bindings():
    context = {
        "vars": {},
        "env": {},
        "item": {"client_id": "abc", "redirect_uri": ["unime://callback"]},
        "index": 2,
    }
    assert render_template("{{ item.client_id }}", context, {}) == "abc"
    assert render_template("{{ item.redirect_uri }}", context, {}) == ["unime://callback"]
    assert render_template("{{ index }}", context, {}) == 2
    assert render_template("n={{ index }}", context, {}) == "n=2"
    assert render_template("{{ item }}", {"vars": {}, "env": {}, "item": "plain"}, {}) == "plain"


def test_foreach_placeholders_are_not_environment_dependencies():
    steps = [
        {
            "method": "POST",
            "path": "/register",
            "foreach": "{{ constants.wallets }}",
            "json": {"client_id": "{{ item.client_id }}", "n": "{{ index }}"},
        }
    ]
    assert missing_environment_dependencies({}, steps) == []


def _allow_public_dns(monkeypatch) -> None:
    import ipaddress

    monkeypatch.setattr(
        "tools.scenario_runner.resolve_host_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )


def test_run_step_foreach_over_list_with_http(monkeypatch):
    calls: list[dict] = []

    def fake_request(self, method, url, **kwargs):  # noqa: ARG001
        calls.append({"method": method, "url": url, "json": kwargs.get("json")})
        response = requests.Response()
        response.status_code = 201
        response.headers["Content-Type"] = "application/json"
        response._content = b'{"ok":true}'
        response.url = url
        return response

    _allow_public_dns(monkeypatch)
    monkeypatch.setattr("requests.Session.request", fake_request)

    context = {
        "vars": {},
        "env": {},
        "constants": {
            "wallets": [
                {"client_id": "one", "redirect_uri": ["a://cb"]},
                {"client_id": "two", "redirect_uri": ["b://cb"]},
            ]
        },
    }
    step = {
        "name": "register",
        "method": "POST",
        "path": "https://as.example.org/register",
        "foreach": "{{ constants.wallets }}",
        "json": {
            "client_id": "{{ item.client_id }}",
            "redirect_uris": "{{ item.redirect_uri }}",
            "index": "{{ index }}",
        },
        "expected_status": [201],
    }
    success, mismatch, _latency, _payload, response = run_step(
        requests.Session(), step, "", context, {}
    )
    assert success and not mismatch
    assert response is not None
    assert len(calls) == 2
    assert calls[0]["json"]["client_id"] == "one"
    assert calls[0]["json"]["redirect_uris"] == ["a://cb"]
    assert calls[0]["json"]["index"] == 0
    assert calls[1]["json"]["client_id"] == "two"
    assert calls[1]["json"]["index"] == 1
    assert "item" not in context
    assert "index" not in context


def test_run_step_foreach_stops_on_failure(monkeypatch):
    calls: list[int] = []

    def fake_request(self, method, url, **kwargs):  # noqa: ARG001
        index = kwargs.get("json", {}).get("index")
        calls.append(index)
        response = requests.Response()
        response.status_code = 500 if index == 0 else 200
        response.headers["Content-Type"] = "application/json"
        response._content = b'{"error":"nope"}' if index == 0 else b'{"ok":true}'
        response.url = url
        return response

    _allow_public_dns(monkeypatch)
    monkeypatch.setattr("requests.Session.request", fake_request)

    context = {"vars": {}, "env": {}, "constants": {"ids": ["a", "b", "c"]}}
    step = {
        "method": "POST",
        "path": "https://as.example.org/register",
        "foreach": "{{ constants.ids }}",
        "json": {"client_id": "{{ item }}", "index": "{{ index }}"},
        "expected_status": 200,
    }
    success, mismatch, _latency, payload, _response = run_step(
        requests.Session(), step, "", context, {}
    )
    assert not success and mismatch
    assert calls == [0]
    assert isinstance(payload, str) and payload.startswith("foreach[0]:")


def test_run_step_foreach_empty_list_is_success():
    session = MagicMock()
    context = {"vars": {}, "env": {}, "constants": {"wallets": []}}
    success, mismatch, latency, payload, response = run_step(
        session,
        {"method": "GET", "path": "https://as.example.org/x", "foreach": "{{ constants.wallets }}"},
        "",
        context,
        {},
    )
    assert success and not mismatch
    assert latency == 0.0
    assert payload is None and response is None
    session.request.assert_not_called()


def test_run_step_foreach_non_list_fails():
    session = MagicMock()
    context = {"vars": {}, "env": {}, "constants": {"wallets": {"not": "a list"}}}
    success, mismatch, _latency, payload, _response = run_step(
        session,
        {"method": "GET", "path": "/", "foreach": "{{ constants.wallets }}"},
        "",
        context,
        {},
    )
    assert not success and mismatch
    assert "foreach must resolve to a list" in str(payload)


def test_preview_step_binds_first_foreach_item():
    preview = preview_step_request(
        steps=[
            {
                "method": "POST",
                "path": "https://as.example.org/register",
                "foreach": "{{ constants.wallets }}",
                "json": {"client_id": "{{ item.client_id }}", "n": "{{ index }}"},
            }
        ],
        step_index=0,
        base_url="",
        context={
            "vars": {},
            "env": {},
            "constants": {"wallets": [{"client_id": "one"}, {"client_id": "two"}]},
        },
        random_generators={},
    )
    assert preview["foreach_count"] == 2
    assert preview["request"]["json"]["client_id"] == "one"
    assert preview["request"]["json"]["n"] == 0
    assert "foreach: previewing item 0 of 2" in preview["curl"]


def test_schema_accepts_foreach_string_or_array():
    validate_scenario_document(
        {
            "steps": [
                {"method": "GET", "path": "/", "foreach": "{{ constants.wallets }}"},
                {"method": "GET", "path": "/", "foreach": ["a", "b"]},
            ]
        }
    )
    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document({"steps": [{"method": "GET", "path": "/", "foreach": {"bad": 1}}]})
    assert any("foreach" in item for item in caught.value.errors)
