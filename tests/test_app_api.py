from __future__ import annotations

import ipaddress
import json
from pathlib import Path

import requests

from webapp.app import mask_sensitive_text


def _allow_public_dns(monkeypatch) -> None:
    monkeypatch.setattr(
        "tools.scenario_runner.resolve_host_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )


def test_health_and_index(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200
    assert "RunWay" in page.text


def test_me_uses_local_user_when_oidc_is_unset(client):
    me = client.get("/api/me")
    assert me.status_code == 200
    body = me.json()
    assert body["authenticated"] is True
    assert body["oidc_enabled"] is False
    assert body["user"]["name"] == "Local user"


def test_scenarios_tree_includes_public_library(client):
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    tree = response.json()
    names = [child.get("name") for child in tree.get("children") or []]
    assert "Public library" in names


def test_parse_curl_endpoint(client):
    response = client.post(
        "/api/parse-curl",
        json={"curl": "curl -sS -X GET https://api.example.org/clients -H 'X-API-Key: secret'"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["path"] == "https://api.example.org/clients"
    assert body["headers"]["X-API-Key"] == "secret"
    empty = client.post("/api/parse-curl", json={"curl": ""})
    assert empty.status_code == 400


def test_preview_step_uses_placeholder_default(client):
    response = client.post(
        "/api/preview-step",
        json={
            "scenario": {
                "selected_environment": "",
                "steps": [
                    {
                        "name": "list_clients",
                        "method": "GET",
                        "path": '{{ server : "https://api.example.org" }}/clients',
                    }
                ],
            },
            "step_index": 0,
            "selected_environment": "",
        },
    )
    assert response.status_code == 200
    assert response.json()["request"]["url"] == "https://api.example.org/clients"


def test_preview_step_override_beats_placeholder_default(client):
    response = client.post(
        "/api/preview-step",
        json={
            "scenario": {
                "selected_environment": "",
                "steps": [{"method": "GET", "path": '{{ server : "https://api.example.org" }}/clients'}],
            },
            "step_index": 0,
            "selected_environment": "",
            "environment_overrides": {"server": "https://other.example.org"},
        },
    )
    assert response.status_code == 200
    assert response.json()["request"]["url"] == "https://other.example.org/clients"


def test_preview_step_without_environment(client):
    response = client.post(
        "/api/preview-step",
        json={
            "scenario": {
                "environments": {"dev": {"server": "https://dev.example.org", "unused": ""}},
                "selected_environment": "",
                "steps": [
                    {
                        "name": "list_clients",
                        "method": "GET",
                        "path": "https://api.example.org/clients",
                        "expected_status": 200,
                    }
                ],
            },
            "step_index": 0,
            "base_url": "",
            "selected_environment": "",
            "environment_overrides": {},
            "hydrate_prior": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["request"]["url"] == "https://api.example.org/clients"
    assert body["unresolved"] == []


def test_preview_step_requires_referenced_env_value(client):
    response = client.post(
        "/api/preview-step",
        json={
            "scenario": {
                "environments": {"dev": {"token": ""}},
                "selected_environment": "dev",
                "steps": [{"method": "GET", "path": "https://api.example.org/{{ env.token }}"}],
            },
            "step_index": 0,
            "selected_environment": "dev",
        },
    )
    assert response.status_code == 400
    assert "token" in response.json()["detail"]


def test_mask_sensitive_text():
    assert mask_sensitive_text("Authorization: Bearer super-secret") == "Authorization: Bearer ***"
    assert mask_sensitive_text("authorization = bearer abc.def") == "authorization = bearer ***"
    assert mask_sensitive_text("plain text") == "plain text"
    assert mask_sensitive_text("") == ""


def test_test_step_reports_saved_discovery_fields(client, monkeypatch):
    class _Response:
        def __init__(self, payload, url):
            self.status_code = 200
            self.url = url
            self.headers = {"Content-Type": "application/json"}
            self.reason = "OK"
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    def fake_request(self, method, url, **kwargs):  # noqa: ARG001
        return _Response(
            {
                "issuer": "https://idp.example.org",
                "authorization_endpoint": "https://idp.example.org/authorize",
                "token_endpoint": "https://idp.example.org/token",
                "registration_endpoint": "https://idp.example.org/register",
                "device_authorization_endpoint": "https://idp.example.org/device",
            },
            url,
        )

    _allow_public_dns(monkeypatch)
    monkeypatch.setattr("requests.Session.request", fake_request)
    response = client.post(
        "/api/test-step",
        json={
            "scenario": {
                "steps": [
                    {
                        "name": "discover_openid",
                        "method": "GET",
                        "path": "https://idp.example.org/.well-known/openid-configuration",
                        "expected_status": 200,
                        "save": {
                            "issuer": "{{ json.issuer }}",
                            "registration_endpoint": "json.registration_endpoint",
                            "device_authorization_endpoint": "json.device_authorization_endpoint",
                        },
                    }
                ]
            },
            "step_index": 0,
        },
    )
    assert response.status_code == 200
    saved = response.json()["saved"]
    assert saved["issuer"] == "https://idp.example.org"
    assert saved["registration_endpoint"] == "https://idp.example.org/register"
    assert saved["device_authorization_endpoint"] == "https://idp.example.org/device"
    assert "worker_id" not in saved


def test_failed_discovery_does_not_save_null_json_vars(client, monkeypatch):
    def fake_request(self, method, url, **kwargs):  # noqa: ARG001
        raise requests.ConnectionError("connection refused")

    _allow_public_dns(monkeypatch)
    monkeypatch.setattr("requests.Session.request", fake_request)
    response = client.post(
        "/api/test-step",
        json={
            "scenario": {
                "steps": [
                    {
                        "name": "discover_openid",
                        "method": "GET",
                        "path": "https://idp.example.org/.well-known/openid-configuration",
                        "expected_status": 200,
                        "save": {"issuer": "json.issuer", "token_endpoint": "json.token_endpoint"},
                    }
                ]
            },
            "step_index": 0,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["saved"] == {}
    assert "issuer" not in body["context"]["vars"]
    assert "connection refused" in str(body["response_body"])


def test_test_step_rejects_loopback_ip_aliases(client):
    for url in (
        "http://127.1:8080/api/health",
        "http://0x7f000001:8080/api/health",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.1.20/",
        "https://8.8.8.8/",
        "https://[2001:4860:4860::8888]/",
    ):
        response = client.post(
            "/api/test-step",
            json={
                "scenario": {
                    "name": "ssrf_guard",
                    "steps": [{"method": "GET", "url": url, "name": "probe"}],
                },
                "step_index": 0,
            },
        )
        assert response.status_code == 400, url
        detail = response.json()["detail"].lower()
        assert "fqdn" in detail or "ip" in detail or "localhost" in detail


def test_validate_scenario_endpoint_accepts_token_exchange(client):
    document = json.loads(
        (Path(__file__).resolve().parents[1] / "examples" / "token-exchange" / "token_exchange.json").read_text(
            encoding="utf-8"
        )
    )
    response = client.post("/api/scenarios/validate", json=document)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    rejected = client.post("/api/scenarios/validate", json={"steps": [{"method": "NOPE"}]})
    assert rejected.status_code == 400
    assert any("method" in item for item in rejected.json()["detail"]["errors"])


def test_workspace_save_rejects_a_scenario_that_breaks_the_schema(client):
    created = client.post(
        "/api/explorer/create",
        json={"kind": "collection", "name": "Schema check", "target": "workspace"},
    )
    assert created.status_code == 200
    scenario = client.post(
        "/api/explorer/create",
        json={"kind": "scenario", "name": "draft", "target": created.json()["path"]},
    )
    assert scenario.status_code == 200
    path = scenario.json()["path"]
    rejected = client.post(
        f"/api/workspace/file?path={path}",
        json={"name": "draft", "steps": [{"method": "NOPE", "path": "/"}]},
    )
    assert rejected.status_code == 400
    assert any("method" in item for item in rejected.json()["detail"]["errors"])
    kept = client.get(f"/api/workspace/file?path={path}")
    assert kept.status_code == 200
    assert "NOPE" not in json.dumps(kept.json())

