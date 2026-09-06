#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi import Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from tools.bruno_import import bruno_suggested_name, convert_bruno_collection, is_bruno_collection
from tools.curl_import import parse_curl_command
from tools.openapi_import import convert_openapi_document, is_openapi_document, openapi_suggested_name
from tools.scenario_runner import (
    apply_save,
    apply_collection_defaults,
    discover_parent_collection,
    apply_env_overrides,
    environment_name_as_base_url,
    expand_environment_values,
    expand_meta_placeholders,
    expand_placeholder_defaults,
    finalize_environment_values,
    missing_environment_dependencies,
    preview_step_request,
    require_routable_api_targets,
    is_forbidden_api_host,
    parse_target_hostname,
    ROUTABLE_HOST_HELP,
    run_step,
)
from tools.oauth_helpers import stop_cimd_metadata_servers
from authlib.integrations.base_client.errors import MismatchingStateError, OAuthError

from webapp.auth import (
    OIDC_REDIRECT_URI,
    SESSION_SECRET,
    attach_oidc_state_cookie,
    clear_oidc_state_cookie,
    complete_oidc_login,
    configure_oauth,
    current_user_optional,
    current_user_required,
    get_or_create_user,
    login_on_callback_host,
    oauth,
    oidc_enabled,
    public_user,
    session_https_only,
)
from webapp.db import get_db, run_migrations
from webapp.models import User
from webapp.explorer import router as explorer_router
from webapp.explorer import read_order, shared_with_me_tree, sort_named, workspace_tree
from webapp.workspace import (
    LIBRARY_READONLY,
    accessible_collection,
    attach_scenario,
    is_collection_filename,
    is_workspace_path,
    load_collection_env_values,
    materialize_workspace_run,
    parse_workspace_path,
    router as workspace_router,
    unique_scenario_name,
    workspace_collection_path,
    workspace_scenario_path,
)

try:
    import yaml
except Exception:  # pragma: no cover - optional import guarded at runtime
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = ROOT / "examples"
BIN_DIR = ROOT / "bin"

EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
configure_oauth()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    run_migrations()
    yield

NO_STORE_HEADERS = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s\"']+)")


def mask_sensitive_text(text: str) -> str:
    if not text:
        return text
    return _BEARER_RE.sub(lambda match: match.group(1) + "***", text)


def _json(content: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=content, status_code=status_code, headers=NO_STORE_HEADERS)


RELEASE = (os.environ.get("RELEASE") or "dev").strip() or "dev"


class NoStoreAPICacheMiddleware:
    """Add no-store headers without BaseHTTPMiddleware, which can drop Set-Cookie."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start" and (path == "/" or path.startswith("/api/")):
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store, max-age=0"
                headers["Pragma"] = "no-cache"
            await send(message)

        await self.app(scope, receive, send_wrapper)


app = FastAPI(title="RunWay", version=RELEASE, lifespan=lifespan)
app.include_router(workspace_router)
app.include_router(explorer_router)
app.add_middleware(NoStoreAPICacheMiddleware)
# Outermost: session cookie must wrap every other middleware or OIDC state is lost.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="aft_session",
    same_site="lax",
    https_only=session_https_only(),
    max_age=14 * 24 * 60 * 60,
)


app.mount("/static", StaticFiles(directory=ROOT / "webapp" / "static"), name="static")
templates = Jinja2Templates(directory=str(ROOT / "webapp" / "templates"))


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "release": RELEASE}


@app.get("/api/me")
def me(user: User | None = Depends(current_user_optional)) -> dict[str, Any]:
    return public_user(user)


@app.get("/api/users")
def list_users(
    user: User = Depends(current_user_required),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.scalars(select(User).where(User.id != user.id).order_by(User.name, User.email, User.id)).all()
    return {
        "users": [
            {
                "id": row.id,
                "name": row.name or "",
                "email": row.email or "",
            }
            for row in rows
        ]
    }


@app.get("/login")
async def login(request: Request):
    if not oidc_enabled():
        raise HTTPException(status_code=400, detail="OIDC is not configured")
    bounce = login_on_callback_host(request)
    if bounce is not None:
        return bounce
    response = await oauth.oidc.authorize_redirect(request, OIDC_REDIRECT_URI)
    return attach_oidc_state_cookie(response, request)


@app.get("/auth/callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    if not oidc_enabled():
        raise HTTPException(status_code=400, detail="OIDC is not configured")
    try:
        claims = await complete_oidc_login(request)
    except MismatchingStateError as exc:
        raise HTTPException(
            status_code=400,
            detail="Sign-in session expired or the browser opened a different host. Open http://127.0.0.1:9011 and try Sign in again.",
        ) from exc
    except OAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user = get_or_create_user(
        db,
        issuer=str(claims.get("iss") or os.environ.get("OIDC_ISSUER") or "").strip(),
        sub=str(claims.get("sub") or "").strip(),
        email=str(claims.get("email") or "") or None,
        name=str(claims.get("name") or claims.get("preferred_username") or "") or None,
    )
    request.session["user_id"] = user.id
    response = RedirectResponse(url="/", status_code=302)
    return clear_oidc_state_cookie(response)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=302)


def _normalize_examples_rel(relative: str, *, allow_empty: bool = False) -> str:
    raw = (relative or "").strip().replace("\\", "/")
    if not raw:
        if allow_empty:
            return ""
        raise HTTPException(status_code=400, detail="Path is required")

    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or part.startswith("."):
            raise HTTPException(status_code=400, detail="Invalid path")
        parts.append(part)

    if not parts:
        if allow_empty:
            return ""
        raise HTTPException(status_code=400, detail="Invalid path")
    return "/".join(parts)


def _resolve_examples_path(relative: str, *, allow_empty: bool = False) -> Path:
    rel = _normalize_examples_rel(relative, allow_empty=allow_empty)
    base = EXAMPLES_DIR.resolve()
    path = (base / rel).resolve() if rel else base
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc
    return path


def _scenario_file_node(path: Path, relative: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    members = data.get("scenarios")
    steps = data.get("steps") or []
    is_collection = is_collection_filename(path.name) or (
        isinstance(members, list) and len(members) > 0 and len(steps) == 0
    )
    member_names = [name for name in members if isinstance(name, str) and name.strip()] if isinstance(members, list) else []
    return {
        "type": "file",
        "name": path.name,
        "path": relative.replace("\\", "/"),
        "base_url": data.get("base_url"),
        "kind": "collection" if is_collection else "scenario",
        "step_count": len(steps) if isinstance(steps, list) else 0,
        "member_count": len(member_names) if is_collection else 0,
        "members": member_names if is_collection else [],
    }


def _build_examples_tree(directory: Path, relative: str) -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    entries = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    for entry in entries:
        if entry.name.startswith("."):
            continue
        child_rel = f"{relative}/{entry.name}" if relative else entry.name
        if entry.is_dir():
            children.append(_build_examples_tree(entry, child_rel))
        elif entry.suffix.lower() == ".json":
            children.append(_scenario_file_node(entry, child_rel))
    for child in children:
        child["source"] = "library"
        child["order_key"] = Path(child.get("path") or child.get("name") or "").name
    children = sort_named(children, read_order(directory))
    return {
        "type": "dir",
        "kind": "folder",
        "name": directory.name if relative else "examples",
        "path": relative.replace("\\", "/"),
        "source": "library",
        "children": children,
    }


@app.get("/api/scenarios")
def list_scenarios(
    user: User | None = Depends(current_user_optional),
    db: Session = Depends(get_db),
) -> JSONResponse:
    tree = _build_examples_tree(EXAMPLES_DIR, "")
    public = {
        "type": "dir",
        "kind": "folder",
        "name": "Public library",
        "path": "examples",
        "source": "library",
        "children": tree.get("children") or [],
    }
    children = [public]
    if user is not None:
        children.append(shared_with_me_tree(user, db))
        children.append(workspace_tree(user, db))
    tree["name"] = "Library"
    tree["children"] = children
    return _json(tree)


@app.get("/api/scenarios/parent-collection")
def parent_collection(path: str = Query(..., min_length=1)) -> JSONResponse:
    file_path = _resolve_examples_path(path)
    if not file_path.is_file() or file_path.suffix.lower() != ".json":
        raise HTTPException(status_code=404, detail="Scenario not found")
    try:
        scenario = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid scenario JSON: {exc}") from exc
    if not isinstance(scenario, dict):
        return _json({"status": "none"})
    collection = discover_parent_collection(file_path, scenario)
    if not collection:
        return _json({"status": "none"})
    collection_name = str(collection.get("_file") or "")
    parent_rel = str(file_path.parent.relative_to(EXAMPLES_DIR)).replace("\\", "/")
    if parent_rel in {"", "."}:
        relative = collection_name
    else:
        relative = f"{parent_rel}/{collection_name}" if collection_name else parent_rel
    return _json({
        "status": "ok",
        "path": relative,
        "name": collection.get("name"),
        "description": collection.get("description") or "",
        "selected_environment": collection.get("selected_environment") or "",
        "environments": collection.get("environments") if isinstance(collection.get("environments"), dict) else {},
        "random_generators": collection.get("random_generators") if isinstance(collection.get("random_generators"), dict) else {},
        "scenarios": collection.get("scenarios") if isinstance(collection.get("scenarios"), list) else [],
    })


@app.get("/api/scenarios/file")
def get_scenario(path: str = Query(..., min_length=1)) -> JSONResponse:
    file_path = _resolve_examples_path(path)
    if not file_path.is_file() or file_path.suffix.lower() != ".json":
        raise HTTPException(status_code=404, detail="Scenario not found")
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid scenario JSON: {exc}") from exc
    return _json(payload)


@app.post("/api/scenarios/file")
def save_scenario(payload: dict[str, Any], path: str = Query(..., min_length=1)) -> dict[str, str]:
    if is_workspace_path(path):
        raise HTTPException(status_code=400, detail="Use /api/workspace/file for workspace documents")
    raise HTTPException(status_code=403, detail=LIBRARY_READONLY)


@app.post("/api/scenarios/folders")
def create_scenario_folder(payload: dict[str, Any]) -> dict[str, str]:
    raise HTTPException(status_code=403, detail=LIBRARY_READONLY)


def _safe_scenario_rel_path(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    parts: list[str] = []
    for part in raw.split("/"):
        cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", part.strip())
        cleaned = cleaned.strip("._")
        if cleaned:
            parts.append(cleaned)
    if not parts:
        parts = [f"scenario_import_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"]
    if not parts[-1].endswith(".json"):
        parts[-1] += ".json"
    return "/".join(parts)


def _unique_scenario_name(base_name: str) -> str:
    candidate = _safe_scenario_rel_path(base_name)
    path = EXAMPLES_DIR / candidate
    if not path.exists():
        return candidate

    parent = str(Path(candidate).parent).replace("\\", "/")
    if parent == ".":
        parent = ""
    stem = Path(candidate).stem
    for idx in range(2, 1000):
        next_name = f"{stem}_{idx}.json"
        rel = f"{parent}/{next_name}" if parent else next_name
        if not (EXAMPLES_DIR / rel).exists():
            return rel
    raise HTTPException(status_code=500, detail="Failed to allocate a unique scenario name")


def _to_path_from_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    return path


def _extract_postman_url(request_url: Any) -> tuple[str | None, str]:
    if isinstance(request_url, str):
        if request_url.startswith("http://") or request_url.startswith("https://"):
            parsed = urlparse(request_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else None
            return base_url, _to_path_from_url(request_url)
        variable_prefix = re.match(r"^\s*\{\{[^}]+\}\}(?P<path>/.*)$", request_url)
        if variable_prefix:
            return None, variable_prefix.group("path")
        return None, request_url if request_url.startswith("/") else f"/{request_url}"

    if isinstance(request_url, dict):
        raw = request_url.get("raw")
        if isinstance(raw, str) and raw:
            return _extract_postman_url(raw)

        host = request_url.get("host")
        protocol = request_url.get("protocol") or "http"
        path_chunks = request_url.get("path")
        query_items = request_url.get("query")

        if isinstance(path_chunks, list):
            path_value = "/" + "/".join(str(p).strip("/") for p in path_chunks if str(p))
        else:
            path_value = "/"

        query_str = ""
        if isinstance(query_items, list):
            query_pairs: list[tuple[str, str]] = []
            for query_item in query_items:
                if not isinstance(query_item, dict) or query_item.get("disabled"):
                    continue
                key = str(query_item.get("key", "")).strip()
                value = str(query_item.get("value", "")).strip()
                if key:
                    query_pairs.append((key, value))
            if query_pairs:
                query_str = "?" + "&".join(f"{k}={v}" for k, v in query_pairs)

        base_url = None
        if isinstance(host, list) and host:
            host_text = ".".join(str(h).strip(".") for h in host if str(h).strip())
            if host_text and "{{" not in host_text:
                base_url = f"{protocol}://{host_text}"
        elif isinstance(host, str) and host and "{{" not in host:
            base_url = f"{protocol}://{host}"

        return base_url, f"{path_value}{query_str}"

    return None, "/"


def _extract_postman_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, list):
        return {}
    out: dict[str, str] = {}
    for item in headers:
        if not isinstance(item, dict):
            continue
        if item.get("disabled"):
            continue
        key = str(item.get("key", "")).strip()
        value = str(item.get("value", "")).strip()
        if key:
            out[key] = value
    return out


def _extract_postman_body(body: Any) -> tuple[Any, Any]:
    if not isinstance(body, dict):
        return None, None
    mode = body.get("mode")

    if mode == "raw":
        raw = body.get("raw")
        if not isinstance(raw, str) or not raw.strip():
            return None, None
        text = raw.strip()
        try:
            return json.loads(text), None
        except Exception:
            return None, text

    if mode == "urlencoded":
        encoded = body.get("urlencoded")
        if not isinstance(encoded, list):
            return None, None
        data_out: dict[str, str] = {}
        for item in encoded:
            if not isinstance(item, dict) or item.get("disabled"):
                continue
            key = str(item.get("key", "")).strip()
            value = str(item.get("value", "")).strip()
            if key:
                data_out[key] = value
        return None, data_out or None

    return None, None


def _flatten_postman_items(items: Any) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return flattened
    for item in items:
        if not isinstance(item, dict):
            continue
        request = item.get("request")
        if isinstance(request, dict):
            flattened.append(item)
            continue
        children = item.get("item")
        if isinstance(children, list):
            flattened.extend(_flatten_postman_items(children))
    return flattened


def _extract_postman_variables(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str]:
    variables = payload.get("variable")
    if not isinstance(variables, list):
        return {}, ""

    default_values: dict[str, Any] = {}
    for item in variables:
        if not isinstance(item, dict) or item.get("disabled"):
            continue
        key = str(item.get("key", "")).strip()
        value = item.get("value")
        if key:
            default_values[key] = value

    if not default_values:
        return {}, ""

    return {"Imported Defaults": default_values}, "Imported Defaults"


def _convert_postman_collection(payload: dict[str, Any]) -> dict[str, Any]:
    items = _flatten_postman_items(payload.get("item"))
    steps: list[dict[str, Any]] = []
    inferred_base_url: str | None = None
    environments, selected_environment = _extract_postman_variables(payload)

    for idx, item in enumerate(items, start=1):
        request = item.get("request")
        if not isinstance(request, dict):
            continue

        method = str(request.get("method", "GET")).upper()
        base_url, path = _extract_postman_url(request.get("url"))
        if base_url and not inferred_base_url:
            inferred_base_url = base_url

        step: dict[str, Any] = {
            "name": str(item.get("name") or f"postman_step_{idx}"),
            "method": method,
            "path": path,
        }

        headers = _extract_postman_headers(request.get("header"))
        if headers:
            step["headers"] = headers

        payload_json, payload_data = _extract_postman_body(request.get("body"))
        if payload_json is not None:
            step["json"] = payload_json
        elif payload_data is not None:
            step["data"] = payload_data

        steps.append(step)

    if not steps:
        raise HTTPException(status_code=400, detail="No request items found in Postman collection")

    if not inferred_base_url and selected_environment:
        env_values = environments.get(selected_environment, {})
        inferred_base_url = str(env_values.get("baseUrl") or env_values.get("base_url") or env_values.get("server") or "") or None

    return {
        "base_url": inferred_base_url or "",
        "random_generators": {},
        "environments": environments,
        "selected_environment": selected_environment,
        "steps": steps,
    }


def _insomnia_url_to_parts(url_value: str) -> tuple[str | None, str]:
    if url_value.startswith("http://") or url_value.startswith("https://"):
        parsed = urlparse(url_value)
        base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else None
        return base_url, _to_path_from_url(url_value)

    # Insomnia collections often use variable-prefixed URLs like '{{ _.server }}/Users'.
    variable_prefix = re.match(r"^\s*\{\{[^}]+\}\}(?P<path>/.*)$", url_value)
    if variable_prefix:
        return None, variable_prefix.group("path")

    return None, url_value if url_value.startswith("/") else f"/{url_value}"


def _extract_insomnia_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, list):
        return {}
    header_map: dict[str, str] = {}
    for item in headers:
        if not isinstance(item, dict) or item.get("disabled"):
            continue
        key = str(item.get("name", "")).strip()
        value = str(item.get("value", "")).strip()
        if key:
            header_map[key] = value
    return header_map


def _extract_insomnia_v5_environments(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], str]:
    environments_block = payload.get("environments")
    if not isinstance(environments_block, dict):
        return {}, ""

    base_data = environments_block.get("data") if isinstance(environments_block.get("data"), dict) else {}
    sub_envs = environments_block.get("subEnvironments")
    if not isinstance(sub_envs, list):
        if base_data:
            base_name = str(environments_block.get("name") or "Base Environment")
            return {base_name: dict(base_data)}, base_name
        return {}, ""

    out: dict[str, dict[str, Any]] = {}
    selected_name = ""
    for item in sub_envs:
        if not isinstance(item, dict):
            continue
        env_name = str(item.get("name") or "Environment").strip()
        env_data = item.get("data") if isinstance(item.get("data"), dict) else {}
        merged = dict(base_data)
        merged.update(env_data)
        out[env_name] = merged
        if not selected_name:
            selected_name = env_name

    if not out and base_data:
        base_name = str(environments_block.get("name") or "Base Environment")
        out[base_name] = dict(base_data)
        selected_name = base_name

    return out, selected_name


def _resolve_base_url_from_environment(environment_values: dict[str, Any] | None) -> str | None:
    if not isinstance(environment_values, dict):
        return None
    for key in ("server", "base_url", "baseUrl", "url"):
        value = environment_values.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _convert_insomnia_export(payload: dict[str, Any]) -> dict[str, Any]:
    resources = payload.get("resources")
    if not isinstance(resources, list):
        raise HTTPException(status_code=400, detail="Invalid Insomnia export format")

    steps: list[dict[str, Any]] = []
    inferred_base_url: str | None = None

    for idx, resource in enumerate(resources, start=1):
        if not isinstance(resource, dict):
            continue
        if resource.get("_type") != "request":
            continue

        method = str(resource.get("method", "GET")).upper()
        url_value = str(resource.get("url", "")).strip()
        if not url_value:
            continue

        base_url, path = _insomnia_url_to_parts(url_value)
        if base_url and not inferred_base_url:
            inferred_base_url = base_url

        step: dict[str, Any] = {
            "name": str(resource.get("name") or f"insomnia_step_{idx}"),
            "method": method,
            "path": path,
        }

        header_map = _extract_insomnia_headers(resource.get("headers"))
        if header_map:
            step["headers"] = header_map

        body = resource.get("body")
        if isinstance(body, dict):
            mime_type = str(body.get("mimeType", ""))
            text_value = body.get("text")
            if isinstance(text_value, str) and text_value.strip():
                stripped = text_value.strip()
                if "json" in mime_type:
                    try:
                        step["json"] = json.loads(stripped)
                    except Exception:
                        step["data"] = stripped
                else:
                    step["data"] = stripped

        parameters = resource.get("parameters")
        if isinstance(parameters, list) and "?" not in step["path"]:
            query_pairs: list[tuple[str, str]] = []
            for item in parameters:
                if not isinstance(item, dict) or item.get("disabled"):
                    continue
                key = str(item.get("name", "")).strip()
                value = str(item.get("value", "")).strip()
                if key:
                    query_pairs.append((key, value))
            if query_pairs:
                step["path"] = step["path"] + "?" + "&".join(f"{k}={v}" for k, v in query_pairs)

        steps.append(step)

    if not steps:
        raise HTTPException(status_code=400, detail="No request resources found in Insomnia export")

    return {
        "base_url": inferred_base_url or "",
        "random_generators": {},
        "steps": steps,
    }


def _convert_insomnia_v5_collection(payload: dict[str, Any]) -> dict[str, Any]:
    collection_items = payload.get("collection")
    if not isinstance(collection_items, list):
        raise HTTPException(status_code=400, detail="Invalid Insomnia v5 collection format")

    steps: list[dict[str, Any]] = []
    inferred_base_url: str | None = None
    environments, selected_environment = _extract_insomnia_v5_environments(payload)

    for idx, request_item in enumerate(collection_items, start=1):
        if not isinstance(request_item, dict):
            continue

        method = str(request_item.get("method", "GET")).upper()
        url_value = str(request_item.get("url", "")).strip()
        if not url_value:
            continue

        base_url, path = _insomnia_url_to_parts(url_value)
        if base_url and not inferred_base_url:
            inferred_base_url = base_url

        step: dict[str, Any] = {
            "name": str(request_item.get("name") or f"insomnia_step_{idx}"),
            "method": method,
            "path": path,
        }

        headers = _extract_insomnia_headers(request_item.get("headers"))

        # Convert common bearer auth block to Authorization header.
        authentication = request_item.get("authentication")
        if isinstance(authentication, dict) and authentication.get("type") == "bearer":
            token = str(authentication.get("token", "")).strip()
            if token:
                headers.setdefault("Authorization", f"Bearer {token}")

        if headers:
            step["headers"] = headers

        body = request_item.get("body")
        if isinstance(body, dict):
            mime_type = str(body.get("mimeType", ""))
            text_value = body.get("text")
            if isinstance(text_value, str) and text_value.strip():
                stripped = text_value.strip()
                if "json" in mime_type:
                    try:
                        step["json"] = json.loads(stripped)
                    except Exception:
                        step["data"] = stripped
                else:
                    step["data"] = stripped

        steps.append(step)

    if not steps:
        raise HTTPException(status_code=400, detail="No request entries found in Insomnia v5 collection")

    if not inferred_base_url and selected_environment:
        inferred_base_url = _resolve_base_url_from_environment(environments.get(selected_environment))

    return {
        "base_url": inferred_base_url or "",
        "random_generators": {},
        "environments": environments,
        "selected_environment": selected_environment,
        "steps": steps,
    }


def _get_scenario_environment_values(scenario: dict[str, Any], selected_environment: str | None = None) -> dict[str, Any]:
    environments = scenario.get("environments")
    if not isinstance(environments, dict):
        return {}

    chosen_name = selected_environment or str(scenario.get("selected_environment") or "")
    if chosen_name and isinstance(environments.get(chosen_name), dict):
        return dict(environments[chosen_name])
    return {}


def _resolve_scenario_environment_values(
    scenario: dict[str, Any],
    selected_environment: str | None = None,
) -> dict[str, Any]:
    return _get_scenario_environment_values(scenario, selected_environment)


def _usable_base_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if parse_target_hostname(text) else ""


def _base_url_from_steps(steps: list[dict[str, Any]]) -> str:
    for step in steps:
        raw = expand_placeholder_defaults(str(step.get("path") or step.get("url") or "").strip())
        if raw.startswith("http://") or raw.startswith("https://"):
            parsed = urlparse(raw)
            if parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
    return ""


def _get_scenario_base_url(scenario: dict[str, Any], selected_environment: str | None = None) -> str:
    from tools.scenario_runner import resolve_base_url

    return resolve_base_url(scenario, selected_environment)


def _prepare_step_test(payload: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    scenario = payload.get("scenario")
    if not isinstance(scenario, dict):
        raise HTTPException(status_code=400, detail="scenario is required")
    collection = payload.get("collection")
    if not isinstance(collection, dict):
        collection = payload.get("suite")  # legacy request key
    if not isinstance(collection, dict):
        scenario_path = payload.get("scenario_file") or payload.get("path")
        if isinstance(scenario_path, str) and scenario_path.strip():
            raw_path = Path(scenario_path)
            if not raw_path.is_absolute():
                raw_path = ROOT / scenario_path
            if raw_path.is_file():
                collection = discover_parent_collection(raw_path, scenario)
    if isinstance(collection, dict):
        scenario = apply_collection_defaults(scenario, collection)
    steps = scenario.get("steps", [])
    if not isinstance(steps, list) or not steps:
        raise HTTPException(status_code=400, detail="scenario has no steps")
    selected_environment = payload.get("selected_environment") or scenario.get("selected_environment")
    env_name = str(selected_environment or "").strip()
    payload_base = expand_meta_placeholders(str(payload.get("base_url") or "").strip(), environment=env_name)
    scenario_base = expand_meta_placeholders(
        _get_scenario_base_url(scenario, selected_environment),
        environment=env_name,
    )
    base_url = (
        _usable_base_url(payload_base)
        or _usable_base_url(scenario_base)
        or _base_url_from_steps(steps)
        or environment_name_as_base_url(env_name)
    )
    random_generators = scenario.get("random_generators", {})
    environment_values = _resolve_scenario_environment_values(scenario, selected_environment)
    overrides = payload.get("environment_overrides")
    if isinstance(environment_values, dict):
        if isinstance(overrides, dict):
            environment_values = apply_env_overrides(environment_values, overrides)
        environment_values = finalize_environment_values(
            environment_values,
            base_url=base_url,
            selected_environment=env_name,
        )
        missing = missing_environment_dependencies(environment_values, steps)
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Environment variables need a value: {', '.join(missing)}",
            )
        env_host = _usable_base_url(environment_values.get("server") or environment_values.get("base_url"))
        if env_host:
            base_url = env_host
        if not base_url:
            base_url = _base_url_from_steps(steps)
        try:
            require_routable_api_targets(environment_values, extra_urls=[base_url])
        except SystemExit as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    context = {
        "vars": {"worker_id": 1},
        "env": environment_values,
        "meta": {"environment": env_name},
    }
    return base_url, context, random_generators if isinstance(random_generators, dict) else {}, steps


def _step_test_record(
    step: dict[str, Any],
    index: int,
    success: bool,
    expected_mismatch: bool,
    latency_ms: float,
    response_json: Any,
    response: requests.Response | None,
) -> dict[str, Any]:
    body: Any = None
    status = None
    if response is not None:
        status = response.status_code
        body = response_json if response_json is not None else response.text
    error = None
    if not success:
        if isinstance(body, (dict, list)):
            error = json.dumps(body)[:300]
        elif body:
            error = str(body)[:300]
        else:
            error = "step failed"
    return {
        "index": index,
        "name": step.get("name") or f"step_{index + 1}",
        "method": step.get("method"),
        "path": step.get("path"),
        "success": success,
        "expected_mismatch": expected_mismatch,
        "latency_ms": round(latency_ms, 2),
        "response_status": status,
        "error": error,
    }


def _convert_imported_scenario(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if "item" in payload and isinstance(payload.get("info"), dict):
        suggested = str(payload.get("info", {}).get("name") or "postman_import")
        return suggested, _convert_postman_collection(payload)

    if str(payload.get("type", "")).startswith("collection.insomnia.rest/"):
        suggested = str(payload.get("name") or "insomnia_import")
        return suggested, _convert_insomnia_v5_collection(payload)

    if isinstance(payload.get("resources"), list):
        metadata = payload.get("_meta")
        if isinstance(metadata, dict):
            suggested = str(metadata.get("name") or "insomnia_import")
        else:
            suggested = "insomnia_import"
        return suggested, _convert_insomnia_export(payload)

    if is_bruno_collection(payload):
        try:
            scenario = convert_bruno_collection(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return bruno_suggested_name(payload), scenario

    if is_openapi_document(payload):
        try:
            scenario = convert_openapi_document(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return openapi_suggested_name(payload), scenario

    raise HTTPException(
        status_code=400,
        detail="Unsupported import file. Use Postman, Insomnia, Bruno collection JSON, or OpenAPI/Swagger JSON/YAML.",
    )


def _parse_import_payload(raw_bytes: bytes, filename: str) -> dict[str, Any]:
    text = raw_bytes.decode("utf-8")
    file_lower = filename.lower()

    if file_lower.endswith((".yaml", ".yml")):
        if yaml is None:
            raise HTTPException(status_code=500, detail="YAML import requires PyYAML to be installed")
        try:
            parsed = yaml.safe_load(text)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid YAML import file: {exc}") from exc
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="YAML import must parse to an object")
        return parsed

    # Prefer JSON for .json files and try YAML as a fallback for text that looks YAML-like.
    try:
        parsed_json = json.loads(text)
        if not isinstance(parsed_json, dict):
            raise HTTPException(status_code=400, detail="Import file must parse to an object")
        return parsed_json
    except Exception as json_exc:
        if yaml is None:
            raise HTTPException(status_code=400, detail=f"Invalid JSON import file: {json_exc}") from json_exc
        try:
            parsed_yaml = yaml.safe_load(text)
        except Exception as yaml_exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON/YAML import file: {yaml_exc}") from yaml_exc
        if not isinstance(parsed_yaml, dict):
            raise HTTPException(status_code=400, detail="Import file must parse to an object")
        return parsed_yaml


@app.post("/api/scenarios/import/file")
async def import_scenario(
    request: Request,
    file: UploadFile = File(...),
    scenario_name: str | None = Form(default=None),
    user: User = Depends(current_user_required),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Import file is required")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Import file is empty")

    payload = _parse_import_payload(raw_bytes, file.filename)

    suggested_name, scenario = _convert_imported_scenario(payload)
    if not isinstance(scenario, dict) or not isinstance(scenario.get("steps"), list):
        raise HTTPException(status_code=400, detail="Converted scenario is invalid")

    desired_name = scenario_name.strip() if isinstance(scenario_name, str) else ""
    collection_id = (request.query_params.get("collection_id") or request.query_params.get("suite_id") or "").strip()
    if not collection_id:
        raise HTTPException(
            status_code=400,
            detail="collection_id is required; import into an existing workspace collection",
        )
    collection, _permission = accessible_collection(db, user, collection_id, write=True)
    filename = unique_scenario_name(collection, desired_name or suggested_name)
    saved = attach_scenario(db, user, collection, filename, scenario)
    return {
        "status": "imported",
        "name": workspace_scenario_path(collection.id, saved.name),
        "path": workspace_scenario_path(collection.id, saved.name),
        "collection_id": collection.id,
        "collection_path": workspace_collection_path(collection.id),
        "scenario": scenario,
        "step_count": len(scenario.get("steps", [])),
    }


@app.post("/api/runs")
def start_run(
    payload: dict[str, Any],
    user: User | None = Depends(current_user_optional),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    scenario_file = payload.get("scenario_file")
    if not scenario_file:
        raise HTTPException(status_code=400, detail="scenario_file is required")

    scheme = payload.get("scheme", "http")
    host = str(payload.get("host") or "").strip()
    port = str(payload.get("port") or "").strip()
    if host and is_forbidden_api_host(host):
        raise HTTPException(status_code=400, detail=ROUTABLE_HOST_HELP)
    scenario_users = str(payload.get("scenario_users", 1))
    scenario_duration = str(payload.get("scenario_duration", 60))
    scenario_iterations = str(payload.get("scenario_iterations", 1))
    scenario_environment = str(payload.get("scenario_environment", "") or "")
    regression = bool(payload.get("regression", True))
    stop_cimd_metadata_servers()

    if regression:
        scenario_users = "1"
        scenario_iterations = "1"
        if int(float(scenario_duration or 0)) < 60:
            scenario_duration = "60"

    workspace_run = is_workspace_path(str(scenario_file))
    if workspace_run and user is None:
        raise HTTPException(status_code=401, detail="Sign in required")

    with tempfile.TemporaryDirectory(prefix="lti-run-") as tmp:
        tmp_dir = Path(tmp)
        run_file = str(scenario_file)
        overrides = payload.get("environment_overrides")
        if not isinstance(overrides, dict):
            overrides = {}
        if workspace_run and user is not None:
            run_file = str(materialize_workspace_run(db, user, str(scenario_file), tmp_dir))
            collection_id, _filename = parse_workspace_path(str(scenario_file))
            stored = load_collection_env_values(db, user, collection_id, scenario_environment)
            overrides = {**stored, **{str(k): v for k, v in overrides.items()}}
        cmd = [
            str(BIN_DIR / "run.sh"),
            "--scheme", str(scheme),
            "--scenario-file", run_file,
            "--scenario-users", scenario_users,
            "--scenario-duration", scenario_duration,
            "--scenario-iterations", scenario_iterations,
            "--scenario-only",
            "--output-dir", str(tmp_dir),
            *(["--host", host] if host else []),
            *(["--port", port] if port else []),
            *(["--scenario-environment", scenario_environment] if scenario_environment else []),
        ]
        if regression:
            cmd.append("--regression")

        if isinstance(overrides, dict) and overrides:
            extra_env_path = tmp_dir / "extra-env.json"
            extra_env_path.write_text(json.dumps(overrides), encoding="utf-8")
            cmd.extend(["--scenario-extra-env", str(extra_env_path)])

        # Ensure the subprocess inherits the current venv so python3 resolves correctly
        venv_bin = Path(sys.executable).parent
        env = os.environ.copy()
        env["PATH"] = str(venv_bin) + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(venv_bin.parent)

        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env)
        summary: dict[str, Any] = {}
        summaries = sorted(tmp_dir.glob("*/summary.json"))
        if summaries:
            try:
                loaded = json.loads(summaries[-1].read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    summary = loaded
            except Exception:
                summary = {}

        payload_out = {
            "status": "completed" if proc.returncode == 0 else "completed_with_errors",
            "summary": summary,
            "stdout": mask_sensitive_text(proc.stdout),
            "stderr": mask_sensitive_text(proc.stderr),
        }
        if proc.returncode != 0 and not summary:
            raise HTTPException(status_code=500, detail=payload_out)
        return payload_out


@app.post("/api/test-step")
def test_step(payload: dict[str, Any]) -> dict[str, Any]:
    step_index = payload.get("step_index")
    if not isinstance(step_index, int):
        raise HTTPException(status_code=400, detail="step_index must be an integer")

    base_url, context, random_generators, steps = _prepare_step_test(payload)
    if step_index < 0 or step_index >= len(steps):
        raise HTTPException(status_code=400, detail="step_index out of range")

    session = requests.Session()
    try:
        for prior_index, prior_step in enumerate(steps[:step_index]):
            if not isinstance(prior_step, dict):
                continue
            success, _, _, response_json, response = run_step(
                session=session,
                step=prior_step,
                base_url=base_url,
                context=context,
                random_generators=random_generators,
            )
            if response is not None:
                apply_save(step=prior_step, response_json=response_json, response=response, context=context)
            if not success and prior_step.get("stop_on_failure", False):
                return {
                    "status": "blocked",
                    "message": "A prior step failed and stop_on_failure prevented testing the selected step.",
                    "blocked_by": prior_step.get("name") or f"step_{prior_index + 1}",
                    "base_url": base_url,
                    "context": context,
                }

        step = steps[step_index]
        if not isinstance(step, dict):
            raise HTTPException(status_code=400, detail="selected step is invalid")
        success, expected_mismatch, latency_ms, response_json, response = run_step(
            session=session,
            step=step,
            base_url=base_url,
            context=context,
            random_generators=random_generators,
        )
        if response is not None:
            apply_save(step=step, response_json=response_json, response=response, context=context)

        record = _step_test_record(step, step_index, success, expected_mismatch, latency_ms, response_json, response)
        response_body: Any = None
        response_headers: dict[str, str] = {}
        if response is not None:
            response_headers = dict(response.headers)
            response_body = response_json if response_json is not None else response.text
        return {
            "status": "ok",
            "base_url": base_url,
            "step_name": record["name"],
            "success": success,
            "expected_mismatch": expected_mismatch,
            "latency_ms": record["latency_ms"],
            "response_status": record["response_status"],
            "response_headers": response_headers,
            "response_body": response_body,
            "context": context,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        stop_cimd_metadata_servers()


@app.post("/api/parse-curl")
def parse_curl(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("curl") or payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Paste a curl command")
    try:
        parsed = parse_curl_command(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    parsed["status"] = "ok"
    return parsed


@app.post("/api/preview-step")
def preview_step(payload: dict[str, Any]) -> dict[str, Any]:
    step_index = payload.get("step_index")
    if not isinstance(step_index, int):
        raise HTTPException(status_code=400, detail="step_index must be an integer")
    base_url, context, random_generators, steps = _prepare_step_test(payload)
    extra_vars = payload.get("context_vars")
    if isinstance(extra_vars, dict):
        context.setdefault("vars", {}).update(
            {str(key): value for key, value in extra_vars.items() if str(key) != "worker_id"}
        )
    try:
        preview = preview_step_request(
            steps=steps,
            step_index=step_index,
            base_url=base_url,
            context=context,
            random_generators=random_generators,
            hydrate_prior=bool(payload.get("hydrate_prior", False)),
        )
        preview["status"] = "ok"
        return preview
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        stop_cimd_metadata_servers()


@app.post("/api/test-sequence")
def test_sequence(payload: dict[str, Any]) -> dict[str, Any]:
    base_url, context, random_generators, steps = _prepare_step_test(payload)
    session = requests.Session()
    results: list[dict[str, Any]] = []
    try:
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            success, expected_mismatch, latency_ms, response_json, response = run_step(
                session=session,
                step=step,
                base_url=base_url,
                context=context,
                random_generators=random_generators,
            )
            if response is not None:
                apply_save(step=step, response_json=response_json, response=response, context=context)
            results.append(
                _step_test_record(step, index, success, expected_mismatch, latency_ms, response_json, response)
            )
        passed = sum(1 for item in results if item.get("success"))
        return {
            "status": "ok",
            "base_url": base_url,
            "passed": passed,
            "failed": len(results) - passed,
            "steps": results,
            "context": {"vars": context.get("vars", {})},
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        stop_cimd_metadata_servers()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> Any:
    static_dir = ROOT / "webapp" / "static"
    asset_version = max(
        int((static_dir / "app.js").stat().st_mtime),
        int((static_dir / "app.css").stat().st_mtime),
    )
    response = templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"asset_version": asset_version, "release": RELEASE},
    )
    response.headers.update(NO_STORE_HEADERS)
    return response
