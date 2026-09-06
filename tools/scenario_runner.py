#!/usr/bin/env python3
"""Scenario-based API runner with OAuth helpers (PKCE, DPoP, redirects, poll, exec)."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shlex
import string
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests
from tools.oauth_helpers import (
    absolute_url,
    is_same_origin_url,
    resolve_redirect_url,
    basic_auth_header,
    form_encode,
    generate_pkce,
    get_dpop_key,
    mock_attestation_jwt,
    query_param,
    reset_dpop_key,
    start_cimd_metadata_server,
    stop_cimd_metadata_servers,
)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([^}]+)\s*\}\}")
PLACEHOLDER_NAME_RE = re.compile(r"^(?P<name>[A-Za-z_][\w.]*)(?:\s*:\s*(?P<default>.+))?$")
CONNECTION_ENV_KEYS = {"server", "base_url", "baseUrl", "url", "mock_provider"}
FORBIDDEN_API_HOSTS = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "host.docker.internal",
    "ip6-localhost",
    "ip6-loopback",
})
ROUTABLE_HOST_HELP = "API hosts must be an IP or FQDN, not localhost or host.docker.internal"


def parse_target_hostname(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"http://{text}"
    host = urlparse(text).hostname or ""
    return host.strip("[]").lower().rstrip(".")


def is_forbidden_api_host(host: str | None) -> bool:
    name = (host or "").strip().lower().rstrip(".")
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if not name:
        return False
    if name in FORBIDDEN_API_HOSTS:
        return True
    return name.endswith(".localhost")


def is_forbidden_api_target(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or "{{" in text:
        return False
    return is_forbidden_api_host(parse_target_hostname(text))


def forbidden_api_targets(values: dict[str, Any] | None, extra_urls: list[str] | None = None) -> list[str]:
    found: list[str] = []
    env = values or {}
    for key in CONNECTION_ENV_KEYS:
        if is_forbidden_api_target(env.get(key)):
            found.append(key)
    for url in extra_urls or []:
        if is_forbidden_api_target(url) and "base_url" not in found:
            found.append("base_url")
    return found


def require_routable_api_targets(values: dict[str, Any] | None, extra_urls: list[str] | None = None) -> None:
    bad = forbidden_api_targets(values, extra_urls)
    if bad:
        raise SystemExit(f"{ROUTABLE_HOST_HELP}: {', '.join(bad)}")


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def path_get(data: Any, path: str, default: Any = None) -> Any:
    current = data
    for chunk in path.split("."):
        if chunk == "":
            continue
        if isinstance(current, list):
            try:
                idx = int(chunk)
            except ValueError:
                return default
            if idx < 0 or idx >= len(current):
                return default
            current = current[idx]
            continue
        if isinstance(current, dict):
            if chunk not in current:
                return default
            current = current[chunk]
            continue
        return default
    return current


def value_contains(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, expected_value in expected.items():
            if key not in actual:
                return False
            if not value_contains(actual[key], expected_value):
                return False
        return True
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        if len(expected) > len(actual):
            return False
        for idx, item in enumerate(expected):
            if not value_contains(actual[idx], item):
                return False
        return True
    # Case-insensitive compare for strings (e.g. token_type Bearer vs bearer)
    if isinstance(expected, str) and isinstance(actual, str):
        return actual.lower() == expected.lower()
    return actual == expected


def build_random_value(config: dict[str, Any]) -> Any:
    kind = config.get("type", "string")
    if kind == "uuid":
        return str(uuid.uuid4())
    if kind == "int":
        return random.randint(int(config.get("min", 0)), int(config.get("max", 1000)))
    if kind == "float":
        return round(random.uniform(float(config.get("min", 0)), float(config.get("max", 1))), int(config.get("decimals", 3)))
    if kind == "choice":
        items = config.get("items", [])
        return random.choice(items) if items else ""
    if kind == "email":
        token = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        return f"{config.get('prefix', 'user')}-{token}@{config.get('domain', 'example.org')}"
    if kind == "string":
        length = int(config.get("length", 12))
        alphabet = config.get("alphabet", string.ascii_letters + string.digits)
        return "".join(random.choices(alphabet, k=length))
    return ""


def unquote_placeholder_default(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def parse_placeholder_token(raw: str) -> tuple[str, str | None]:
    """Split `name` or `name : "default"` from a `{{ ... }}` body."""
    text = str(raw or "").strip()
    if not text:
        return "", None
    match = PLACEHOLDER_NAME_RE.match(text)
    if not match:
        return text, None
    default_raw = match.group("default")
    default = None if default_raw is None else unquote_placeholder_default(default_raw)
    return match.group("name"), default


def _is_blank_resolved(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and value.strip() == ""


def resolve_named_token(token: str, context: dict[str, Any], random_generators: dict[str, Any]) -> Any:
    if token.startswith("vars."):
        return path_get(context.get("vars", {}), token[5:], "")
    if token.startswith("env."):
        return path_get(context.get("env", {}), token[4:], "")
    if token.startswith("_."):
        return path_get(context.get("env", {}), token[2:], "")
    if token.startswith("random."):
        generator_config = random_generators.get(token[7:])
        if generator_config is None:
            return ""
        return build_random_value(generator_config)
    if token.startswith("meta."):
        key = token[5:]
        if key == "now":
            return now_iso()
        if key == "unix":
            return int(time.time())
        meta = context.get("meta") if isinstance(context.get("meta"), dict) else {}
        value = meta.get(key)
        return "" if value is None else value
    if token in context.get("env", {}):
        return context["env"][token]
    return ""


def resolve_token(token: str, context: dict[str, Any], random_generators: dict[str, Any]) -> Any:
    name, default = parse_placeholder_token(token)
    resolved = resolve_named_token(name, context, random_generators)
    if _is_blank_resolved(resolved) and default is not None:
        return default
    return resolved


def expand_placeholder_defaults(value: str) -> str:
    """Replace `{{ name : "default" }}` with the default for inspection only."""

    def repl(match: re.Match[str]) -> str:
        _name, default = parse_placeholder_token(match.group(1))
        return default if default is not None else match.group(0)

    return PLACEHOLDER_RE.sub(repl, str(value or ""))


def resolve_base_url(scenario: dict[str, Any], selected_environment: str | None) -> str:
    environments = scenario.get("environments") if isinstance(scenario.get("environments"), dict) else {}
    chosen_name = selected_environment or scenario.get("selected_environment")
    env_values: dict[str, Any] = {}
    if isinstance(chosen_name, str) and isinstance(environments.get(chosen_name), dict):
        env_values = environments[chosen_name]
    env_values = expand_environment_values(dict(env_values), keys=CONNECTION_ENV_KEYS)
    for key in ("server", "base_url", "baseUrl", "url"):
        value = env_values.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    base_url = scenario.get("base_url", "")
    return str(base_url).strip()


def resolve_environment_values(scenario: dict[str, Any], selected_environment: str | None) -> dict[str, Any]:
    environments = scenario.get("environments") if isinstance(scenario.get("environments"), dict) else {}
    chosen_name = selected_environment or scenario.get("selected_environment")
    if isinstance(chosen_name, str) and isinstance(environments.get(chosen_name), dict):
        return dict(environments[chosen_name])
    return {}


def _environment_ref_path(token: str) -> str | None:
    name, _default = parse_placeholder_token(token)
    if name.startswith(("vars.", "random.", "meta.")):
        return None
    if name.startswith("env."):
        return name[4:]
    if name.startswith("_."):
        return name[2:]
    return name or None


def _placeholder_default(token: str) -> str | None:
    _name, default = parse_placeholder_token(token)
    return default


def _value_has_env_placeholders(value: str) -> bool:
    return any(_environment_ref_path(raw) is not None for raw in PLACEHOLDER_RE.findall(value))


def expand_environment_values(
    values: dict[str, Any],
    *,
    keys: set[str] | None = None,
) -> dict[str, Any]:
    """Expand {{ name }} placeholders that refer to other keys in the same environment."""
    if not isinstance(values, dict):
        return {}
    current: Any = dict(values)

    def lookup(path: str) -> Any:
        found = path_get(current, path, None)
        if found is None and path in current:
            return current[path]
        return found

    def expand_str(value: str) -> str:
        def repl(match: re.Match[str]) -> str:
            path = _environment_ref_path(match.group(1))
            default = _placeholder_default(match.group(1))
            if path is None:
                return match.group(0)
            resolved = lookup(path)
            if not _is_concrete_env_value(resolved):
                return default if default is not None else match.group(0)
            text = str(resolved)
            if _value_has_env_placeholders(text):
                return default if default is not None else match.group(0)
            return text

        return PLACEHOLDER_RE.sub(repl, value)

    def walk(obj: Any) -> Any:
        if isinstance(obj, str):
            return expand_str(obj)
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(v) for v in obj]
        return obj

    for _ in range(10):
        if keys is None:
            nxt = walk(current)
        else:
            nxt = dict(current)
            for key in keys:
                if key in nxt:
                    nxt[key] = walk(nxt[key])
        if nxt == current:
            break
        current = nxt
    return current if isinstance(current, dict) else dict(values)


def finalize_environment_values(
    values: dict[str, Any] | None,
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Expand intra-environment placeholders and encoded companion keys."""
    prepared = dict(values or {})
    prepared = expand_environment_values(prepared, keys=CONNECTION_ENV_KEYS)
    resolved = ""
    for key in ("server", "base_url", "baseUrl", "url"):
        value = prepared.get(key)
        if _is_concrete_env_value(value) and not is_forbidden_api_target(value):
            resolved = str(value).strip()
            break
    if not resolved:
        resolved = (base_url or "").strip()
        if resolved and is_forbidden_api_target(resolved):
            resolved = ""
    if resolved:
        prepared["server"] = resolved
        prepared.setdefault("base_url", resolved)
    return apply_encoded_companions(expand_environment_values(prepared))


def apply_encoded_companions(values: dict[str, Any]) -> dict[str, Any]:
    """Set foo_encoded to the URL-encoded form of foo after placeholders expand."""
    applied = dict(values)
    for key, value in list(applied.items()):
        if key.endswith("_encoded") or not isinstance(value, str):
            continue
        encoded_key = f"{key}_encoded"
        if encoded_key not in applied:
            continue
        if not value.strip() or _value_has_env_placeholders(value):
            continue
        applied[encoded_key] = quote(value, safe="")
    return applied


def collect_environment_ref_specs(value: Any, found: dict[str, str | None] | None = None) -> dict[str, str | None]:
    """Map env paths to a default, or None when any use has no default."""
    specs = found if found is not None else {}
    if isinstance(value, str):
        for raw in PLACEHOLDER_RE.findall(value):
            path = _environment_ref_path(raw)
            if not path:
                continue
            default = _placeholder_default(raw)
            if path not in specs:
                specs[path] = default
            elif default is None:
                specs[path] = None
    elif isinstance(value, dict):
        for item in value.values():
            collect_environment_ref_specs(item, specs)
    elif isinstance(value, list):
        for item in value:
            collect_environment_ref_specs(item, specs)
    return specs


def collect_environment_refs(value: Any, found: set[str] | None = None) -> set[str]:
    refs = found if found is not None else set()
    refs.update(path for path, default in collect_environment_ref_specs(value).items() if default is None)
    return refs


def _is_concrete_env_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip()
        return bool(text) and not _value_has_env_placeholders(text)
    if isinstance(value, (dict, list)):
        return len(value) > 0
    return True


def apply_env_overrides(values: dict[str, Any] | None, overrides: dict[str, Any] | None) -> dict[str, Any]:
    applied = dict(values or {})
    for key, value in (overrides or {}).items():
        if not _is_set(value):
            continue
        name = str(key).strip()
        if not name:
            continue
        if "." in name:
            cursor: dict[str, Any] = applied
            parts = name.split(".")
            for part in parts[:-1]:
                next_value = cursor.get(part)
                if not isinstance(next_value, dict):
                    next_value = {}
                    cursor[part] = next_value
                cursor = next_value
            cursor[parts[-1]] = value
        else:
            applied[name] = value
    return applied


SKIP_EXPORTED_VARS = {"worker_id", "exec_error"}


def exported_context_vars(vars_map: dict[str, Any] | None, names: Any) -> dict[str, Any]:
    """Promote selected scenario vars into collection env for later members."""
    source = vars_map if isinstance(vars_map, dict) else {}
    if names is True:
        keys = [str(key) for key in source if str(key) not in SKIP_EXPORTED_VARS]
    elif isinstance(names, list):
        keys = [str(key).strip() for key in names if str(key).strip()]
    else:
        return {}
    exported: dict[str, Any] = {}
    for key in keys:
        value = source.get(key)
        if _is_set(value):
            exported[key] = value
    return exported


def render_expectation_step(
    step: dict[str, Any],
    context: dict[str, Any],
    random_generators: dict[str, Any],
) -> dict[str, Any]:
    rendered = dict(step)
    for key in ("expected_json_contains", "expected_body_contains", "expected_body_not_contains"):
        if key in rendered:
            rendered[key] = render_template(rendered[key], context, random_generators)
    return rendered


def missing_environment_dependencies(*sources: Any) -> list[str]:
    specs: dict[str, str | None] = {}
    values = sources[0] if sources and isinstance(sources[0], dict) else {}
    for source in sources[1:] if sources and isinstance(sources[0], dict) else sources:
        collect_environment_ref_specs(source, specs)
    missing: list[str] = []
    has_host = any(
        _is_concrete_env_value(values.get(key)) and not is_forbidden_api_target(values.get(key))
        for key in CONNECTION_ENV_KEYS
    )
    for path in sorted(specs):
        if specs.get(path) is not None:
            continue
        resolved = path_get(values, path, None)
        if resolved is None and path in values:
            resolved = values[path]
        if _is_concrete_env_value(resolved):
            continue
        if path in CONNECTION_ENV_KEYS and has_host:
            continue
        missing.append(path)
    return missing


def render_template(value: Any, context: dict[str, Any], random_generators: dict[str, Any]) -> Any:
    if isinstance(value, str):
        matches = PLACEHOLDER_RE.findall(value)
        if not matches:
            return value
        stripped = value.strip()
        if stripped.startswith("{{") and stripped.endswith("}}") and len(matches) == 1:
            return resolve_token(matches[0], context, random_generators)

        def replace_match(match: re.Match[str]) -> str:
            replacement = resolve_token(match.group(1), context, random_generators)
            return "" if replacement is None else str(replacement)

        return PLACEHOLDER_RE.sub(replace_match, value)
    if isinstance(value, dict):
        return {k: render_template(v, context, random_generators) for k, v in value.items()}
    if isinstance(value, list):
        return [render_template(v, context, random_generators) for v in value]
    return value


@dataclass
class StepStats:
    name: str
    method: str
    path: str
    count: int = 0
    success: int = 0
    failure: int = 0
    expected_mismatch: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    last_status: int | None = None
    last_error: str | None = None

    def add(
        self,
        latency_ms: float,
        success: bool,
        expected_mismatch: bool,
        last_status: int | None = None,
        last_error: str | None = None,
    ) -> None:
        self.count += 1
        if success:
            self.success += 1
        else:
            self.failure += 1
            if last_status is not None:
                self.last_status = last_status
            if last_error:
                self.last_error = last_error
        if expected_mismatch:
            self.expected_mismatch += 1
        self.latencies_ms.append(latency_ms)

    def as_dict(self, duration_s: float) -> dict[str, Any]:
        sorted_lats = sorted(self.latencies_ms)
        payload = {
            "name": self.name,
            "method": self.method,
            "path": self.path,
            "count": self.count,
            "success": self.success,
            "failure": self.failure,
            "expected_mismatch": self.expected_mismatch,
            "success_rate": (self.success / self.count) if self.count else 0,
            "rps": self.count / duration_s if duration_s > 0 else 0,
            "p50_ms": percentile(sorted_lats, 50),
            "p95_ms": percentile(sorted_lats, 95),
            "p99_ms": percentile(sorted_lats, 99),
        }
        if self.last_status is not None:
            payload["last_status"] = self.last_status
        if self.last_error:
            payload["last_error"] = self.last_error
        return payload


def percentile(sorted_values: list[float], p: int) -> float:
    if not sorted_values:
        return 0.0
    idx = int(round((p / 100) * (len(sorted_values) - 1)))
    return float(sorted_values[idx])


def to_status_list(expected_status: Any) -> list[int]:
    if expected_status is None:
        return []
    if isinstance(expected_status, list):
        return [int(x) for x in expected_status]
    return [int(expected_status)]


def run_prepare(action: dict[str, Any], context: dict[str, Any], random_generators: dict[str, Any], base_url: str) -> None:
    context.setdefault("vars", {})
    kind = action.get("action") or action.get("type")
    action = render_template(action, context, random_generators)

    if kind == "pkce":
        pkce = generate_pkce()
        context["vars"][action.get("save_verifier", "pkce_verifier")] = pkce["verifier"]
        context["vars"][action.get("save_challenge", "pkce_challenge")] = pkce["challenge"]
        context["vars"][action.get("save_method", "pkce_method")] = pkce["method"]
        return

    if kind == "dpop_key":
        key_name = str(action.get("key", "default"))
        if action.get("reset", False):
            key = reset_dpop_key(key_name)
        else:
            key = get_dpop_key(key_name)
        context["vars"][action.get("save_jkt", "dpop_jkt")] = key.jkt
        return

    if kind == "dpop":
        key_name = str(action.get("key", "default"))
        key = get_dpop_key(key_name)
        htm = str(action.get("htm", "POST"))
        htu = str(action.get("htu") or f"{base_url.rstrip('/')}/token")
        nonce = action.get("nonce") or None
        if nonce == "":
            nonce = None
        access_token = action.get("ath_token") or action.get("access_token") or None
        if access_token == "":
            access_token = None
        proof = key.proof(htm=htm, htu=htu, nonce=nonce, access_token=access_token)
        context["vars"][action.get("save_proof", "dpop_proof")] = proof
        context["vars"][action.get("save_jkt", "dpop_jkt")] = key.jkt
        return

    if kind == "basic_auth":
        header = basic_auth_header(str(action.get("username", "")), str(action.get("password", "")))
        context["vars"][action.get("save", "basic_auth")] = header
        return

    if kind == "mock_attestation_jwt":
        jwt = mock_attestation_jwt(
            client_id=str(action.get("client_id", "")),
            audience=str(action.get("audience") or base_url),
        )
        context["vars"][action.get("save", "client_assertion")] = jwt
        return

    if kind == "cimd_metadata":
        port = int(action.get("port", 8099))
        advertise_host = parse_target_hostname(base_url)
        if not advertise_host or is_forbidden_api_host(advertise_host):
            advertise_host = "127.0.0.1"
        metadata = action.get("metadata") or {
            "client_id": f"http://{advertise_host}:{port}/client.json",
            "client_name": "CIMD Loadtest Client",
            "redirect_uris": [f"{base_url}/callback"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "openid profile email",
        }
        url = start_cimd_metadata_server(port, metadata, advertise_host=advertise_host)
        context["vars"][action.get("save_url", "cimd_client_id")] = url
        return

    if kind == "set":
        for key, value in (action.get("vars") or {}).items():
            context["vars"][key] = value
        return

    raise ValueError(f"Unknown prepare action: {kind}")


def apply_save(
    step: dict[str, Any],
    response_json: Any,
    response: requests.Response | None,
    context: dict[str, Any],
    final_url: str | None = None,
    body_text: str | None = None,
) -> None:
    context.setdefault("vars", {})
    save_map = step.get("save", {}) or {}
    for var_name, source in save_map.items():
        source = str(source)
        if source.startswith("json."):
            context["vars"][var_name] = path_get(response_json, source[5:], None)
            continue
        if source.startswith("headers.") and response is not None:
            # Header lookup is case-insensitive via requests
            context["vars"][var_name] = response.headers.get(source[8:])
            continue
        if source == "final_url":
            context["vars"][var_name] = final_url or (response.url if response is not None else None)
            continue
        if source == "status_code" and response is not None:
            context["vars"][var_name] = response.status_code
            continue
        if source == "body" and body_text is not None:
            context["vars"][var_name] = body_text
            continue
        if source.startswith("url_query."):
            url = final_url or (response.url if response is not None else "")
            value = query_param(url or "", source[len("url_query.") :])
            if value or var_name not in context["vars"]:
                context["vars"][var_name] = value
            continue
        if source.startswith("location_query.") and response is not None:
            loc = response.headers.get("X-Final-Location") or response.headers.get("Location", "")
            value = query_param(loc, source[len("location_query.") :])
            if value or var_name not in context["vars"]:
                context["vars"][var_name] = value
            continue
        if source.startswith("body_regex:") and body_text is not None:
            pattern = source[len("body_regex:") :]
            match = re.search(pattern, body_text)
            context["vars"][var_name] = match.group(1) if match and match.lastindex else (match.group(0) if match else None)
            continue
    save_response_as = step.get("save_response_as")
    if save_response_as:
        context["vars"][save_response_as] = response_json


def evaluate_expectations(
    step: dict[str, Any],
    response: requests.Response | None,
    response_json: Any,
    body_text: str,
) -> bool:
    if response is None:
        return False
    expected_status = to_status_list(step.get("expected_status"))
    status_ok = True if not expected_status else response.status_code in expected_status

    json_ok = True
    expected_json_contains = step.get("expected_json_contains")
    if expected_json_contains is not None:
        json_ok = response_json is not None and value_contains(response_json, expected_json_contains)

    body_ok = True
    expected_body_contains = step.get("expected_body_contains")
    if expected_body_contains is not None:
        needles = expected_body_contains if isinstance(expected_body_contains, list) else [expected_body_contains]
        body_ok = all(str(n) in body_text for n in needles)

    not_body = step.get("expected_body_not_contains")
    if not_body is not None:
        needles = not_body if isinstance(not_body, list) else [not_body]
        body_ok = body_ok and all(str(n) not in body_text for n in needles)

    return status_ok and json_ok and body_ok


def smart_follow(
    session: requests.Session,
    method: str,
    url: str,
    headers: dict[str, Any],
    payload_json: Any,
    payload_data: Any,
    timeout: float,
    max_redirects: int,
    stop_hosts: list[str],
) -> tuple[requests.Response | None, str | None, float]:
    """Follow redirects manually; stop before requesting client callback hosts (capture Location)."""
    started = time.perf_counter()
    current_method = method
    current_url = url
    current_headers = dict(headers or {})
    current_json = payload_json
    current_data = payload_data
    last_response: requests.Response | None = None

    for _ in range(max_redirects + 1):
        try:
            last_response = session.request(
                method=current_method,
                url=current_url,
                headers=current_headers,
                json=current_json,
                data=current_data,
                timeout=timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            latency_ms = (time.perf_counter() - started) * 1000
            return last_response, current_url, latency_ms

        if last_response.status_code not in (301, 302, 303, 307, 308):
            latency_ms = (time.perf_counter() - started) * 1000
            return last_response, current_url, latency_ms

        location = last_response.headers.get("Location")
        if not location:
            latency_ms = (time.perf_counter() - started) * 1000
            return last_response, current_url, latency_ms

        next_url = resolve_redirect_url(current_url, location)
        host = urlparse(next_url).hostname or ""
        path = urlparse(next_url).path or ""
        has_auth_code = bool(query_param(next_url, "code"))
        is_client_callback = any(host == h or host.endswith(h) for h in stop_hosts if h)
        is_callback_path = path in {"/callback", "/oauth/callback"} or path.endswith("/callback")
        if is_client_callback or has_auth_code or (is_callback_path and host in {"localhost", "127.0.0.1"}):
            # Synthetic response representing the client-callback redirect
            latency_ms = (time.perf_counter() - started) * 1000
            last_response.headers["X-Final-Location"] = next_url
            return last_response, next_url, latency_ms

        if is_forbidden_api_host(host):
            raise ValueError(f"{ROUTABLE_HOST_HELP}: redirect {next_url}")

        current_url = next_url
        current_method = "GET" if last_response.status_code in (302, 303) else current_method
        current_json = None
        current_data = None

    latency_ms = (time.perf_counter() - started) * 1000
    return last_response, current_url, latency_ms


def collect_placeholders(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.extend(token.strip() for token in PLACEHOLDER_RE.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(collect_placeholders(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(collect_placeholders(item))
    return found


def normalize_prepare(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def prepare_output_keys(action: dict[str, Any]) -> list[str]:
    kind = action.get("action") or action.get("type")
    if kind == "pkce":
        return [
            str(action.get("save_verifier", "pkce_verifier")),
            str(action.get("save_challenge", "pkce_challenge")),
            str(action.get("save_method", "pkce_method")),
        ]
    if kind == "dpop_key":
        return [str(action.get("save_jkt", "dpop_jkt"))]
    if kind == "dpop":
        return [str(action.get("save_proof", "dpop_proof")), str(action.get("save_jkt", "dpop_jkt"))]
    if kind == "basic_auth":
        return [str(action.get("save", "basic_auth"))]
    if kind == "mock_attestation_jwt":
        return [str(action.get("save", "client_assertion"))]
    if kind == "cimd_metadata":
        return [str(action.get("save_url", "cimd_client_id"))]
    if kind == "set":
        return [str(key) for key in (action.get("vars") or {}).keys()]
    return []


def missing_var_tokens(step: dict[str, Any], context: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for key in ("path", "url", "headers", "json", "data", "auth", "command", "exec", "cwd", "prepare"):
        tokens.extend(collect_placeholders(step.get(key)))
    missing: list[str] = []
    vars_map = context.get("vars") if isinstance(context.get("vars"), dict) else {}
    for token in tokens:
        name, default = parse_placeholder_token(token)
        if not name.startswith("vars."):
            continue
        value = path_get(vars_map, name[5:], None)
        if value is None or value == "":
            if default is not None:
                continue
            missing.append(name)
    return missing


def build_http_request(
    step: dict[str, Any],
    base_url: str,
    context: dict[str, Any],
    random_generators: dict[str, Any],
) -> dict[str, Any]:
    method = str(step.get("method", "GET")).upper()
    path = render_template(step.get("path") or step.get("url") or "/", context, random_generators)
    url = absolute_url(base_url, str(path))
    headers = render_template(step.get("headers", {}), context, random_generators) or {}
    if not isinstance(headers, dict):
        headers = {}
    auth = render_template(step.get("auth"), context, random_generators)
    if isinstance(auth, dict) and auth.get("type") == "basic":
        headers["Authorization"] = basic_auth_header(str(auth.get("username", "")), str(auth.get("password", "")))
    payload_json = render_template(step.get("json"), context, random_generators)
    payload_data = form_encode(render_template(step.get("data"), context, random_generators))
    rendered_headers = {str(key): "" if value is None else str(value) for key, value in headers.items()}
    return {
        "method": method,
        "url": url,
        "headers": rendered_headers,
        "json": payload_json,
        "data": payload_data,
    }


def format_curl_command(
    method: str,
    url: str,
    headers: dict[str, Any] | None = None,
    payload_json: Any = None,
    payload_data: Any = None,
    timeout: float | None = None,
) -> str:
    lines = [f"curl -sS -X {method} {shlex.quote(url)}"]
    header_items = headers or {}
    has_content_type = any(str(key).lower() == "content-type" for key in header_items)
    for key, value in header_items.items():
        lines.append(f"  -H {shlex.quote(f'{key}: {value}')}")
    if payload_json is not None:
        if not has_content_type:
            lines.append(f"  -H {shlex.quote('Content-Type: application/json')}")
        body = payload_json if isinstance(payload_json, str) else json.dumps(payload_json, ensure_ascii=False)
        lines.append(f"  --data-raw {shlex.quote(body)}")
    elif payload_data is not None:
        lines.append(f"  --data {shlex.quote(str(payload_data))}")
    if timeout is not None:
        lines.append(f"  --max-time {shlex.quote(str(timeout))}")
    return " \\\n".join(lines)


def run_http_step(
    session: requests.Session,
    step: dict[str, Any],
    base_url: str,
    context: dict[str, Any],
    random_generators: dict[str, Any],
) -> tuple[bool, bool, float, Any, requests.Response | None, str | None, str]:
    request = build_http_request(step, base_url, context, random_generators)
    method = request["method"]
    url = request["url"]
    headers = request["headers"]
    payload_json = request["json"]
    payload_data = request["data"]
    timeout = float(step.get("timeout", 15))
    if is_forbidden_api_target(url):
        return False, False, 0.0, {"error": ROUTABLE_HOST_HELP, "url": url}, None, url, ROUTABLE_HOST_HELP

    follow = step.get("follow_redirects", False)
    max_redirects = int(step.get("max_redirects", 10))
    # Stop smart-redirects on 127.0.0.1 client callbacks only. API hops must
    # use an IP or FQDN; localhost is rejected below.
    stop_hosts = step.get("stop_redirect_hosts") or ["127.0.0.1"]
    if isinstance(stop_hosts, str):
        stop_hosts = [stop_hosts]

    final_url: str | None = None
    response: requests.Response | None = None
    latency_ms = 0.0

    if follow in (True, "smart", "manual"):
        try:
            response, final_url, latency_ms = smart_follow(
                session=session,
                method=method,
                url=url,
                headers=headers,
                payload_json=payload_json,
                payload_data=payload_data,
                timeout=timeout,
                max_redirects=max_redirects,
                stop_hosts=list(stop_hosts),
            )
        except ValueError as exc:
            return False, False, 0.0, {"error": str(exc)}, None, url, str(exc)
    else:
        started = time.perf_counter()
        try:
            response = session.request(
                method=method,
                url=url,
                headers=headers,
                json=payload_json,
                data=payload_data,
                timeout=timeout,
                allow_redirects=False,
            )
            latency_ms = (time.perf_counter() - started) * 1000
            final_url = response.url
            loc = response.headers.get("Location")
            if loc and response.status_code in (301, 302, 303, 307, 308):
                next_url = resolve_redirect_url(url, loc)
                final_url = next_url
                expected = to_status_list(step.get("expected_status"))
                wants_redirect = bool(expected) and response.status_code in expected
                if not wants_redirect and is_same_origin_url(url, next_url):
                    response, final_url, extra_ms = smart_follow(
                        session=session,
                        method="GET" if response.status_code in (302, 303) else method,
                        url=next_url,
                        headers=headers,
                        payload_json=None if response.status_code in (302, 303) else payload_json,
                        payload_data=None if response.status_code in (302, 303) else payload_data,
                        timeout=timeout,
                        max_redirects=max_redirects,
                        stop_hosts=list(stop_hosts),
                    )
                    latency_ms += extra_ms
        except requests.RequestException as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            return False, True, latency_ms, None, None, None, f"{exc.__class__.__name__}: {exc}"

    body_text = response.text if response is not None else ""
    response_json = None
    try:
        if response is not None:
            response_json = response.json()
    except Exception:
        response_json = None

    success = evaluate_expectations(
        render_expectation_step(step, context, random_generators),
        response,
        response_json,
        body_text,
    )
    return success, (not success), latency_ms, response_json, response, final_url, body_text


def run_poll_step(
    session: requests.Session,
    step: dict[str, Any],
    base_url: str,
    context: dict[str, Any],
    random_generators: dict[str, Any],
) -> tuple[bool, bool, float, Any, requests.Response | None, str | None, str]:
    poll = step.get("poll") or {}
    interval = float(poll.get("interval_seconds", 1))
    max_attempts = int(poll.get("max_attempts", 30))
    until_json = render_template(poll.get("until_json_contains"), context, random_generators)
    until_not_error = poll.get("until_not_error")  # e.g. authorization_pending
    total_latency = 0.0
    last: tuple[bool, bool, float, Any, requests.Response | None, str | None, str] | None = None

    for _ in range(max_attempts):
        result = run_http_step(session, step, base_url, context, random_generators)
        last = result
        total_latency += result[2]
        success, _, _, response_json, response, final_url, body_text = result

        done = False
        if until_json is not None and response_json is not None and value_contains(response_json, until_json):
            done = True
        if until_not_error and isinstance(response_json, dict):
            err = response_json.get("error")
            if err != until_not_error and response is not None and response.status_code == 200:
                done = True
            if err != until_not_error and success:
                done = True
        if until_json is None and until_not_error is None and success:
            done = True

        if done:
            # Poll condition met — success regardless of intermediate pending statuses
            return True, False, total_latency, response_json, response, final_url, body_text
        time.sleep(interval)

    if last is None:
        return False, True, total_latency, None, None, None, ""
    # Timed out waiting for poll condition
    _, _, _, response_json, response, final_url, body_text = last
    return False, True, total_latency, response_json, response, final_url, body_text


def run_exec_step(step: dict[str, Any], context: dict[str, Any], random_generators: dict[str, Any]) -> tuple[bool, bool, float]:
    command = render_template(step.get("command") or step.get("exec"), context, random_generators)
    cwd = render_template(step.get("cwd"), context, random_generators)
    env = os.environ.copy()
    extra_env = render_template(step.get("env", {}), context, random_generators) or {}
    if isinstance(extra_env, dict):
        env.update({str(k): str(v) for k, v in extra_env.items()})
    if cwd and not Path(str(cwd)).is_dir() and step.get("skip_if_cwd_missing"):
        context.setdefault("vars", {})
        context["vars"]["exec_skipped"] = f"cwd missing: {cwd}"
        return True, False, 0.0

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command if isinstance(command, list) else str(command),
            shell=not isinstance(command, list),
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=float(step.get("timeout", 300)),
            check=False,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        expected_code = step.get("expected_exit_code", 0)
        ok = completed.returncode == int(expected_code)
        context.setdefault("vars", {})
        context["vars"][step.get("save_stdout", "exec_stdout")] = completed.stdout
        context["vars"][step.get("save_stderr", "exec_stderr")] = completed.stderr
        context["vars"][step.get("save_exit_code", "exec_exit_code")] = completed.returncode
        return ok, (not ok), latency_ms
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        context.setdefault("vars", {})
        context["vars"]["exec_error"] = str(exc)
        return False, True, latency_ms


def run_step(
    session: requests.Session,
    step: dict[str, Any],
    base_url: str,
    context: dict[str, Any],
    random_generators: dict[str, Any],
) -> tuple[bool, bool, float, Any, requests.Response | None]:
    method = str(step.get("method", "GET")).upper()

    def _normalize_prepare(raw: Any) -> list[dict[str, Any]]:
        if raw is None:
            return []
        if isinstance(raw, dict):
            return [raw]
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        return []

    if method == "PREPARE":
        started = time.perf_counter()
        try:
            raw = step.get("prepare") if "prepare" in step else step.get("action")
            for item in _normalize_prepare(raw):
                run_prepare(item, context, random_generators, base_url)
            latency_ms = (time.perf_counter() - started) * 1000
            return True, False, latency_ms, None, None
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            return False, True, latency_ms, str(exc), None

    # Optional inline prepares before HTTP/EXEC/POLL steps (prepare key only)
    for item in _normalize_prepare(step.get("prepare")):
        run_prepare(item, context, random_generators, base_url)

    if method == "EXEC":
        ok, mismatch, latency_ms = run_exec_step(step, context, random_generators)
        return ok, mismatch, latency_ms, None, None

    if method == "SLEEP":
        seconds = float(render_template(step.get("seconds", 1), context, random_generators))
        time.sleep(seconds)
        return True, False, seconds * 1000, None, None

    if step.get("poll"):
        success, mismatch, latency_ms, response_json, response, final_url, body_text = run_poll_step(
            session, step, base_url, context, random_generators
        )
    else:
        success, mismatch, latency_ms, response_json, response, final_url, body_text = run_http_step(
            session, step, base_url, context, random_generators
        )

    if response is not None or final_url or body_text:
        apply_save(step, response_json, response, context, final_url=final_url, body_text=body_text)

    # Optional: on use_dpop_nonce, mint new proof and retry once
    if (
        not success
        and step.get("dpop_nonce_retry")
        and response is not None
        and response.status_code == 400
        and isinstance(response_json, dict)
        and "use_dpop_nonce" in str(response_json.get("error", "")) + str(response_json.get("error_description", ""))
    ):
        nonce = response.headers.get("DPoP-Nonce") or response.headers.get("dpop-nonce")
        if nonce:
            context["vars"]["dpop_nonce"] = nonce
            retry_prepare = step.get("dpop_nonce_retry")
            if isinstance(retry_prepare, dict):
                retry_prepare = {**retry_prepare, "nonce": nonce}
                run_prepare(retry_prepare, context, random_generators, base_url)
            success, mismatch, latency2, response_json, response, final_url, body_text = run_http_step(
                session, step, base_url, context, random_generators
            )
            latency_ms += latency2
            if response is not None:
                apply_save(step, response_json, response, context, final_url=final_url, body_text=body_text)

    return success, mismatch, latency_ms, response_json if response is not None else (body_text or None), response


def preview_step_request(
    steps: list[dict[str, Any]],
    step_index: int,
    base_url: str,
    context: dict[str, Any],
    random_generators: dict[str, Any],
    hydrate_prior: bool = False,
) -> dict[str, Any]:
    if step_index < 0 or step_index >= len(steps):
        raise ValueError("step_index out of range")
    step = steps[step_index]
    if not isinstance(step, dict):
        raise ValueError("selected step is invalid")

    session = requests.Session()
    context.setdefault("vars", {})

    for prior in steps[:step_index]:
        if not isinstance(prior, dict):
            continue
        prior_method = str(prior.get("method", "GET")).upper()
        if prior_method == "PREPARE":
            saved = []
            for item in normalize_prepare(prior.get("prepare") if "prepare" in prior else prior.get("action")):
                saved.extend(prepare_output_keys(item))
            if saved and all(context["vars"].get(key) not in (None, "") for key in saved):
                continue
            run_step(session, prior, base_url, context, random_generators)
            continue
        if prior_method == "SLEEP":
            continue
        if hydrate_prior and missing_var_tokens(step, context):
            run_step(session, prior, base_url, context, random_generators)

    for item in normalize_prepare(step.get("prepare")):
        run_prepare(item, context, random_generators, base_url)

    method = str(step.get("method", "GET")).upper()
    timeout = step.get("timeout")
    request: dict[str, Any] = {
        "method": method,
        "url": None,
        "headers": {},
        "json": None,
        "data": None,
    }
    if method == "PREPARE":
        curl = "# PREPARE (no HTTP request)"
        for key, value in context.get("vars", {}).items():
            if key == "worker_id":
                continue
            curl += f"\n# {key}={value}"
    elif method == "EXEC":
        command = render_template(step.get("command") or step.get("exec"), context, random_generators)
        cwd = render_template(step.get("cwd"), context, random_generators)
        if isinstance(command, list):
            curl = " ".join(shlex.quote(str(part)) for part in command)
        else:
            curl = str(command or "")
        if cwd:
            curl = f"cd {shlex.quote(str(cwd))} && {curl}"
        request["url"] = curl
    elif method == "SLEEP":
        seconds = render_template(step.get("seconds", 1), context, random_generators)
        curl = f"sleep {seconds}"
        request["url"] = curl
    else:
        request = build_http_request(step, base_url, context, random_generators)
        curl = format_curl_command(
            method=request["method"],
            url=request["url"],
            headers=request["headers"],
            payload_json=request["json"],
            payload_data=request["data"],
            timeout=float(timeout) if timeout not in (None, "") else None,
        )

    return {
        "curl": curl,
        "request": request,
        "unresolved": missing_var_tokens(step, context),
        "vars": dict(context.get("vars") or {}),
        "context": {"vars": dict(context.get("vars") or {})},
        "base_url": base_url,
        "step_name": step.get("name") or f"step_{step_index + 1}",
    }


def run_worker(
    worker_id: int,
    base_url: str,
    steps: list[dict[str, Any]],
    random_generators: dict[str, Any],
    environment_values: dict[str, Any],
    selected_environment: str | None,
    run_until: float,
    max_iterations: int,
    aggregate: dict[str, StepStats],
    lock: threading.Lock,
    fail_fast: bool,
    exported_vars: dict[str, Any] | None = None,
) -> None:
    session = requests.Session()
    context = {
        "vars": {"worker_id": worker_id},
        "env": dict(environment_values),
        "meta": {"environment": str(selected_environment or "")},
    }

    def capture_vars() -> None:
        if exported_vars is None:
            return
        with lock:
            exported_vars.clear()
            exported_vars.update(context.get("vars") or {})

    iterations = 0
    while time.time() < run_until and (max_iterations <= 0 or iterations < max_iterations):
        for step in steps:
            if time.time() >= run_until:
                capture_vars()
                return
            success, expected_mismatch, latency_ms, _response_json, _response = run_step(
                session=session,
                step=step,
                base_url=base_url,
                context=context,
                random_generators=random_generators,
            )

            name = step.get("name") or f"{step.get('method', 'GET')} {step.get('path', '/')}"
            method = str(step.get("method", "GET")).upper()
            path = str(step.get("path") or step.get("url") or "/")
            last_status = _response.status_code if _response is not None else None
            last_error = None
            if not success:
                if _response is not None:
                    snippet = " ".join((_response.text or "").split())[:240]
                    last_error = f"HTTP {_response.status_code}: {snippet}" if snippet else f"HTTP {_response.status_code}"
                elif isinstance(_response_json, str) and _response_json:
                    last_error = _response_json
                elif method == "PREPARE":
                    last_error = "prepare failed"
                elif method == "EXEC":
                    last_error = str(context.get("vars", {}).get("exec_error") or "exec failed")
                else:
                    last_error = "request failed"
            with lock:
                if name not in aggregate:
                    aggregate[name] = StepStats(name=name, method=method, path=path)
                aggregate[name].add(
                    latency_ms=latency_ms,
                    success=success,
                    expected_mismatch=expected_mismatch,
                    last_status=last_status,
                    last_error=last_error,
                )

            # stop_on_failure=False means soft/assert-optional: never abort the worker.
            # fail_fast only stops on hard failures (stop_on_failure true/default).
            if not success:
                sof = step.get("stop_on_failure", True)
                if sof is False:
                    pass
                elif sof or fail_fast:
                    capture_vars()
                    return
        iterations += 1
        capture_vars()
    capture_vars()


def is_collection(scenario: dict[str, Any]) -> bool:
    files = scenario.get("scenarios")
    steps = scenario.get("steps") or []
    return isinstance(files, list) and len(files) > 0 and len(steps) == 0


def _is_set(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


def merge_defined(higher: dict[str, Any] | None, lower: dict[str, Any] | None) -> dict[str, Any]:
    """Prefer higher-level values when set; otherwise keep the lower-level value."""
    merged = dict(lower or {})
    for key, value in (higher or {}).items():
        if not _is_set(value):
            continue
        current = merged.get(key)
        if isinstance(value, dict) and not isinstance(value, list) and isinstance(current, dict):
            merged[key] = merge_defined(value, current)
        else:
            merged[key] = value
    return merged


def apply_collection_defaults(child: dict[str, Any], collection: dict[str, Any] | None) -> dict[str, Any]:
    """Collection environments/constants win when set; scenario values fill gaps."""
    if not collection or not isinstance(child, dict):
        return child
    merged = dict(child)
    collection_envs = collection.get("environments") if isinstance(collection.get("environments"), dict) else {}
    child_envs = child.get("environments") if isinstance(child.get("environments"), dict) else {}
    names: list[str] = []
    for name in list(collection_envs) + list(child_envs):
        if name not in names:
            names.append(name)
    if names:
        merged_envs: dict[str, Any] = {}
        for name in names:
            higher = collection_envs.get(name) if isinstance(collection_envs.get(name), dict) else {}
            lower = child_envs.get(name) if isinstance(child_envs.get(name), dict) else {}
            merged_envs[name] = merge_defined(higher, lower)
        merged["environments"] = merged_envs
    collection_consts = collection.get("random_generators") if isinstance(collection.get("random_generators"), dict) else {}
    child_consts = child.get("random_generators") if isinstance(child.get("random_generators"), dict) else {}
    if collection_consts or child_consts:
        merged["random_generators"] = merge_defined(collection_consts, child_consts)
    if _is_set(collection.get("selected_environment")):
        merged["selected_environment"] = collection.get("selected_environment")
    if _is_set(collection.get("base_url")):
        merged["base_url"] = collection.get("base_url")
    return merged


def discover_parent_collection(scenario_path: Path, scenario: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Find the most specific collection in the same folder that lists this scenario."""
    folder = scenario_path.parent
    filename = scenario_path.name
    wanted_env = ""
    if isinstance(scenario, dict):
        wanted_env = str(scenario.get("selected_environment") or "").strip()
    candidates: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
    paths = sorted({*folder.glob("collection*.json"), *folder.glob("suite*.json")})
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or not is_collection(data):
            continue
        members = data.get("scenarios") or []
        if filename not in members:
            continue
        selected = str(data.get("selected_environment") or "").strip()
        env_rank = 0 if wanted_env and selected == wanted_env else 1
        envs = data.get("environments") if isinstance(data.get("environments"), dict) else {}
        env_block = envs.get(selected) or envs.get(wanted_env) or {}
        defined_env = (
            sum(1 for value in env_block.values() if _is_set(value))
            if isinstance(env_block, dict)
            else 0
        )
        # Prefer collections that already supply credentials over provision-only collections.
        candidates.append(((-defined_env, env_rank, len(members), path.name), data))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    data = dict(candidates[0][1])
    data["_file"] = str(candidates[0][0][3])
    return data


def load_collection_members(collection_path: Path, collection: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    members: list[tuple[str, dict[str, Any]]] = []
    for name in collection.get("scenarios") or []:
        if not isinstance(name, str) or not name.strip():
            continue
        child_path = (collection_path.parent / name).resolve()
        if not child_path.is_file():
            raise SystemExit(f"Collection member not found: {name}")
        with child_path.open("r", encoding="utf-8") as fh:
            child = json.load(fh)
        if not isinstance(child, dict):
            raise SystemExit(f"Collection member is not a JSON object: {name}")
        members.append((name, child))
    if not members:
        raise SystemExit("Collection has no scenario files")
    return members


def execute_scenario(
    scenario: dict[str, Any],
    *,
    base_url_override: str | None,
    environment: str | None,
    users: int,
    duration: int,
    iterations: int,
    fail_fast: bool,
    extra_env: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_url = (base_url_override or resolve_base_url(scenario, environment) or "").strip()
    steps = scenario.get("steps", [])
    if not steps:
        raise SystemExit("Scenario has no steps")

    random_generators = scenario.get("random_generators", {})
    environment_values = resolve_environment_values(scenario, environment)
    selected_environment_name = str(
        environment or scenario.get("selected_environment") or ""
    ).strip()
    if extra_env:
        environment_values = apply_env_overrides(environment_values, extra_env)
    if isinstance(environment_values, dict):
        environment_values = finalize_environment_values(environment_values, base_url=base_url)
        missing = missing_environment_dependencies(environment_values, steps)
        if missing:
            raise SystemExit(f"Environment variables need a value: {', '.join(missing)}")
        env_host = environment_values.get("server") or environment_values.get("base_url")
        if isinstance(env_host, str) and env_host.strip():
            base_url = env_host.strip()
        require_routable_api_targets(environment_values, extra_urls=[base_url])

    environment_values.setdefault(
        "oauth2_server_root",
        os.environ.get("OAUTH2_SERVER_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "oauth2-server"))),
    )

    lock = threading.Lock()
    aggregate: dict[str, StepStats] = {}
    captured_vars: dict[str, Any] = {}
    started = time.time()
    run_until = started + max(duration, 1)
    worker_fail_fast = fail_fast or bool(scenario.get("fail_fast", False))

    threads = [
        threading.Thread(
            target=run_worker,
            kwargs={
                "worker_id": i + 1,
                "base_url": base_url,
                "steps": steps,
                "random_generators": random_generators,
                "environment_values": environment_values,
                "selected_environment": selected_environment_name,
                "run_until": run_until,
                "max_iterations": iterations,
                "aggregate": aggregate,
                "lock": lock,
                "fail_fast": worker_fail_fast,
                "exported_vars": captured_vars,
            },
            daemon=True,
        )
        for i in range(max(users, 1))
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=max(duration, 1) + 60)

    ended = time.time()
    elapsed = max(ended - started, 0.001)
    step_results = [stats.as_dict(elapsed) for _, stats in aggregate.items()]
    step_results.sort(key=lambda x: x["name"])
    total_count = sum(item["count"] for item in step_results)
    total_success = sum(item["success"] for item in step_results)
    total_failure = sum(item["failure"] for item in step_results)
    total_mismatch = sum(item["expected_mismatch"] for item in step_results)

    return {
        "base_url": base_url,
        "environment": environment,
        "users": users,
        "duration_s": round(elapsed, 3),
        "iterations_per_user": iterations,
        "steps": step_results,
        "totals": {
            "requests": total_count,
            "success": total_success,
            "failure": total_failure,
            "expected_mismatch": total_mismatch,
            "rps": total_count / elapsed if elapsed > 0 else 0,
            "success_rate": (total_success / total_count) if total_count else 0,
        },
        "vars": dict(captured_vars),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scenario-based API load/compliance runner")
    parser.add_argument("--scenario-file", required=True)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--users", type=int, default=10)
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--iterations", type=int, default=0, help="per worker, 0 means unlimited until duration")
    parser.add_argument("--environment", default=None)
    parser.add_argument("--extra-env-file", default=None, help="JSON object of environment overrides")
    parser.add_argument("--output", required=True)
    parser.add_argument("--fail-fast", action="store_true", help="stop a worker after first failed step")
    parser.add_argument("--strict", action="store_true", help="exit non-zero if any step failure/mismatch occurred")
    args = parser.parse_args()

    scenario_path = Path(args.scenario_file)
    with scenario_path.open("r", encoding="utf-8") as fh:
        scenario = json.load(fh)

    extra_env: dict[str, Any] = {}
    if args.extra_env_file:
        extra_path = Path(args.extra_env_file)
        loaded = json.loads(extra_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise SystemExit("--extra-env-file must contain a JSON object")
        extra_env.update(loaded)

    collection_mode = is_collection(scenario)
    parent_collection = None if collection_mode else discover_parent_collection(scenario_path, scenario)
    members = load_collection_members(scenario_path, scenario) if collection_mode else [(scenario_path.name, scenario)]
    iterations = args.iterations if args.iterations > 0 or not collection_mode else 1
    users = 1 if collection_mode else args.users
    duration = args.duration if args.duration > 0 else 30
    if collection_mode and args.duration <= 30:
        duration = 120
    started = time.time()
    child_results: list[dict[str, Any]] = []
    skip_remaining = ""
    for name, child in members:
        if skip_remaining:
            child_results.append(
                {
                    "file": name,
                    "name": child.get("name", Path(name).stem),
                    "base_url": args.base_url,
                    "environment": args.environment,
                    "users": users,
                    "duration_s": 0,
                    "iterations_per_user": iterations,
                    "steps": [],
                    "totals": {
                        "requests": 0,
                        "success": 0,
                        "failure": 1,
                        "expected_mismatch": 0,
                        "rps": 0,
                        "success_rate": 0,
                    },
                }
            )
            print(f"FAIL {name}: skipped ({skip_remaining})")
            continue
        parent = scenario if collection_mode else parent_collection
        prepared = apply_collection_defaults(child, parent) if parent else child
        result = execute_scenario(
            prepared,
            base_url_override=args.base_url,
            environment=args.environment,
            users=users,
            duration=duration,
            iterations=iterations,
            fail_fast=True if collection_mode else args.fail_fast,
            extra_env=extra_env or None,
        )
        result["file"] = name
        result["name"] = child.get("name", Path(name).stem)
        child_results.append(result)
        exported = exported_context_vars(result.get("vars"), child.get("export_env"))
        extra_env.update(exported)
        totals = result.get("totals", {})
        status = "PASS" if totals.get("failure", 0) == 0 and totals.get("expected_mismatch", 0) == 0 and totals.get("success", 0) > 0 else "FAIL"
        failed_steps = [step for step in result.get("steps", []) if step.get("failure")]
        extra = f"  {failed_steps[0].get('last_error')}" if failed_steps and failed_steps[0].get("last_error") else ""
        print(f"{status} {name}: {totals.get('success', 0)}/{totals.get('requests', 0)}{extra}")
        if collection_mode and child.get("export_env") and status == "FAIL":
            skip_remaining = f"{name} failed"

    elapsed = max(time.time() - started, 0.001)
    if collection_mode:
        total_count = sum(item["totals"]["requests"] for item in child_results)
        total_success = sum(item["totals"]["success"] for item in child_results)
        total_failure = sum(item["totals"]["failure"] for item in child_results)
        total_mismatch = sum(item["totals"]["expected_mismatch"] for item in child_results)
        passed = sum(1 for item in child_results if item["totals"]["failure"] == 0 and item["totals"]["expected_mismatch"] == 0 and item["totals"]["success"] > 0)
        output = {
            "kind": "collection",
            "collection": scenario_path.name,
            "base_url": args.base_url or resolve_base_url(scenario, args.environment),
            "environment": args.environment,
            "users": users,
            "duration_s": round(elapsed, 3),
            "iterations_per_user": iterations,
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
    else:
        output = child_results[0]

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2)
    print(json.dumps(output, indent=2))

    totals = output.get("totals", {})
    if args.strict and (totals.get("failure", 0) > 0 or totals.get("expected_mismatch", 0) > 0 or totals.get("success", 0) == 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
