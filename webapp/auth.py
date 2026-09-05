from __future__ import annotations

import os
from urllib.parse import urlparse

from authlib.integrations.base_client.errors import MismatchingStateError, OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from joserfc.errors import MissingClaimError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response

from webapp.db import get_db
from webapp.models import User

OIDC_ISSUER = (os.environ.get("OIDC_ISSUER") or "").strip()
OIDC_CLIENT_ID = (os.environ.get("OIDC_CLIENT_ID") or "").strip()
OIDC_CLIENT_SECRET = (os.environ.get("OIDC_CLIENT_SECRET") or "").strip()
OIDC_REDIRECT_URI = (os.environ.get("OIDC_REDIRECT_URI") or "").strip()
SESSION_SECRET = (os.environ.get("SESSION_SECRET") or "dev-insecure-session-secret").strip()
SESSION_SECURE = (os.environ.get("SESSION_SECURE") or "").strip().lower() in {"1", "true", "yes"}
LOCAL_ISSUER = "local"
LOCAL_SUB = "local-dev"
OIDC_STATE_COOKIE = "aft_oidc"
OIDC_STATE_MAX_AGE = 600


def session_https_only() -> bool:
    """Use a Secure cookie only when the browser will actually send it."""
    if not SESSION_SECURE:
        return False
    if OIDC_REDIRECT_URI.startswith("http://"):
        return False
    return True


def _oidc_state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(SESSION_SECRET, salt="aft-oidc-state")


def dump_oidc_state(session: dict) -> str | None:
    payload = {key: value for key, value in session.items() if str(key).startswith("_state_oidc_")}
    if not payload:
        return None
    return _oidc_state_serializer().dumps(payload)


def restore_oidc_state(request: Request) -> None:
    if any(str(key).startswith("_state_oidc_") for key in request.session):
        return
    raw = request.cookies.get(OIDC_STATE_COOKIE)
    if not raw:
        return
    try:
        payload = _oidc_state_serializer().loads(raw, max_age=OIDC_STATE_MAX_AGE)
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return
    if isinstance(payload, dict):
        request.session.update(payload)


def attach_oidc_state_cookie(response: Response, request: Request) -> Response:
    token = dump_oidc_state(request.session)
    if not token:
        return response
    response.set_cookie(
        OIDC_STATE_COOKIE,
        token,
        max_age=OIDC_STATE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=session_https_only(),
        path="/",
    )
    return response


def clear_oidc_state_cookie(response: Response) -> Response:
    response.delete_cookie(OIDC_STATE_COOKIE, path="/")
    return response


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"})


def _request_hostname(request: Request) -> str:
    return (request.url.hostname or "").lower().strip("[]")


def login_on_callback_host(request: Request) -> RedirectResponse | None:
    """Keep the session cookie on the same host as OIDC_REDIRECT_URI.

    Only auto-corrects loopback aliases (localhost ↔ 127.0.0.1). Other host
    mismatches used to 302 forever behind Tailscale/proxies that never present
    the configured Host header — those now get a clear 400 instead.
    """
    configured = urlparse(OIDC_REDIRECT_URI)
    incoming_host = _request_hostname(request)
    expected_host = (configured.hostname or "").lower().strip("[]")
    if not expected_host or incoming_host == expected_host:
        return None
    if incoming_host in _LOOPBACK_HOSTS and expected_host in _LOOPBACK_HOSTS:
        # One-shot bounce so we do not loop if Host stays wrong.
        if request.query_params.get("host_align") == "1":
            return None
        target = configured._replace(path="/login", query="host_align=1", fragment="").geturl()
        return RedirectResponse(url=target, status_code=302)
    raise HTTPException(
        status_code=400,
        detail=(
            "Sign-in must use the same host as OIDC_REDIRECT_URI. "
            f"Open {configured.scheme}://{configured.netloc}/ and try again "
            f"(this request used host {incoming_host!r})."
        ),
    )

oauth = OAuth()


def oidc_enabled() -> bool:
    return bool(OIDC_ISSUER and OIDC_CLIENT_ID and OIDC_REDIRECT_URI)


def configure_oauth() -> None:
    if not oidc_enabled():
        return
    issuer = OIDC_ISSUER.rstrip("/")
    oauth.register(
        name="oidc",
        server_metadata_url=f"{issuer}/.well-known/openid-configuration",
        client_id=OIDC_CLIENT_ID,
        client_secret=OIDC_CLIENT_SECRET or None,
        client_kwargs={
            "scope": "openid profile email",
            "code_challenge_method": "S256",
        },
    )


def _as_claims(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    return {}


async def complete_oidc_login(request: Request) -> dict[str, object]:
    """Exchange the code and read identity without requiring a nonce claim.

    Some issuers (including SRAM) omit ``nonce`` from the ID token even when
    the authorization request included one. Authlib then raises MissingClaimError.
    """
    restore_oidc_state(request)
    client = oauth.oidc
    error = request.query_params.get("error")
    if error:
        raise OAuthError(error=error, description=request.query_params.get("error_description"))
    params = {
        "code": request.query_params.get("code"),
        "state": request.query_params.get("state"),
    }
    state_data = await client.framework.get_state_data(request.session, params.get("state"))
    if not state_data:
        raise MismatchingStateError()
    await client.framework.clear_state_data(request.session, params.get("state"))
    params = client._format_state_params(state_data, params)
    token = await client.fetch_access_token(**params)
    claims = _as_claims(token.get("userinfo"))
    if not claims.get("sub") and token.get("id_token"):
        try:
            claims = _as_claims(await client.parse_id_token(token, nonce=state_data.get("nonce")))
        except MissingClaimError:
            claims = _as_claims(await client.parse_id_token(token, nonce=None))
    if not claims.get("sub"):
        try:
            claims = _as_claims(await client.userinfo(token=token))
        except Exception:
            claims = claims
    if not claims.get("sub"):
        raise HTTPException(status_code=400, detail="OIDC token missing sub")
    return claims


def get_or_create_user(
    db: Session,
    *,
    issuer: str,
    sub: str,
    email: str | None = None,
    name: str | None = None,
) -> User:
    user = db.scalar(select(User).where(User.issuer == issuer, User.sub == sub))
    if user is None:
        user = User(issuer=issuer, sub=sub, email=email, name=name)
        db.add(user)
        db.flush()
        return user
    if email:
        user.email = email
    if name:
        user.name = name
    return user


def get_or_create_local_user(db: Session) -> User:
    return get_or_create_user(db, issuer=LOCAL_ISSUER, sub=LOCAL_SUB, name="Local user")


def resolve_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if user_id:
        user = db.get(User, str(user_id))
        if user is not None:
            return user
    if not oidc_enabled():
        return get_or_create_local_user(db)
    return None


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    return resolve_user(request, db)


def current_user_required(request: Request, db: Session = Depends(get_db)) -> User:
    user = resolve_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required")
    return user


def public_user(user: User | None) -> dict[str, object]:
    return {
        "authenticated": user is not None,
        "oidc_enabled": oidc_enabled(),
        "user": None
        if user is None
        else {
            "id": user.id,
            "email": user.email,
            "name": user.name,
        },
    }
