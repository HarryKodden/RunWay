"""Build a human-readable scenario documentation report (markdown)."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from tools.scenario_runner import (
    apply_collection_defaults,
    apply_env_overrides,
    coerce_structured_value,
    environment_name_as_base_url,
    finalize_environment_values,
    preview_step_request,
    resolve_base_url,
    resolve_environment_values,
)

_SENSITIVE_KEY_RE = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|authorization|client_secret|access_token|"
    r"refresh_token|private_key|bearer|credential)",
    re.I,
)
_AUTH_SCHEME_HEADER_RE = re.compile(
    r"(?im)^(Authorization\s*[:=]\s*(?:Bearer|Basic)\s+)(\S+)"
)
_API_KEY_HEADER_RE = re.compile(
    r"(?im)^((?:X-API-KEY|API-KEY|X-Auth-Token)\s*[:=]\s*)(.+)$"
)
_BEARER_INLINE_RE = re.compile(r"(?i)(bearer\s+)([^\s\"']+)")
_BASIC_INLINE_RE = re.compile(r"(?i)(basic\s+)([A-Za-z0-9+/=]+)")
_HEADER_FLAG_AUTH_RE = re.compile(
    r"(?i)(-H\s+['\"]Authorization\s*:\s*(?:Bearer|Basic)\s+)([^'\"]+)(['\"])"
)
_HEADER_FLAG_API_KEY_RE = re.compile(
    r"(?i)(-H\s+['\"](?:X-API-KEY|API-KEY|X-Auth-Token)\s*:\s*)([^'\"]+)(['\"])"
)


def mask_sensitive_text(text: str) -> str:
    """Redact secrets commonly present in curls, logs, and headers."""
    if not text:
        return text
    masked = _AUTH_SCHEME_HEADER_RE.sub(lambda match: match.group(1) + "***", text)
    masked = _API_KEY_HEADER_RE.sub(lambda match: match.group(1) + "***", masked)
    masked = _HEADER_FLAG_AUTH_RE.sub(lambda match: match.group(1) + "***" + match.group(3), masked)
    masked = _HEADER_FLAG_API_KEY_RE.sub(lambda match: match.group(1) + "***" + match.group(3), masked)
    masked = _BEARER_INLINE_RE.sub(lambda match: match.group(1) + "***", masked)
    masked = _BASIC_INLINE_RE.sub(lambda match: match.group(1) + "***", masked)
    return masked


def is_sensitive_key(name: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(name or "")))


def mask_structured(value: Any, *, key_hint: str = "") -> Any:
    """Recursively mask sensitive keys in env/constants trees."""
    if is_sensitive_key(key_hint):
        if value is None or value == "":
            return value
        return "***"
    if isinstance(value, dict):
        return {str(k): mask_structured(v, key_hint=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_structured(item, key_hint=key_hint) for item in value]
    if isinstance(value, str):
        return mask_sensitive_text(value)
    return value


def _format_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def _bullet_map(values: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in sorted(values.keys(), key=lambda item: str(item).lower()):
        raw = values[key]
        shown = mask_structured(raw, key_hint=str(key))
        if isinstance(shown, (dict, list)):
            pretty = _format_json(shown)
            lines.append(f"- `{key}`:\n\n```json\n{pretty}\n```")
        else:
            lines.append(f"- `{key}`: `{shown}`")
    return lines


def _describe_expected_status(step: dict[str, Any]) -> str:
    expected = step.get("expected_status")
    if expected is None or expected == "":
        return "No explicit status check (any HTTP response is accepted unless other expectations fail)."
    if isinstance(expected, list):
        joined = ", ".join(str(item) for item in expected)
        return f"Expects HTTP status in [{joined}]."
    return f"Expects HTTP status {expected}."


def _describe_expected_json(step: dict[str, Any]) -> str | None:
    expected = step.get("expected_json_contains")
    if not isinstance(expected, dict) or not expected:
        return None
    return (
        "Also asserts that the JSON body contains at least these fields/values:\n\n"
        f"```json\n{_format_json(mask_structured(expected))}\n```"
    )


def _describe_report(step: dict[str, Any]) -> str:
    report = step.get("report")
    if report is False:
        return "This step is excluded from the run Output dialog."
    if report is True or report is None:
        if "report" not in step:
            return "Included in run Output by default (full response body)."
        return "Included in run Output (full response body)."
    if isinstance(report, list):
        fields = ", ".join(f"`{item}`" for item in report if str(item).strip())
        return f"Included in run Output with only these fields: {fields}."
    if isinstance(report, str) and report.strip():
        if "," in report:
            fields = ", ".join(f"`{part.strip()}`" for part in report.split(",") if part.strip())
            return f"Included in run Output with only these fields: {fields}."
        return f"Included in run Output with field projection `{report.strip()}`."
    if isinstance(report, dict) and report:
        fields = ", ".join(f"`{key}`" for key in report.keys())
        return f"Included in run Output as a shaped object with keys: {fields}."
    return "Included in run Output by default (full response body)."


def _describe_save(step: dict[str, Any]) -> str | None:
    parts: list[str] = []
    save_as = str(step.get("save_response_as") or "").strip()
    if save_as:
        parts.append(f"Stores the full JSON response as `vars.{save_as}` for later steps.")
    save_map = step.get("save")
    if isinstance(save_map, dict) and save_map:
        mappings = ", ".join(f"`{key}` ← `{save_map[key]}`" for key in save_map)
        parts.append(f"Saves individual values into vars: {mappings}.")
    return " ".join(parts) if parts else None


def _describe_foreach(step: dict[str, Any], foreach_count: int | None) -> str:
    if "foreach" not in step:
        return "Runs once."
    source = step.get("foreach")
    if isinstance(source, list):
        label = f"a literal list of {len(source)} item(s)"
    else:
        label = f"`{source}`"
    stop = step.get("stop_on_failure", True)
    stop_text = (
        "Stops remaining iterations after the first failure."
        if stop is not False
        else "Continues remaining iterations even if one item fails."
    )
    count_text = ""
    if foreach_count is not None:
        count_text = f" Currently resolves to {foreach_count} item(s)."
    return (
        f"Iterates with foreach over {label}. Each iteration binds `{{{{ item }}}}` and "
        f"`{{{{ index }}}}` (0-based).{count_text} {stop_text}"
    )


def _describe_step_action(step: dict[str, Any]) -> str:
    method = str(step.get("method") or "GET").upper()
    name = str(step.get("name") or "step").strip() or "step"
    path = str(step.get("path") or step.get("url") or "").strip()
    if method == "PREPARE":
        return f"**{name}** prepares variables/helpers before later HTTP steps (no outbound request)."
    if method == "EXEC":
        return f"**{name}** runs a local command (`EXEC`) instead of calling an HTTP API."
    if method == "SLEEP":
        seconds = step.get("seconds", 1)
        return f"**{name}** pauses the scenario for `{seconds}` second(s)."
    if method == "USER_AUTH":
        return (
            f"**{name}** pauses the run for interactive sign-in (device code and/or authorization code)."
        )
    if method == "MCP":
        spec = step.get("mcp") if isinstance(step.get("mcp"), dict) else {}
        call = str(spec.get("tool") or spec.get("method") or "JSON-RPC").strip() or "JSON-RPC"
        target = f"`{path}`" if path else "`/mcp`"
        return (
            f"**{name}** calls MCP `{call}` on {target}. "
            "The first MCP step for that URL sends initialize and notifications/initialized, "
            "keeps Mcp-Session-Id, and reuses it for later MCP steps. "
            "Responses may be JSON or an SSE `data:` event; the JSON-RPC message is the step result."
        )
    target = f"`{path}`" if path else "the configured URL"
    verb = {
        "GET": "retrieves",
        "POST": "creates or submits to",
        "PUT": "replaces",
        "PATCH": "updates",
        "DELETE": "deletes",
        "HEAD": "probes",
        "OPTIONS": "options-checks",
    }.get(method, "calls")
    bits = [f"**{name}** {verb} {target} using HTTP `{method}`."]
    if step.get("follow_redirects"):
        bits.append("Redirects are followed.")
    if step.get("timeout") not in (None, ""):
        bits.append(f"Timeout is `{step.get('timeout')}` second(s).")
    headers = step.get("headers") if isinstance(step.get("headers"), dict) else {}
    if headers:
        header_names = ", ".join(f"`{key}`" for key in headers.keys())
        bits.append(f"Sends headers: {header_names}.")
    if step.get("json") is not None:
        bits.append("Sends a JSON request body.")
    elif step.get("data") is not None:
        bits.append("Sends a form/text request body.")
    if step.get("poll"):
        bits.append("Uses poll/retry until the expectation succeeds or the poll budget ends.")
    auth = step.get("auth")
    if isinstance(auth, dict) and auth.get("type"):
        bits.append(f"Applies `{auth.get('type')}` authentication (secrets masked in the curl below).")
    return " ".join(bits)


def _usable_base_url(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith(("http://", "https://")):
        return text
    return ""


def prepare_document_context(
    scenario: dict[str, Any],
    *,
    selected_environment: str | None = None,
    environment_overrides: dict[str, Any] | None = None,
    base_url: str | None = None,
    collection: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Return (base_url, context, random_generators, steps, environment_values).

    Unlike run/preview preparation, missing env vars and private hosts do not fail —
    documentation should still render with unresolved placeholders.
    """
    document = apply_collection_defaults(dict(scenario), collection) if collection else dict(scenario)
    steps = document.get("steps") if isinstance(document.get("steps"), list) else []
    steps = [step for step in steps if isinstance(step, dict)]
    env_name = str(
        selected_environment or document.get("selected_environment") or ""
    ).strip()
    random_generators = (
        document.get("random_generators")
        if isinstance(document.get("random_generators"), dict)
        else {}
    )
    constants = document.get("constants") if isinstance(document.get("constants"), dict) else {}
    environment_values = resolve_environment_values(document, env_name or None)
    if isinstance(environment_overrides, dict) and environment_overrides:
        environment_values = apply_env_overrides(environment_values, environment_overrides)
    resolved_base = (
        _usable_base_url(base_url)
        or _usable_base_url(resolve_base_url(document, env_name or None))
        or environment_name_as_base_url(env_name)
        or ""
    )
    environment_values = finalize_environment_values(
        environment_values,
        base_url=resolved_base,
        selected_environment=env_name,
    )
    env_host = _usable_base_url(
        environment_values.get("server") or environment_values.get("base_url")
    )
    if env_host:
        resolved_base = env_host
    context = {
        "vars": {"worker_id": 1},
        "env": dict(environment_values),
        "constants": dict(constants),
        "reports": [],
        "meta": {"environment": env_name},
    }
    return resolved_base, context, random_generators, steps, environment_values


def build_scenario_document(
    scenario: dict[str, Any],
    *,
    selected_environment: str | None = None,
    environment_overrides: dict[str, Any] | None = None,
    base_url: str | None = None,
    collection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a masked markdown report describing the scenario."""
    document = apply_collection_defaults(dict(scenario), collection) if collection else dict(scenario)
    base, context, random_generators, steps, environment_values = prepare_document_context(
        document,
        selected_environment=selected_environment,
        environment_overrides=environment_overrides,
        base_url=base_url,
        collection=None,  # already applied
    )
    name = str(document.get("name") or "scenario").strip() or "scenario"
    description = str(document.get("description") or "").strip()
    env_name = str(context.get("meta", {}).get("environment") or "").strip()
    constants = document.get("constants") if isinstance(document.get("constants"), dict) else {}
    environments = document.get("environments") if isinstance(document.get("environments"), dict) else {}

    lines: list[str] = [f"# Scenario: {name}", ""]
    if description:
        lines.extend([description, ""])

    session_headers = document.get("session_headers")
    if isinstance(session_headers, list):
        session_label = ", ".join(f"`{name}`" for name in session_headers) or "(none)"
    elif isinstance(session_headers, str) and session_headers.strip():
        session_label = f"`{session_headers.strip()}`"
    else:
        session_label = "(none)"
    lines.extend(
        [
            "## Overview",
            "",
            f"- Steps: **{len(steps)}**",
            f"- Selected environment: `{env_name or '(none)'}`",
            f"- Base URL: `{base or '(none)'}`",
            f"- Session headers: {session_label}",
            "",
        ]
    )

    lines.extend(["## Constants", ""])
    if constants:
        lines.extend(_bullet_map(constants))
    else:
        lines.append("_No constants defined._")
    lines.append("")

    lines.extend(["## Environments", ""])
    if environments:
        for env_key in sorted(environments.keys(), key=lambda item: str(item).lower()):
            values = environments.get(env_key)
            marker = " (selected)" if str(env_key) == env_name else ""
            lines.append(f"### `{env_key}`{marker}")
            lines.append("")
            if not isinstance(values, dict) or not values:
                lines.append("_Empty environment._")
            else:
                # Prefer live resolved values for the selected env (includes overrides).
                source = environment_values if str(env_key) == env_name else values
                coerced = {
                    key: coerce_structured_value(item) for key, item in dict(source).items()
                }
                lines.extend(_bullet_map(coerced))
            lines.append("")
    else:
        lines.append("_No named environments._")
        lines.append("")

    lines.extend(["## Steps", ""])
    if not steps:
        lines.append("_This scenario has no steps._")
        lines.append("")

    for index, step in enumerate(steps):
        step_name = str(step.get("name") or f"step_{index + 1}").strip() or f"step_{index + 1}"
        method = str(step.get("method") or "GET").upper()
        lines.append(f"### {index + 1}. {step_name}")
        lines.append("")
        lines.append(_describe_step_action(step))
        lines.append("")

        preview: dict[str, Any] = {}
        curl = ""
        foreach_count = None
        unresolved: list[str] = []
        try:
            preview_context = copy.deepcopy(context)
            preview = preview_step_request(
                steps=steps,
                step_index=index,
                base_url=base,
                context=preview_context,
                random_generators=random_generators,
                hydrate_prior=False,
            )
            curl = str(preview.get("curl") or "")
            foreach_count = preview.get("foreach_count")
            unresolved = list(preview.get("unresolved") or [])
        except Exception as exc:
            curl = f"# curl preview unavailable: {exc}"

        lines.append(f"**Flow control:** {_describe_foreach(step, foreach_count if isinstance(foreach_count, int) else None)}")
        lines.append("")
        lines.append(f"**Validation:** {_describe_expected_status(step)}")
        lines.append("")
        expected_json = _describe_expected_json(step)
        if expected_json:
            lines.append(expected_json)
            lines.append("")
        lines.append(f"**Output:** {_describe_report(step)}")
        lines.append("")
        save_text = _describe_save(step)
        if save_text:
            lines.append(f"**Saved results:** {save_text}")
            lines.append("")
        if unresolved:
            lines.append(
                "**Unresolved placeholders in this preview:** "
                + ", ".join(f"`{item}`" for item in unresolved)
            )
            lines.append("")

        lines.append("**Request (curl):**")
        lines.append("")
        lines.append("```bash")
        lines.append(mask_sensitive_text(curl).rstrip() or f"# {method} (empty preview)")
        lines.append("```")
        lines.append("")

    markdown = "\n".join(lines).rstrip() + "\n"
    return {
        "name": name,
        "markdown": markdown,
        "step_count": len(steps),
        "environment": env_name,
        "base_url": base,
    }
