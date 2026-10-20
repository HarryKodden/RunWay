"""Convert RunWay collections into Bruno collection JSON.

The output matches the in-memory Bruno collection shape produced by
@usebruno/converters (Import → Bruno Collection in the Bruno app).
"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

_UID_ALPHABET = "useandom26T198340PX75pxJACKVERYMINDBUSHWOLFGQZbfghjklqvwyzrict"
_PLACEHOLDER_RE = re.compile(r"\{\{([^{}]+)\}\}")
_PREFIXES = ("env.", "_.", "vars.", "random.", "meta.")


def bruno_uid(length: int = 21) -> str:
    return "".join(secrets.choice(_UID_ALPHABET) for _ in range(length))


def _safe_filename_stem(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", (name or "").strip()).strip(".-")
    return cleaned or "collection"


def convert_placeholders(value: Any) -> Any:
    """Map Flow Tester placeholders to Bruno `{{name}}` variables."""
    if isinstance(value, dict):
        return {str(key): convert_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [convert_placeholders(item) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        if not inner:
            return "{{}}"
        encode_match = re.match(r"^encode\s*\(\s*(.+)\s*\)$", inner)
        if encode_match:
            inner = encode_match.group(1).strip()
        name_part = inner.split(":", 1)[0].strip()
        for prefix in _PREFIXES:
            if name_part.startswith(prefix):
                name_part = name_part[len(prefix) :].strip()
                break
        name_part = re.sub(r"[^\w.-]+", "_", name_part).strip("._") or "var"
        return "{{" + name_part + "}}"

    return _PLACEHOLDER_RE.sub(replace, value)


def _empty_auth(mode: str = "none") -> dict[str, Any]:
    return {
        "mode": mode,
        "basic": None,
        "bearer": None,
        "awsv4": None,
        "apikey": None,
        "oauth1": None,
        "oauth2": None,
        "digest": None,
        "ntlm": None,
    }


def _empty_body() -> dict[str, Any]:
    return {
        "mode": "none",
        "json": None,
        "text": None,
        "xml": None,
        "formUrlEncoded": [],
        "multipartForm": [],
        "file": [],
    }


def _header_items(headers: Any) -> list[dict[str, Any]]:
    if not isinstance(headers, dict):
        return []
    items: list[dict[str, Any]] = []
    for key, value in headers.items():
        name = str(key or "").strip()
        if not name:
            continue
        items.append(
            {
                "uid": bruno_uid(),
                "name": convert_placeholders(name),
                "value": convert_placeholders("" if value is None else str(value)),
                "description": "",
                "enabled": True,
            }
        )
    return items


def _query_params_from_url(url: str) -> tuple[str, list[dict[str, Any]]]:
    raw = convert_placeholders(url or "")
    if not isinstance(raw, str) or not raw:
        return "", []
    if "{{" in raw:
        # Keep templated URLs intact; Bruno accepts query strings in the URL.
        return raw, []
    parts = urlsplit(raw)
    if not parts.query:
        return raw, []
    params = [
        {
            "uid": bruno_uid(),
            "name": name,
            "value": value,
            "description": "",
            "type": "query",
            "enabled": True,
        }
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    base = raw.split("?", 1)[0]
    return base, params


def _apply_auth(step: dict[str, Any], request: dict[str, Any]) -> None:
    auth = step.get("auth")
    if not isinstance(auth, dict):
        return
    auth_type = str(auth.get("type") or "").strip().lower()
    if auth_type == "basic":
        request["auth"] = _empty_auth("basic")
        request["auth"]["basic"] = {
            "username": convert_placeholders(str(auth.get("username") or "")),
            "password": convert_placeholders(str(auth.get("password") or "")),
        }
    elif auth_type == "bearer":
        token = auth.get("token") or auth.get("value") or ""
        request["auth"] = _empty_auth("bearer")
        request["auth"]["bearer"] = {"token": convert_placeholders(str(token))}


def _apply_body(step: dict[str, Any], request: dict[str, Any]) -> None:
    body = _empty_body()
    if "json" in step and step.get("json") is not None:
        payload = convert_placeholders(step.get("json"))
        body["mode"] = "json"
        body["json"] = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
        request["body"] = body
        return

    data = step.get("data")
    if data is None:
        request["body"] = body
        return

    converted = convert_placeholders(data)
    if isinstance(converted, dict):
        body["mode"] = "formUrlEncoded"
        body["formUrlEncoded"] = [
            {
                "uid": bruno_uid(),
                "name": str(key),
                "value": "" if value is None else str(value),
                "description": "",
                "enabled": True,
            }
            for key, value in converted.items()
        ]
    else:
        body["mode"] = "text"
        body["text"] = "" if converted is None else str(converted)
    request["body"] = body


def step_to_bruno_item(step: dict[str, Any], seq: int) -> dict[str, Any]:
    name = str(step.get("name") or f"step_{seq}").strip() or f"step_{seq}"
    method = str(step.get("method") or "GET").upper() or "GET"
    raw_url = str(step.get("path") or step.get("url") or "").strip() or "/"
    url, params = _query_params_from_url(raw_url)

    request: dict[str, Any] = {
        "url": url,
        "method": method,
        "auth": _empty_auth("inherit"),
        "headers": _header_items(step.get("headers")),
        "params": params,
        "body": _empty_body(),
        "docs": str(step.get("description") or ""),
    }
    _apply_auth(step, request)
    _apply_body(step, request)

    item: dict[str, Any] = {
        "uid": bruno_uid(),
        "name": name,
        "type": "http-request",
        "seq": seq,
        "request": request,
        "settings": {
            "encodeUrl": True,
            "followRedirects": bool(step.get("follow_redirects", False)),
            "maxRedirects": int(step.get("max_redirects", 5) or 5),
        },
    }
    expected = step.get("expected_status")
    if expected is not None and str(expected).strip() != "":
        item["request"]["assertions"] = [
            {
                "uid": bruno_uid(),
                "name": "res.status",
                "value": f"eq {expected}",
                "enabled": True,
            }
        ]
    return item


def scenario_to_bruno_folder(name: str, scenario: dict[str, Any], seq: int) -> dict[str, Any]:
    fallback = Path(str(name or "")).stem or f"scenario_{seq}"
    display = str(scenario.get("name") or fallback).strip() or fallback
    steps = scenario.get("steps") if isinstance(scenario.get("steps"), list) else []
    items = [
        step_to_bruno_item(step, index)
        for index, step in enumerate(steps, start=1)
        if isinstance(step, dict)
    ]
    return {
        "uid": bruno_uid(),
        "name": display,
        "type": "folder",
        "seq": seq,
        "items": items,
        "root": {
            "docs": str(scenario.get("description") or ""),
            "meta": {"name": display},
            "request": {
                "auth": _empty_auth("inherit"),
                "headers": [],
                "script": {},
                "tests": "",
                "vars": {},
            },
        },
    }


def environments_to_bruno(environments: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(environments, dict):
        return []
    result: list[dict[str, Any]] = []
    for env_name, values in environments.items():
        name = str(env_name or "").strip() or "Environment"
        variables: list[dict[str, Any]] = []
        if isinstance(values, dict):
            for key, value in values.items():
                var_name = str(key or "").strip()
                if not var_name:
                    continue
                variables.append(
                    {
                        "uid": bruno_uid(),
                        "name": re.sub(r"[^\w.-]+", "_", var_name).strip("._") or "var",
                        "value": convert_placeholders("" if value is None else str(value)),
                        "enabled": True,
                        "type": "text",
                        "secret": False,
                    }
                )
        result.append({"uid": bruno_uid(), "name": name, "variables": variables})
    return result


def collection_to_bruno(
    collection: dict[str, Any],
    scenarios: list[tuple[str, dict[str, Any]]],
    *,
    environments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    name = str(collection.get("name") or "Untitled Collection").strip() or "Untitled Collection"
    docs = str(collection.get("description") or "")
    env_map = environments if isinstance(environments, dict) else collection.get("environments")
    items: list[dict[str, Any]] = []
    for index, (member_name, document) in enumerate(scenarios, start=1):
        if not isinstance(document, dict):
            document = {"steps": []}
        items.append(scenario_to_bruno_folder(member_name, document, index))

    # Collections that are actually single-scenario docs with inline steps.
    inline_steps = collection.get("steps") if isinstance(collection.get("steps"), list) else []
    if not items and inline_steps:
        items.append(
            scenario_to_bruno_folder(
                name,
                {"name": name, "description": docs, "steps": inline_steps},
                1,
            )
        )

    return {
        "uid": bruno_uid(),
        "name": name,
        "version": "1",
        "items": items,
        "environments": environments_to_bruno(env_map if isinstance(env_map, dict) else {}),
        "root": {
            "docs": docs,
            "meta": {"name": name},
            "request": {
                "auth": _empty_auth("none"),
                "headers": [],
                "script": {},
                "tests": "",
                "vars": {},
            },
        },
    }


def bruno_export_filename(collection_name: str) -> str:
    return f"{_safe_filename_stem(collection_name)}.bruno.json"
