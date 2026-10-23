from __future__ import annotations

import ipaddress
import json
from pathlib import Path

from webapp.app import (
    _convert_imported_scenario,
    _normalize_examples_rel,
    _parse_import_payload,
    _scenario_auth_callback_page,
    _usable_base_url,
)


def _allow_public_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        "tools.scenario_runner.resolve_host_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )


def test_users_endpoint_and_parent_collection(client):
    users = client.get("/api/users")
    assert users.status_code == 200
    assert isinstance(users.json()["users"], list)

    tree = client.get("/api/scenarios")
    assert tree.status_code == 200
    examples = Path(__file__).resolve().parents[1] / "examples"
    scenario_files = [
        p
        for p in examples.rglob("*.json")
        if p.name not in {"suite.json", "collection.json"} and "oauth2-server" not in p.parts
    ]
    assert scenario_files
    rel = scenario_files[0].relative_to(examples).as_posix()
    parent = client.get(f"/api/scenarios/parent-collection?path={rel}")
    assert parent.status_code in {200, 404}
    file_resp = client.get(f"/api/scenarios/file?path={rel}")
    assert file_resp.status_code == 200
    assert isinstance(file_resp.json(), dict)


def test_import_helpers_and_callback_page():
    assert _usable_base_url("https://api.example.org") == "https://api.example.org"
    assert _usable_base_url("") == ""
    assert _normalize_examples_rel("demo/suite.json") == "demo/suite.json"
    page = _scenario_auth_callback_page("ok", True)
    assert "ok" in page
    assert "runway-user-auth" in page

    suggested, document = _convert_imported_scenario(
        {
            "info": {"name": "Postman"},
            "item": [{"name": "ping", "request": {"method": "GET", "url": "https://api.example.org/ping"}}],
        }
    )
    assert suggested == "Postman"
    assert document["steps"]

    parsed = _parse_import_payload(
        json.dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "T"},
                "paths": {"/x": {"get": {"operationId": "x"}}},
            }
        ).encode(),
        "spec.json",
    )
    assert parsed["openapi"] == "3.0.0"

    openapi_name, openapi_doc = _convert_imported_scenario(parsed)
    assert openapi_name == "T"
    assert openapi_doc["steps"]


def test_test_sequence_and_logout(client, monkeypatch):
    _allow_public_dns(monkeypatch)

    class _Response:
        status_code = 200
        url = "https://api.example.org/ping"
        headers = {"Content-Type": "application/json"}
        reason = "OK"
        text = '{"ok":true}'

        def json(self):
            return {"ok": True}

    monkeypatch.setattr("requests.Session.request", lambda self, *a, **k: _Response())
    response = client.post(
        "/api/test-sequence",
        json={
            "scenario": {
                "steps": [
                    {"name": "one", "method": "GET", "path": "https://api.example.org/ping", "expected_status": 200},
                    {"name": "two", "method": "GET", "path": "https://api.example.org/ping", "expected_status": 200},
                ]
            },
            "through_index": 1,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["passed"] == 2
    assert len(body["steps"]) == 2

    logout = client.get("/logout", follow_redirects=False)
    assert logout.status_code in {200, 302, 303, 307}

    auth_cb = client.get("/scenario-auth/callback?error=access_denied")
    assert auth_cb.status_code == 200
    assert "cancelled" in auth_cb.text.lower() or "rejected" in auth_cb.text.lower()
