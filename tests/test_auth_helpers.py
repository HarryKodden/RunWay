from __future__ import annotations

from types import SimpleNamespace

from starlette.responses import Response

from webapp import auth


def test_session_https_only(monkeypatch):
    monkeypatch.setattr(auth, "SESSION_SECURE", False)
    assert auth.session_https_only() is False
    monkeypatch.setattr(auth, "SESSION_SECURE", True)
    monkeypatch.setattr(auth, "OIDC_REDIRECT_URI", "http://127.0.0.1:9011/auth/callback")
    assert auth.session_https_only() is False
    monkeypatch.setattr(auth, "OIDC_REDIRECT_URI", "https://app.example.org/auth/callback")
    assert auth.session_https_only() is True


def test_oidc_state_dump_and_cookies(monkeypatch):
    monkeypatch.setattr(auth, "SESSION_SECRET", "test-secret")
    monkeypatch.setattr(auth, "SESSION_SECURE", False)
    monkeypatch.setattr(auth, "OIDC_REDIRECT_URI", "http://127.0.0.1:9011/auth/callback")

    assert auth.dump_oidc_state({}) is None
    token = auth.dump_oidc_state({"_state_oidc_abc": {"data": True}, "other": 1})
    assert token
    payload = auth._oidc_state_serializer().loads(token, max_age=auth.OIDC_STATE_MAX_AGE)
    assert payload == {"_state_oidc_abc": {"data": True}}

    response = Response("ok")
    request = SimpleNamespace(session={"_state_oidc_abc": {"data": True}})
    with_cookie = auth.attach_oidc_state_cookie(response, request)  # type: ignore[arg-type]
    assert auth.OIDC_STATE_COOKIE in (with_cookie.headers.get("set-cookie") or "")

    empty = auth.attach_oidc_state_cookie(Response("ok"), SimpleNamespace(session={}))  # type: ignore[arg-type]
    assert auth.OIDC_STATE_COOKIE not in (empty.headers.get("set-cookie") or "")

    cleared = auth.clear_oidc_state_cookie(Response("ok"))
    assert cleared is not None


def test_restore_oidc_state(monkeypatch):
    monkeypatch.setattr(auth, "SESSION_SECRET", "test-secret")
    token = auth.dump_oidc_state({"_state_oidc_abc": {"data": True}})
    assert token

    class _Req:
        def __init__(self, session=None, cookies=None):
            self.session = session if session is not None else {}
            self.cookies = cookies or {}

    already = _Req(session={"_state_oidc_keep": {"x": 1}})
    auth.restore_oidc_state(already)  # type: ignore[arg-type]
    assert already.session == {"_state_oidc_keep": {"x": 1}}

    restored = _Req(cookies={auth.OIDC_STATE_COOKIE: token})
    auth.restore_oidc_state(restored)  # type: ignore[arg-type]
    assert restored.session["_state_oidc_abc"] == {"data": True}

    bad = _Req(cookies={auth.OIDC_STATE_COOKIE: "not-valid"})
    auth.restore_oidc_state(bad)  # type: ignore[arg-type]
    assert bad.session == {}

    empty = _Req()
    auth.restore_oidc_state(empty)  # type: ignore[arg-type]
    assert empty.session == {}


def test_public_user_and_as_claims():
    assert auth._as_claims({"sub": "1"}) == {"sub": "1"}
    assert auth._as_claims("nope") == {}
    assert auth.public_user(None)["authenticated"] is False
    user = SimpleNamespace(id="u1", email="a@b.c", name="Ada")
    body = auth.public_user(user)
    assert body["authenticated"] is True
    assert body["user"]["id"] == "u1"


def test_oidc_enabled(monkeypatch):
    monkeypatch.setattr(auth, "OIDC_ISSUER", "")
    monkeypatch.setattr(auth, "OIDC_CLIENT_ID", "")
    monkeypatch.setattr(auth, "OIDC_REDIRECT_URI", "")
    assert auth.oidc_enabled() is False
    monkeypatch.setattr(auth, "OIDC_ISSUER", "https://idp.example.org")
    monkeypatch.setattr(auth, "OIDC_CLIENT_ID", "client")
    monkeypatch.setattr(auth, "OIDC_REDIRECT_URI", "https://app.example.org/cb")
    assert auth.oidc_enabled() is True
