"""Pause a scenario run so a person can finish an OIDC sign-in, then continue."""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

import requests

from tools.oauth_helpers import basic_auth_header, form_encode, generate_pkce
from tools.scenario_runner import (
    StepStats,
    apply_collection_defaults,
    configure_http_session,
    apply_env_overrides,
    apply_save,
    discover_parent_collection,
    exported_context_vars,
    finalize_environment_values,
    is_collection,
    ensure_safe_request_url,
    is_user_auth_step,
    load_collection_members,
    missing_environment_dependencies,
    now_iso,
    render_template,
    require_routable_api_targets,
    resolve_base_url,
    resolve_environment_values,
    run_step,
    safe_session_request,
)

PENDING_TOKEN_ERRORS = {"authorization_pending", "slow_down"}
ALLOWED_FLOWS = ("device_code", "authorization_code")
SESSION_TTL_SECONDS = 20 * 60

_SESSIONS: dict[str, "InteractiveSession"] = {}
_SESSIONS_LOCK = threading.Lock()


@dataclass
class InteractiveSession:
    id: str
    created: float
    phase: str
    collection: dict[str, Any] | None
    members: list[tuple[str, dict[str, Any]]]
    member_index: int
    step_index: int
    extra_env: dict[str, Any]
    environment_name: str
    base_url_override: str
    http: requests.Session
    started: float
    owner_user_id: str = ""
    member_stats: list[dict[str, StepStats]] = field(default_factory=list)
    context: dict[str, Any] | None = None
    base_url: str = ""
    random_generators: dict[str, Any] = field(default_factory=dict)
    prepared_steps: list[dict[str, Any]] = field(default_factory=list)
    user_auth_step: dict[str, Any] | None = None
    rendered_auth: dict[str, Any] = field(default_factory=dict)
    flow: str = ""
    device_code: str = ""
    poll_interval: float = 5.0
    next_poll_at: float = 0.0
    expires_at: float = 0.0
    auth_state: str = ""
    code_verifier: str = ""
    redirect_uri: str = ""
    authorization_code: str = ""
    callback_error: str = ""
    result: dict[str, Any] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def scenario_file_needs_user_auth(path: Path) -> bool:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(document, dict):
        return False
    if is_collection(document):
        try:
            members = load_collection_members(path, document)
        except SystemExit:
            return False
        return any(_document_has_user_auth(child) for _, child in members)
    if _document_has_user_auth(document):
        return True
    parent = discover_parent_collection(path, document)
    if not parent:
        return False
    try:
        members = load_collection_members(path.parent / str(parent.get("_file") or ""), parent)
    except (SystemExit, OSError):
        return False
    return any(name == path.name and _document_has_user_auth(child) for name, child in members)


def _document_has_user_auth(document: dict[str, Any]) -> bool:
    return any(is_user_auth_step(step) for step in document.get("steps") or [] if isinstance(step, dict))


def _prune_sessions() -> None:
    cutoff = time.time() - SESSION_TTL_SECONDS
    expired = [key for key, session in _SESSIONS.items() if session.created < cutoff or session.phase in {"done", "cancelled"}]
    # Keep just-finished sessions so a duplicate advance can still see them, but drop old ones.
    for key in expired:
        session = _SESSIONS.get(key)
        if session is None:
            continue
        if session.phase in {"done", "cancelled"} and time.time() - session.created < 30:
            continue
        if session.created < cutoff or session.phase in {"done", "cancelled"}:
            _SESSIONS.pop(key, None)


def _load_members(path: Path) -> tuple[dict[str, Any] | None, list[tuple[str, dict[str, Any]]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise SystemExit("Scenario file is not a JSON object")
    if is_collection(document):
        return document, load_collection_members(path, document)
    parent = discover_parent_collection(path, document)
    child = apply_collection_defaults(document, parent) if parent else document
    return None, [(path.name, child)]


def start_interactive_run(
    scenario_path: Path,
    *,
    environment: str | None,
    extra_env: dict[str, Any] | None,
    base_url_override: str | None,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    collection, members = _load_members(scenario_path)
    if not members:
        raise SystemExit("Scenario has no steps")
    session = InteractiveSession(
        id=secrets.token_urlsafe(24),
        created=time.time(),
        phase="running",
        collection=collection,
        members=members,
        member_index=0,
        step_index=0,
        extra_env=dict(extra_env or {}),
        environment_name=str(environment or "").strip(),
        base_url_override=str(base_url_override or "").strip(),
        http=requests.Session(),
        started=time.time(),
        owner_user_id=str(owner_user_id or "").strip(),
    )
    with _SESSIONS_LOCK:
        _prune_sessions()
        _SESSIONS[session.id] = session
    try:
        return _run_until_pause(session)
    except (Exception, SystemExit):
        session.phase = "cancelled"
        with _SESSIONS_LOCK:
            _SESSIONS.pop(session.id, None)
        raise


def begin_user_auth(session_id: str, flow: str, *, owner_user_id: str | None = None) -> dict[str, Any]:
    session = _require_session(session_id, owner_user_id=owner_user_id)
    with session.lock:
        if session.phase != "choose_flow":
            raise ValueError("This run is not waiting for a sign-in method")
        chosen = str(flow or "").strip()
        allowed = _allowed_flows(session.rendered_auth)
        if chosen not in allowed:
            raise ValueError(f"Choose one of: {', '.join(allowed)}")
        session.flow = chosen
        session.callback_error = ""
        session.authorization_code = ""
        try:
            if chosen == "device_code":
                payload = _begin_device_code(session)
            else:
                payload = _begin_authorization_code(session)
        except ValueError as exc:
            session.phase = "choose_flow"
            return {"status": "failed", "session_id": session.id, "message": str(exc)}
        session.phase = "waiting"
        return payload


def advance_user_auth(session_id: str, *, owner_user_id: str | None = None) -> dict[str, Any]:
    session = _require_session(session_id, owner_user_id=owner_user_id)
    with session.lock:
        if session.phase == "done":
            return _stored_result(session)
        if session.phase != "waiting":
            raise ValueError("This run is not waiting for sign-in")
        if session.callback_error:
            return _fail_user_auth(session, session.callback_error)
        if session.expires_at and time.time() > session.expires_at:
            return _fail_user_auth(session, "Sign-in expired before it was approved")
        if session.flow == "authorization_code" and not session.authorization_code:
            return {
                "status": "pending",
                "session_id": session.id,
                "flow": session.flow,
                "message": "Waiting for the sign-in window",
                "interval_seconds": 1,
            }
        if session.flow == "device_code" and time.monotonic() < session.next_poll_at:
            return {
                "status": "pending",
                "session_id": session.id,
                "flow": session.flow,
                "message": "Waiting for you to approve the device code",
                "interval_seconds": session.poll_interval,
            }
        try:
            token_payload, response = _exchange_token(session)
        except ValueError as exc:
            return _fail_user_auth(session, str(exc))
        error = ""
        if isinstance(token_payload, dict):
            error = str(token_payload.get("error") or "")
        if error in PENDING_TOKEN_ERRORS:
            if error == "slow_down":
                session.poll_interval = min(session.poll_interval + 5, 60)
            session.next_poll_at = time.monotonic() + session.poll_interval
            return {
                "status": "pending",
                "session_id": session.id,
                "flow": session.flow,
                "message": "Waiting for you to approve the device code",
                "interval_seconds": session.poll_interval,
            }
        if error or response.status_code >= 400 or not isinstance(token_payload, dict):
            return _fail_user_auth(session, _http_error(response, token_payload))
        if not token_payload.get("access_token") and not token_payload.get("refresh_token"):
            return _fail_user_auth(session, "Token endpoint returned no access_token or refresh_token")
        _apply_token_response(session, token_payload, response)
        session.step_index += 1
        session.phase = "running"
        session.device_code = ""
        session.authorization_code = ""
        session.code_verifier = ""
        try:
            return _run_until_pause(session)
        except (Exception, SystemExit):
            session.phase = "cancelled"
            raise


def cancel_user_auth(session_id: str, *, owner_user_id: str | None = None) -> dict[str, Any]:
    session = _require_session(session_id, owner_user_id=owner_user_id)
    with session.lock:
        session.phase = "cancelled"
        return {"status": "cancelled", "session_id": session.id}


def capture_authorization_callback(
    *,
    state: str,
    code: str | None,
    error: str | None,
    error_description: str | None,
) -> bool:
    wanted = str(state or "").strip()
    if not wanted:
        return False
    with _SESSIONS_LOCK:
        session = next((item for item in _SESSIONS.values() if item.auth_state == wanted), None)
    if session is None:
        return False
    with session.lock:
        if session.phase != "waiting" or session.flow != "authorization_code":
            return False
        if error:
            detail = str(error_description or error)
            session.callback_error = detail
            return True
        if code:
            session.authorization_code = str(code)
            return True
    return False


def drop_session(session_id: str) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS.pop(session_id, None)


def _stored_result(session: InteractiveSession) -> dict[str, Any]:
    if isinstance(session.result, dict):
        return session.result
    return {"status": "failed", "session_id": session.id, "message": "Run already finished"}


def _require_session(session_id: str, *, owner_user_id: str | None = None) -> InteractiveSession:
    with _SESSIONS_LOCK:
        _prune_sessions()
        session = _SESSIONS.get(str(session_id or "").strip())
    if session is None:
        raise KeyError("Sign-in session expired or was cancelled")
    expected = str(session.owner_user_id or "").strip()
    provided = str(owner_user_id or "").strip()
    if expected and expected != provided:
        raise KeyError("Sign-in session expired or was cancelled")
    return session


def _allowed_flows(rendered: dict[str, Any]) -> list[str]:
    raw = rendered.get("flows") or list(ALLOWED_FLOWS)
    if isinstance(raw, str):
        raw = [raw]
    flows = [str(item).strip() for item in raw if str(item).strip() in ALLOWED_FLOWS]
    return flows or list(ALLOWED_FLOWS)


def _run_until_pause(session: InteractiveSession) -> dict[str, Any]:
    while session.member_index < len(session.members):
        _ensure_member(session)
        steps = session.prepared_steps
        while session.step_index < len(steps):
            step = steps[session.step_index]
            if is_user_auth_step(step):
                session.user_auth_step = step
                session.rendered_auth = _render_user_auth(step, session)
                session.phase = "choose_flow"
                session.flow = ""
                return _awaiting_payload(session)
            context = session.context or {"vars": {}, "env": {}, "meta": {}}
            success, expected_mismatch, latency_ms, response_json, response = run_step(
                session=session.http,
                step=step,
                base_url=session.base_url,
                context=context,
                random_generators=session.random_generators,
            )
            _record_step(session, step, success, expected_mismatch, latency_ms, response_json, response)
            session.step_index += 1
            if not success and step.get("stop_on_failure", True) is not False:
                return _finish(session)
        _close_member(session)
        session.member_index += 1
        session.step_index = 0
        session.context = None
    return _finish(session)


def _ensure_member(session: InteractiveSession) -> None:
    if session.context is not None:
        return
    filename, child = session.members[session.member_index]
    prepared = apply_collection_defaults(child, session.collection) if session.collection else child
    session.members[session.member_index] = (filename, prepared)
    environment_name = session.environment_name or str(prepared.get("selected_environment") or "").strip()
    base_url = (session.base_url_override or resolve_base_url(prepared, environment_name) or "").strip()
    environment_values = resolve_environment_values(prepared, environment_name)
    if session.extra_env:
        environment_values = apply_env_overrides(environment_values, session.extra_env)
    environment_values = finalize_environment_values(
        environment_values,
        base_url=base_url,
        selected_environment=environment_name,
    )
    steps = prepared.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise SystemExit(f"{filename} has no steps")
    missing = missing_environment_dependencies(environment_values, steps)
    if missing:
        raise SystemExit(f"Environment variables need a value: {', '.join(missing)}")
    env_host = environment_values.get("server") or environment_values.get("base_url")
    if isinstance(env_host, str) and env_host.strip():
        base_url = env_host.strip()
    require_routable_api_targets(environment_values, extra_urls=[base_url])
    session.base_url = base_url
    session.random_generators = prepared.get("random_generators") or {}
    session.prepared_steps = [step for step in steps if isinstance(step, dict)]
    session.context = {
        "vars": {},
        "env": dict(environment_values),
        "meta": {"environment": environment_name},
    }
    while len(session.member_stats) <= session.member_index:
        session.member_stats.append({})


def _close_member(session: InteractiveSession) -> None:
    _filename, child = session.members[session.member_index]
    vars_map = (session.context or {}).get("vars") if isinstance((session.context or {}).get("vars"), dict) else {}
    session.extra_env.update(exported_context_vars(vars_map, child.get("export_env")))


def _render_user_auth(step: dict[str, Any], session: InteractiveSession) -> dict[str, Any]:
    rendered = render_template(step.get("user_auth") or {}, session.context or {}, session.random_generators)
    return rendered if isinstance(rendered, dict) else {}


def _awaiting_payload(session: InteractiveSession) -> dict[str, Any]:
    step = session.user_auth_step or {}
    flows = _allowed_flows(session.rendered_auth)
    description = str(step.get("description") or session.rendered_auth.get("description") or "").strip()
    if not description:
        description = "Sign in to continue this run. You can use a device code or the authorization code flow."
    return {
        "status": "awaiting_user",
        "session_id": session.id,
        "step_name": step.get("name") or "user_authentication",
        "step_index": session.step_index,
        "flows": flows,
        "description": description,
        "redirect_uri": str(session.rendered_auth.get("redirect_uri") or ""),
        "completed_steps": _completed_step_rows(session),
    }


def _completed_step_rows(session: InteractiveSession) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stats in session.member_stats:
        for item in stats.values():
            rows.append({"name": item.name, "ok": item.failure == 0 and item.expected_mismatch == 0})
    return rows


def _begin_device_code(session: InteractiveSession) -> dict[str, Any]:
    auth = session.rendered_auth
    endpoint = str(auth.get("device_authorization_endpoint") or "").strip()
    client_id = str(auth.get("client_id") or "").strip()
    if not endpoint:
        raise ValueError("This server did not advertise a device authorization endpoint")
    if not client_id:
        raise ValueError("Device code needs a client_id")
    if not str(auth.get("token_endpoint") or "").strip():
        raise ValueError("Device code needs a token endpoint")
    _require_routable(endpoint)
    body: dict[str, Any] = {
        "client_id": client_id,
        "scope": str(auth.get("scope") or "openid profile offline_access"),
    }
    audience = str(auth.get("audience") or "").strip()
    if audience:
        body["audience"] = audience
    # Nimbus DeviceAuthorizationRequest.getClientID() is null when the request
    # includes client authentication. This server then calls getValue() on it.
    # Identify the client with client_id in the body and authenticate at the token endpoint.
    payload, response = _post_form(
        session,
        endpoint,
        body,
        {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    if response.status_code >= 400 or not isinstance(payload, dict):
        raise ValueError(_http_error(response, payload))
    device_code = str(payload.get("device_code") or "").strip()
    user_code = str(payload.get("user_code") or "").strip()
    if not device_code or not user_code:
        raise ValueError("Device authorization response did not include device_code and user_code")
    verification = _absolute_verification(
        str(payload.get("verification_uri") or ""),
        str(auth.get("issuer") or session.base_url or endpoint),
    )
    complete = _absolute_verification(
        str(payload.get("verification_uri_complete") or ""),
        str(auth.get("issuer") or session.base_url or endpoint),
    )
    interval = payload.get("interval") or payload.get("interval_seconds") or 5
    try:
        session.poll_interval = max(float(interval), 1)
    except (TypeError, ValueError):
        session.poll_interval = 5
    expires = payload.get("expires_in") or 600
    try:
        session.expires_at = time.time() + max(int(expires), 1)
    except (TypeError, ValueError):
        session.expires_at = time.time() + 600
    session.device_code = device_code
    session.next_poll_at = time.monotonic() + session.poll_interval
    return {
        "status": "awaiting_user",
        "session_id": session.id,
        "flow": "device_code",
        "user_code": user_code,
        "verification_uri": verification,
        "verification_uri_complete": complete or verification,
        "interval_seconds": session.poll_interval,
        "expires_in": int(session.expires_at - time.time()),
        "message": "Open the verification page and enter the code.",
    }


def _begin_authorization_code(session: InteractiveSession) -> dict[str, Any]:
    auth = session.rendered_auth
    endpoint = str(auth.get("authorization_endpoint") or "").strip()
    token_endpoint = str(auth.get("token_endpoint") or "").strip()
    client_id = str(auth.get("client_id") or "").strip()
    redirect_uri = str(auth.get("redirect_uri") or "").strip()
    if not endpoint:
        raise ValueError("Authorization code needs an authorization endpoint")
    if not token_endpoint:
        raise ValueError("Authorization code needs a token endpoint")
    if not client_id:
        raise ValueError("Authorization code needs a client_id")
    if not redirect_uri:
        raise ValueError("Set redirect_uri to this RunWay app's /scenario-auth/callback")
    _require_routable(endpoint)
    pkce = generate_pkce()
    state = secrets.token_urlsafe(24)
    query: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": str(auth.get("scope") or "openid profile offline_access"),
        "state": state,
        "code_challenge": pkce["challenge"],
        "code_challenge_method": pkce["method"],
    }
    audience = str(auth.get("audience") or "").strip()
    if audience:
        query["audience"] = audience
    separator = "&" if "?" in endpoint else "?"
    session.auth_state = state
    session.code_verifier = pkce["verifier"]
    session.redirect_uri = redirect_uri
    session.expires_at = time.time() + 600
    session.poll_interval = 1
    return {
        "status": "awaiting_user",
        "session_id": session.id,
        "flow": "authorization_code",
        "authorization_url": f"{endpoint}{separator}{urlencode(query)}",
        "redirect_uri": redirect_uri,
        "interval_seconds": 1,
        "message": "Finish sign-in in the window that opens, then return here.",
    }


def _exchange_token(session: InteractiveSession) -> tuple[Any, requests.Response]:
    auth = session.rendered_auth
    token_endpoint = str(auth.get("token_endpoint") or "").strip()
    if not token_endpoint:
        raise ValueError("Token endpoint is missing")
    _require_routable(token_endpoint)
    client_id = str(auth.get("client_id") or "")
    if session.flow == "device_code":
        body = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": session.device_code,
            "client_id": client_id,
        }
    else:
        body = {
            "grant_type": "authorization_code",
            "code": session.authorization_code,
            "redirect_uri": session.redirect_uri,
            "client_id": client_id,
            "code_verifier": session.code_verifier,
        }
    return _post_form(session, token_endpoint, body, _auth_headers(auth))


def _apply_token_response(session: InteractiveSession, payload: dict[str, Any], response: requests.Response) -> None:
    step = dict(session.user_auth_step or {})
    if not step.get("save"):
        step["save"] = {
            "user_access_token": "{{ json.access_token }}",
            "user_refresh_token": "{{ json.refresh_token }}",
            "user_id_token": "{{ json.id_token }}",
        }
    context = session.context or {"vars": {}}
    apply_save(step, payload, response, context, final_url=response.url, body_text=response.text)
    _record_step(session, step, True, False, 0.0, payload, response)


def _fail_user_auth(session: InteractiveSession, message: str) -> dict[str, Any]:
    step = session.user_auth_step or {"name": "user_authentication", "method": "USER_AUTH"}
    _record_step(session, step, False, False, 0.0, message, None)
    session.phase = "running"
    return _finish(session)


def _auth_headers(auth: dict[str, Any]) -> dict[str, str]:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    client_id = str(auth.get("client_id") or "")
    client_secret = str(auth.get("client_secret") or "")
    if client_id and client_secret:
        headers["Authorization"] = basic_auth_header(client_id, client_secret)
    return headers


def _post_form(
    session: InteractiveSession,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
) -> tuple[Any, requests.Response]:
    timeout = 30.0
    step = session.user_auth_step or {}
    if step.get("timeout"):
        timeout = float(step.get("timeout") or timeout)
    env = session.context.get("env") if isinstance(session.context, dict) else None
    configure_http_session(session.http, env if isinstance(env, dict) else None)
    response = safe_session_request(
        session.http,
        method="POST",
        url=url,
        headers=headers,
        data=form_encode(body),
        timeout=timeout,
        allow_redirects=False,
    )
    return _read_body(response), response


def _read_body(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"response": response.text}


def _http_error(response: requests.Response, payload: Any) -> str:
    if isinstance(payload, dict):
        detail = payload.get("error_description") or payload.get("error") or payload.get("message")
        if detail:
            return f"HTTP {response.status_code}: {detail}"
    text = " ".join((response.text or "").split())[:240]
    return f"HTTP {response.status_code}: {text}" if text else f"HTTP {response.status_code}"


def _require_routable(url: str) -> None:
    ensure_safe_request_url(url)


def _absolute_verification(uri: str, issuer_or_base: str) -> str:
    value = str(uri or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    base = str(issuer_or_base or "").strip()
    if not base:
        return value
    parsed = urlparse(base)
    if parsed.scheme and parsed.netloc:
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return urljoin(origin + "/", value.lstrip("/"))
    return urljoin(base.rstrip("/") + "/", value.lstrip("/"))


def _record_step(
    session: InteractiveSession,
    step: dict[str, Any],
    success: bool,
    expected_mismatch: bool,
    latency_ms: float,
    response_json: Any,
    response: requests.Response | None,
) -> None:
    while len(session.member_stats) <= session.member_index:
        session.member_stats.append({})
    stats = session.member_stats[session.member_index]
    name = str(step.get("name") or f"{step.get('method', 'GET')} {step.get('path', '/')}")
    method = str(step.get("method", "GET")).upper()
    path = str(step.get("path") or step.get("url") or method)
    last_status = response.status_code if response is not None else None
    last_error = None
    if not success:
        if response is not None:
            snippet = " ".join((response.text or "").split())[:240]
            last_error = f"HTTP {response.status_code}: {snippet}" if snippet else f"HTTP {response.status_code}"
        elif isinstance(response_json, str) and response_json:
            last_error = response_json
        else:
            last_error = "request failed"
    if name not in stats:
        stats[name] = StepStats(name=name, method=method, path=path)
    stats[name].add(
        latency_ms=latency_ms,
        success=success,
        expected_mismatch=expected_mismatch,
        last_status=last_status,
        last_error=last_error,
    )


def _finish(session: InteractiveSession) -> dict[str, Any]:
    session.phase = "done"
    elapsed = max(time.time() - session.started, 0.001)
    child_results: list[dict[str, Any]] = []
    for index, (filename, child) in enumerate(session.members):
        if index >= len(session.member_stats):
            break
        stats = session.member_stats[index]
        if not stats and index > session.member_index:
            break
        step_results = [item.as_dict(elapsed) for item in stats.values()]
        total_count = sum(item["count"] for item in step_results)
        total_success = sum(item["success"] for item in step_results)
        total_failure = sum(item["failure"] for item in step_results)
        total_mismatch = sum(item["expected_mismatch"] for item in step_results)
        child_results.append(
            {
                "file": filename,
                "name": child.get("name", Path(filename).stem),
                "base_url": session.base_url,
                "environment": session.environment_name,
                "users": 1,
                "duration_s": round(elapsed, 3),
                "iterations_per_user": 1,
                "steps": step_results,
                "totals": {
                    "requests": total_count,
                    "success": total_success,
                    "failure": total_failure,
                    "expected_mismatch": total_mismatch,
                    "rps": total_count / elapsed if elapsed > 0 else 0,
                    "success_rate": (total_success / total_count) if total_count else 0,
                },
            }
        )
    if session.collection is not None:
        output = _collection_output(session, child_results, elapsed)
    else:
        output = child_results[0] if child_results else {
            "steps": [],
            "totals": {"requests": 0, "success": 0, "failure": 1, "expected_mismatch": 0, "rps": 0, "success_rate": 0},
        }
    totals = output.get("totals") or {}
    failed = int(totals.get("failure") or 0) + int(totals.get("expected_mismatch") or 0)
    if session.collection is not None:
        failed = int(totals.get("failed") or 0)
    lines = []
    steps = output.get("steps") or []
    for step in steps:
        mark = "PASS" if not step.get("failure") and not step.get("expected_mismatch") else "FAIL"
        extra = f"  {step.get('last_error')}" if step.get("last_error") else ""
        lines.append(f"{mark} {step.get('name')}{extra}")
    result = {
        "status": "completed" if failed == 0 and int(totals.get("success") or totals.get("passed") or 0) > 0 else "completed_with_errors",
        "summary": {
            "timestamp": now_iso(),
            "target": session.base_url,
            "scenario": output,
        },
        "stdout": "\n".join(lines),
        "stderr": "",
    }
    if failed == 0 and int(totals.get("requests") or 0) == 0 and session.collection is None:
        result["status"] = "completed_with_errors"
    session.result = result
    return result


def _collection_output(session: InteractiveSession, child_results: list[dict[str, Any]], elapsed: float) -> dict[str, Any]:
    total_count = sum(item["totals"]["requests"] for item in child_results)
    total_success = sum(item["totals"]["success"] for item in child_results)
    total_failure = sum(item["totals"]["failure"] for item in child_results)
    total_mismatch = sum(item["totals"]["expected_mismatch"] for item in child_results)
    passed = sum(
        1
        for item in child_results
        if item["totals"]["failure"] == 0 and item["totals"]["expected_mismatch"] == 0 and item["totals"]["success"] > 0
    )
    return {
        "kind": "collection",
        "collection": (session.collection or {}).get("name") or "collection",
        "base_url": session.base_url,
        "environment": session.environment_name,
        "users": 1,
        "duration_s": round(elapsed, 3),
        "iterations_per_user": 1,
        "scenarios": child_results,
        "steps": [
            {**step, "name": f"{item['name']}.{step.get('name', '')}"}
            for item in child_results
            for step in item.get("steps", [])
        ],
        "totals": {
            "requests": total_count,
            "success": total_success,
            "failure": total_failure,
            "expected_mismatch": total_mismatch,
            "rps": total_count / elapsed if elapsed > 0 else 0,
            "success_rate": (total_success / total_count) if total_count else 0,
            "scenarios": len(child_results),
            "passed": passed,
            "failed": len(child_results) - passed,
        },
    }

