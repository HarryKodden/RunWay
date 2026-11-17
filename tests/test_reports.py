from __future__ import annotations

import ipaddress

import pytest
import requests

from tools.scenario_runner import apply_report, apply_save, run_step
from tools.scenario_schema import ScenarioValidationError, validate_scenario_document


def _allow_public_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        "tools.scenario_runner.resolve_host_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )


def test_apply_report_true_uses_save_response_as_name():
    context: dict = {"vars": {}, "reports": []}
    response = requests.Response()
    response.status_code = 201
    response._content = b'{"client_id":"abc"}'
    response.url = "https://as.example.org/register"
    payload = {"client_id": "abc"}
    step = {"name": "register", "save_response_as": "registration", "report": True}
    apply_save(step, payload, response, context, final_url=response.url, body_text=response.text)
    apply_report(step, response_json=payload, response=response, context=context, ok=True)
    assert context["vars"]["registration"] == payload
    assert context["reports"] == [
        {
            "step": "register",
            "name": "registration",
            "value": payload,
            "ok": True,
            "status_code": 201,
        }
    ]


def test_save_response_as_alone_is_reported():
    context: dict = {"vars": {}, "reports": []}
    payload = {"client_id": "abc"}
    response = requests.Response()
    response.status_code = 201
    step = {"name": "register", "save_response_as": "registration"}
    apply_save(step, payload, response, context, body_text="{}")
    apply_report(step, response_json=payload, response=response, context=context, ok=True)
    assert context["reports"][0]["name"] == "registration"
    assert context["reports"][0]["value"] == payload


def test_report_false_opts_out_of_save_response_as_reporting():
    context: dict = {"vars": {}, "reports": []}
    step = {"name": "register", "save_response_as": "registration", "report": False}
    apply_report(step, response_json={"a": 1}, response=None, context=context, ok=True)
    assert context["reports"] == []


def test_apply_report_string_also_saves_var():
    context: dict = {"vars": {}, "reports": []}
    payload = {"access_token": "t"}
    step = {"name": "token", "report": "token_response"}
    apply_report(step, response_json=payload, response=None, context=context, ok=True)
    assert context["vars"]["token_response"] == payload
    assert context["reports"][0]["name"] == "token_response"
    assert context["reports"][0]["value"] == payload


def test_apply_report_object_collects_fields():
    context: dict = {"vars": {"nonce": "n1"}, "reports": []}
    response = requests.Response()
    response.status_code = 200
    response.headers["X-Request-Id"] = "req-9"
    payload = {"client_id": "c1"}
    step = {
        "name": "register",
        "report": {
            "client_id": "{{ json.client_id }}",
            "status": "status_code",
            "rid": "headers.X-Request-Id",
            "nonce": "vars.nonce",
        },
    }
    apply_report(
        step,
        response_json=payload,
        response=response,
        context=context,
        body_text="{}",
        ok=True,
    )
    assert context["reports"][0]["value"] == {
        "client_id": "c1",
        "status": 200,
        "rid": "req-9",
        "nonce": "n1",
    }


def test_run_step_collects_report_with_foreach(monkeypatch):
    def fake_request(self, method, url, **kwargs):  # noqa: ARG001
        response = requests.Response()
        response.status_code = 201
        response.headers["Content-Type"] = "application/json"
        client_id = kwargs.get("json", {}).get("client_id")
        response._content = f'{{"client_id":"{client_id}"}}'.encode()
        response.url = url
        return response

    _allow_public_dns(monkeypatch)
    monkeypatch.setattr("requests.Session.request", fake_request)
    context = {
        "vars": {},
        "env": {},
        "constants": {"ids": ["a", "b"]},
        "reports": [],
    }
    success, mismatch, _latency, _payload, _response = run_step(
        requests.Session(),
        {
            "name": "register",
            "method": "POST",
            "path": "https://as.example.org/register",
            "foreach": "{{ constants.ids }}",
            "json": {"client_id": "{{ item }}"},
            "report": "registration",
            "expected_status": 201,
        },
        "",
        context,
        {},
    )
    assert success and not mismatch
    assert len(context["reports"]) == 2
    assert context["reports"][0]["index"] == 0
    assert context["reports"][0]["value"]["client_id"] == "a"
    assert context["reports"][1]["index"] == 1
    assert context["reports"][1]["value"]["client_id"] == "b"


def test_schema_accepts_report_shapes():
    validate_scenario_document({"steps": [{"method": "GET", "path": "/", "report": True}]})
    validate_scenario_document({"steps": [{"method": "GET", "path": "/", "report": "registration"}]})
    validate_scenario_document(
        {"steps": [{"method": "GET", "path": "/", "report": {"id": "{{ json.id }}"}}]}
    )
    with pytest.raises(ScenarioValidationError):
        validate_scenario_document({"steps": [{"method": "GET", "path": "/", "report": 1}]})
