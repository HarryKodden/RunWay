from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import requests

from tools.scenario_runner import is_user_auth_step, preview_step_request, run_step


class _Response:
    def __init__(self, status: int, payload, url: str):
        self.status_code = status
        self.url = url
        self.headers: dict[str, str] = {}
        self.reason = "OK"
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


def _form(data) -> dict[str, list[str]]:
    if not isinstance(data, str) or not data:
        return {}
    return parse_qs(data)


def _install_idp(monkeypatch):
    calls: list[dict] = []
    device_polls = {"n": 0}

    def fake_request(self, method, url, **kwargs):  # noqa: ARG001
        method = str(method or "GET").upper()
        path = urlparse(url).path
        form = _form(kwargs.get("data"))
        body = kwargs.get("json") if isinstance(kwargs.get("json"), dict) else {}
        headers = kwargs.get("headers") or {}
        calls.append({"method": method, "path": path, "form": form, "json": body, "headers": dict(headers)})
        if method == "GET" and path.endswith("/.well-known/openid-configuration"):
            return _Response(
                200,
                {
                    "issuer": "https://idp.example.org",
                    "authorization_endpoint": "https://idp.example.org/authorize",
                    "token_endpoint": "https://idp.example.org/token",
                    "userinfo_endpoint": "https://idp.example.org/userinfo",
                    "introspection_endpoint": "https://idp.example.org/introspect",
                    "registration_endpoint": "https://idp.example.org/register",
                    "device_authorization_endpoint": "https://idp.example.org/device",
                },
                url,
            )
        if method == "POST" and path.endswith("/register"):
            grants = body.get("grant_types") or []
            if "urn:ietf:params:oauth:grant-type:token-exchange" in grants:
                return _Response(201, {"client_id": "backend", "client_secret": "backend-secret"}, url)
            return _Response(201, {"client_id": "frontend", "client_secret": "frontend-secret"}, url)
        if method == "POST" and path.endswith("/device"):
            return _Response(
                200,
                {
                    "device_code": "device-1",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "/device",
                    "verification_uri_complete": "https://idp.example.org/device?user_code=ABCD-EFGH",
                    "interval": 5,
                    "expires_in": 300,
                },
                url,
            )
        if method == "POST" and path.endswith("/token"):
            grant = (form.get("grant_type") or [""])[0]
            if grant == "urn:ietf:params:oauth:grant-type:device_code":
                device_polls["n"] += 1
                if device_polls["n"] == 1:
                    return _Response(400, {"error": "authorization_pending"}, url)
                return _Response(
                    200,
                    {"access_token": "user-at", "refresh_token": "user-rt", "id_token": "user-id", "token_type": "Bearer"},
                    url,
                )
            if grant == "authorization_code":
                return _Response(
                    200,
                    {"access_token": "user-at", "refresh_token": "user-rt", "id_token": "user-id", "token_type": "Bearer"},
                    url,
                )
            if grant == "urn:ietf:params:oauth:grant-type:token-exchange":
                return _Response(200, {"refresh_token": "exchanged-rt", "token_type": "Bearer"}, url)
            if grant == "refresh_token":
                return _Response(200, {"access_token": "backend-at", "refresh_token": "backend-rt", "token_type": "Bearer"}, url)
            return _Response(400, {"error": "unsupported_grant"}, url)
        if method == "GET" and path.endswith("/userinfo"):
            return _Response(200, {"sub": "user-1"}, url)
        if method == "POST" and path.endswith("/introspect"):
            return _Response(200, {"active": True}, url)
        return _Response(404, {"error": "not_found", "path": path}, url)

    monkeypatch.setattr(requests.Session, "request", fake_request)
    return calls


def _run_payload(**extra):
    payload = {
        "scenario_file": "./examples/token-exchange/suite.json",
        "scenario_environment": "idp",
        "environment_overrides": {
            "server": "https://idp.example.org",
            "api_key": "test-key",
            "redirect_uri": "http://127.0.0.1:9011/scenario-auth/callback",
        },
        "regression": True,
    }
    payload.update(extra)
    return payload


def test_user_auth_step_is_not_an_http_call():
    step = {"name": "sign_in", "method": "USER_AUTH", "user_auth": {"flows": ["device_code"]}}
    assert is_user_auth_step(step)
    success, mismatch, _latency, message, response = run_step(
        requests.Session(),
        step,
        "https://idp.example.org",
        {"vars": {}, "env": {}},
        {},
    )
    assert success is False
    assert mismatch is False
    assert response is None
    assert "browser sign-in" in str(message)
    preview = preview_step_request([step], 0, "https://idp.example.org", {"vars": {}, "env": {}}, {})
    assert "USER_AUTH" in preview["curl"]
    assert "device_code" in preview["curl"]


def test_run_requires_idp_settings_before_sign_in(client):
    response = client.post(
        "/api/runs",
        json={
            "scenario_file": "./examples/token-exchange/suite.json",
            "scenario_environment": "idp",
            "regression": True,
        },
    )
    assert response.status_code == 400
    assert "api_key" in response.text
    assert "redirect_uri" in response.text


def test_device_code_breakout_completes_token_exchange(client, monkeypatch):
    calls = _install_idp(monkeypatch)
    started = client.post("/api/runs", json=_run_payload())
    assert started.status_code == 200, started.text
    pending = started.json()
    assert pending["status"] == "awaiting_user"
    assert pending["flows"] == ["device_code", "authorization_code"]
    assert pending["step_name"] == "user_authentication"

    begun = client.post(f"/api/runs/interactive/{pending['session_id']}/begin", json={"flow": "device_code"})
    assert begun.status_code == 200, begun.text
    prompt = begun.json()
    assert prompt["user_code"] == "ABCD-EFGH"
    assert prompt["verification_uri"] == "https://idp.example.org/device"
    assert prompt["verification_uri_complete"].endswith("user_code=ABCD-EFGH")
    device_call = next(call for call in calls if call["path"].endswith("/device"))
    assert device_call["form"]["client_id"] == ["frontend"]
    assert "Authorization" not in device_call["headers"]

    from tools.interactive_run import _SESSIONS

    early = client.post(f"/api/runs/interactive/{pending['session_id']}/advance")
    assert early.status_code == 200
    assert early.json()["status"] == "pending"
    assert not any(
        call["path"].endswith("/token") and (call["form"].get("grant_type") or [""])[0].endswith("device_code")
        for call in calls
    )

    _SESSIONS[pending["session_id"]].next_poll_at = 0
    waiting = client.post(f"/api/runs/interactive/{pending['session_id']}/advance")
    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["status"] == "pending"

    _SESSIONS[pending["session_id"]].next_poll_at = 0
    finished = client.post(f"/api/runs/interactive/{pending['session_id']}/advance")
    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["status"] == "completed", body.get("stdout")
    names = [step["name"] for step in body["summary"]["scenario"]["steps"]]
    assert "token_exchange.user_authentication" in names
    assert "token_exchange.token_exchange" in names
    assert "token_exchange.backend_userinfo" in names
    assert all(step["failure"] == 0 for step in body["summary"]["scenario"]["steps"])

    exchanged = next(
        call for call in calls
        if call["path"].endswith("/token")
        and (call["form"].get("grant_type") or [""])[0].endswith("token-exchange")
    )
    assert exchanged["form"]["subject_token"] == ["user-rt"]
    assert any(call["headers"].get("X-API-KEY") == "test-key" for call in calls if call["path"].endswith("/register"))


def test_authorization_code_breakout_exchanges_the_code(client, monkeypatch):
    calls = _install_idp(monkeypatch)
    started = client.post("/api/runs", json=_run_payload()).json()
    begun = client.post(
        f"/api/runs/interactive/{started['session_id']}/begin",
        json={"flow": "authorization_code"},
    )
    assert begun.status_code == 200, begun.text
    prompt = begun.json()
    parsed = urlparse(prompt["authorization_url"])
    query = parse_qs(parsed.query)
    assert parsed.path == "/authorize"
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://127.0.0.1:9011/scenario-auth/callback"]
    assert query["client_id"] == ["frontend"]
    assert "offline_access" in query["scope"][0]

    callback = client.get("/scenario-auth/callback", params={"code": "auth-code-1", "state": query["state"][0]})
    assert callback.status_code == 200
    assert "Sign-in complete" in callback.text
    assert "runway-user-auth" in callback.text
    assert "auth-code-1" not in callback.text

    finished = client.post(f"/api/runs/interactive/{started['session_id']}/advance")
    assert finished.status_code == 200, finished.text
    assert finished.json()["status"] == "completed"
    code_grant = next(
        call for call in calls
        if call["path"].endswith("/token") and (call["form"].get("grant_type") or [""])[0] == "authorization_code"
    )
    assert code_grant["form"]["code"] == ["auth-code-1"]
    assert code_grant["form"]["code_verifier"][0]


def test_unknown_sign_in_session_is_not_found(client):
    missing = client.post("/api/runs/interactive/missing/advance")
    assert missing.status_code == 404
    unmatched = client.get("/scenario-auth/callback", params={"code": "x", "state": "nope"})
    assert unmatched.status_code == 200
    assert "could not be matched" in unmatched.text
