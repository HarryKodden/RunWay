from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from webapp import auth


def _request(url: str) -> Request:
    return Request({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": "/login", "raw_path": b"/login", "query_string": url.split("?", 1)[1].encode() if "?" in url else b"", "headers": [(b"host", url.split("://", 1)[1].split("/", 1)[0].encode())], "client": ("127.0.0.1", 123), "server": ("127.0.0.1", 9011)})


def test_login_host_noop_when_hosts_match(monkeypatch):
    monkeypatch.setattr(auth, "OIDC_REDIRECT_URI", "http://127.0.0.1:9011/auth/callback")
    assert auth.login_on_callback_host(_request("http://127.0.0.1:9011/login")) is None


def test_login_host_bounces_localhost_to_configured_loopback(monkeypatch):
    monkeypatch.setattr(auth, "OIDC_REDIRECT_URI", "http://127.0.0.1:9011/auth/callback")
    bounce = auth.login_on_callback_host(_request("http://localhost:9011/login"))
    assert bounce is not None
    assert bounce.status_code == 302
    assert bounce.headers["location"] == "http://127.0.0.1:9011/login?host_align=1"


def test_login_host_stops_after_one_loopback_bounce(monkeypatch):
    monkeypatch.setattr(auth, "OIDC_REDIRECT_URI", "http://127.0.0.1:9011/auth/callback")
    assert auth.login_on_callback_host(_request("http://localhost:9011/login?host_align=1")) is None


def test_login_host_rejects_tailscale_mismatch_without_redirect_loop(monkeypatch):
    monkeypatch.setattr(auth, "OIDC_REDIRECT_URI", "http://127.0.0.1:9011/auth/callback")
    with pytest.raises(HTTPException) as exc:
        auth.login_on_callback_host(_request("http://100.72.16.117:9011/login"))
    assert exc.value.status_code == 400
    assert "OIDC_REDIRECT_URI" in str(exc.value.detail)
    assert "100.72.16.117" in str(exc.value.detail)
