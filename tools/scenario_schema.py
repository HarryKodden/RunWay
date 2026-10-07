"""Validate a scenario document before it is saved.

The schema is the set of fields the runner actually reads. Unknown keys are
rejected so a typo in an advanced JSON edit is reported instead of ignored.
"""

from __future__ import annotations

from typing import Any

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
STEP_METHODS = HTTP_METHODS | {"USER_AUTH", "PREPARE", "EXEC", "SLEEP"}

SCENARIO_FIELDS = {
    "name",
    "description",
    "base_url",
    "fail_fast",
    "export_env",
    "selected_environment",
    "environments",
    "random_generators",
    "steps",
}

STEP_FIELDS = {
    "name",
    "description",
    "method",
    "path",
    "url",
    "headers",
    "json",
    "data",
    "auth",
    "save",
    "save_response_as",
    "expected_status",
    "expected_body_contains",
    "expected_body_not_contains",
    "expected_json_contains",
    "timeout",
    "follow_redirects",
    "max_redirects",
    "stop_redirect_hosts",
    "stop_on_failure",
    "poll",
    "user_auth",
    "prepare",
    "action",
    "command",
    "exec",
    "cwd",
    "env",
    "skip_if_cwd_missing",
    "expected_exit_code",
    "save_stdout",
    "save_stderr",
    "save_exit_code",
    "seconds",
    "dpop_nonce_retry",
}

AUTH_FIELDS = {"type", "username", "password", "token"}
AUTH_TYPES = {"basic", "bearer"}

USER_AUTH_FIELDS = {
    "description",
    "flows",
    "client_id",
    "client_secret",
    "scope",
    "audience",
    "issuer",
    "authorization_endpoint",
    "device_authorization_endpoint",
    "token_endpoint",
    "redirect_uri",
}
USER_AUTH_FLOWS = {"device_code", "authorization_code"}

POLL_FIELDS = {"interval_seconds", "max_attempts", "until_json_contains", "until_not_error"}

PREPARE_ACTIONS = {
    "pkce": {"save_verifier", "save_challenge", "save_method"},
    "dpop_key": {"key", "reset", "save_jkt"},
    "dpop": {"key", "htm", "htu", "nonce", "ath_token", "access_token", "save_proof", "save_jkt"},
    "basic_auth": {"username", "password", "save"},
    "mock_attestation_jwt": {"client_id", "audience", "save"},
    "cimd_metadata": {"port", "metadata", "save_url"},
    "set": {"vars"},
}
PREPARE_META = {"action", "type"}

GENERATOR_TYPES = {"uuid", "int", "float", "choice", "email", "string"}
GENERATOR_FIELDS = {"type", "min", "max", "decimals", "items", "prefix", "domain", "length", "alphabet"}


class ScenarioValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def validate_scenario_document(document: Any) -> None:
    """Raise ScenarioValidationError when the document is not a scenario."""
    if not isinstance(document, dict):
        raise ScenarioValidationError(["Scenario must be a JSON object"])
    errors: list[str] = []
    _check_scenario(document, errors)
    if errors:
        raise ScenarioValidationError(errors)


def _check_scenario(document: dict[str, Any], errors: list[str]) -> None:
    _unknown_keys(document, SCENARIO_FIELDS, "scenario", errors)
    _optional_string(document, "name", "scenario.name", errors)
    _optional_string(document, "description", "scenario.description", errors)
    _optional_string(document, "base_url", "scenario.base_url", errors)
    _optional_string(document, "selected_environment", "scenario.selected_environment", errors)
    if "fail_fast" in document and not isinstance(document["fail_fast"], bool):
        errors.append("scenario.fail_fast must be true or false")
    if "export_env" in document:
        _check_export_env(document["export_env"], errors)
    if "environments" in document:
        _check_environments(document["environments"], errors)
    if "random_generators" in document:
        _check_generators(document["random_generators"], errors)
    steps = document.get("steps")
    if not isinstance(steps, list):
        errors.append("scenario.steps must be an array")
        return
    for index, step in enumerate(steps):
        _check_step(step, f"steps[{index}]", errors)


def _check_export_env(value: Any, errors: list[str]) -> None:
    if value is True:
        return
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return
    errors.append('scenario.export_env must be true or an array of variable names')


def _check_environments(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("scenario.environments must be an object")
        return
    for name, env in value.items():
        path = f"scenario.environments.{name}"
        if not isinstance(env, dict):
            errors.append(f"{path} must be an object")
            continue
        for key, item in env.items():
            if not _is_scalar(item):
                errors.append(f"{path}.{key} must be a string, number, or boolean")


def _check_generators(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("scenario.random_generators must be an object")
        return
    for name, config in value.items():
        path = f"scenario.random_generators.{name}"
        if not isinstance(config, dict):
            errors.append(f"{path} must be an object")
            continue
        _unknown_keys(config, GENERATOR_FIELDS, path, errors)
        kind = config.get("type", "string")
        if not isinstance(kind, str) or kind not in GENERATOR_TYPES:
            errors.append(f"{path}.type must be one of {', '.join(sorted(GENERATOR_TYPES))}")
        for key in ("min", "max"):
            if key in config and not _is_number(config[key]):
                errors.append(f"{path}.{key} must be a number")
        for key in ("decimals", "length"):
            if key in config and not _is_int(config[key]):
                errors.append(f"{path}.{key} must be an integer")
        for key in ("prefix", "domain", "alphabet"):
            if key in config and not isinstance(config[key], str):
                errors.append(f"{path}.{key} must be a string")
        if "items" in config:
            items = config["items"]
            if not isinstance(items, list) or any(not _is_scalar(item) for item in items):
                errors.append(f"{path}.items must be an array of strings, numbers, or booleans")


def _check_step(step: Any, path: str, errors: list[str]) -> None:
    if not isinstance(step, dict):
        errors.append(f"{path} must be an object")
        return
    _unknown_keys(step, STEP_FIELDS, path, errors)
    _optional_string(step, "name", f"{path}.name", errors)
    _optional_string(step, "description", f"{path}.description", errors)
    _optional_string(step, "path", f"{path}.path", errors)
    _optional_string(step, "url", f"{path}.url", errors)
    _optional_string(step, "save_response_as", f"{path}.save_response_as", errors)
    _optional_string(step, "cwd", f"{path}.cwd", errors)
    _optional_string(step, "save_stdout", f"{path}.save_stdout", errors)
    _optional_string(step, "save_stderr", f"{path}.save_stderr", errors)
    _optional_string(step, "save_exit_code", f"{path}.save_exit_code", errors)
    if "method" in step:
        method = step["method"]
        if not isinstance(method, str) or method.upper() not in STEP_METHODS:
            errors.append(f"{path}.method must be one of {', '.join(sorted(STEP_METHODS))}")
    if "headers" in step:
        _check_string_map(step["headers"], f"{path}.headers", errors, scalars=True)
    if "save" in step:
        _check_string_map(step["save"], f"{path}.save", errors, scalars=False)
    if "auth" in step:
        _check_auth(step["auth"], f"{path}.auth", errors)
    if "expected_status" in step:
        _check_status(step["expected_status"], f"{path}.expected_status", errors)
    for key in ("expected_body_contains", "expected_body_not_contains"):
        if key in step:
            _check_body_expectation(step[key], f"{path}.{key}", errors)
    if "timeout" in step and not _is_number(step["timeout"]):
        errors.append(f"{path}.timeout must be a number")
    if "seconds" in step and not (_is_number(step["seconds"]) or isinstance(step["seconds"], str)):
        errors.append(f"{path}.seconds must be a number or a string")
    if "follow_redirects" in step and step["follow_redirects"] not in (True, False, "smart", "manual"):
        errors.append(f'{path}.follow_redirects must be true, false, "smart", or "manual"')
    if "max_redirects" in step and not _is_int(step["max_redirects"]):
        errors.append(f"{path}.max_redirects must be an integer")
    if "stop_redirect_hosts" in step:
        hosts = step["stop_redirect_hosts"]
        if not isinstance(hosts, list) or any(not isinstance(item, str) for item in hosts):
            errors.append(f"{path}.stop_redirect_hosts must be an array of strings")
    if "stop_on_failure" in step and not isinstance(step["stop_on_failure"], bool):
        errors.append(f"{path}.stop_on_failure must be true or false")
    if "skip_if_cwd_missing" in step and not isinstance(step["skip_if_cwd_missing"], bool):
        errors.append(f"{path}.skip_if_cwd_missing must be true or false")
    if "expected_exit_code" in step and not _is_int(step["expected_exit_code"]):
        errors.append(f"{path}.expected_exit_code must be an integer")
    if "poll" in step:
        _check_poll(step["poll"], f"{path}.poll", errors)
    if "user_auth" in step:
        _check_user_auth(step["user_auth"], f"{path}.user_auth", errors)
    if "prepare" in step:
        _check_prepare_value(step["prepare"], f"{path}.prepare", errors)
    if "action" in step:
        _check_prepare_object(step["action"], f"{path}.action", errors)
    if "dpop_nonce_retry" in step:
        _check_prepare_object(step["dpop_nonce_retry"], f"{path}.dpop_nonce_retry", errors)
    for key in ("command", "exec"):
        if key in step:
            _check_command(step[key], f"{path}.{key}", errors)
    if "env" in step:
        _check_string_map(step["env"], f"{path}.env", errors, scalars=True)
    if "data" in step and not _is_form_data(step["data"]):
        errors.append(f"{path}.data must be an object or a string")


def _check_auth(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    _unknown_keys(value, AUTH_FIELDS, path, errors)
    kind = value.get("type")
    if not isinstance(kind, str) or kind not in AUTH_TYPES:
        errors.append(f"{path}.type must be basic or bearer")
    for key in ("username", "password", "token"):
        if key in value and not isinstance(value[key], str):
            errors.append(f"{path}.{key} must be a string")


def _check_status(value: Any, path: str, errors: list[str]) -> None:
    if _is_int(value):
        return
    if isinstance(value, list) and value and all(_is_int(item) for item in value):
        return
    errors.append(f"{path} must be an integer or an array of integers")


def _check_body_expectation(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, str):
        return
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return
    errors.append(f"{path} must be a string or an array of strings")


def _check_poll(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    _unknown_keys(value, POLL_FIELDS, path, errors)
    if "interval_seconds" in value and not _is_number(value["interval_seconds"]):
        errors.append(f"{path}.interval_seconds must be a number")
    if "max_attempts" in value and not _is_int(value["max_attempts"]):
        errors.append(f"{path}.max_attempts must be an integer")
    if "until_not_error" in value and not isinstance(value["until_not_error"], str):
        errors.append(f"{path}.until_not_error must be a string")


def _check_user_auth(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    _unknown_keys(value, USER_AUTH_FIELDS, path, errors)
    for key in USER_AUTH_FIELDS - {"flows"}:
        if key in value and not isinstance(value[key], str):
            errors.append(f"{path}.{key} must be a string")
    if "flows" not in value:
        return
    flows = value["flows"]
    if isinstance(flows, str):
        flows = [flows]
    if not isinstance(flows, list) or any(item not in USER_AUTH_FLOWS for item in flows):
        errors.append(f"{path}.flows must be device_code, authorization_code, or both")


def _check_prepare_value(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, list):
        if not value:
            errors.append(f"{path} must contain at least one prepare action")
            return
        for index, item in enumerate(value):
            _check_prepare_object(item, f"{path}[{index}]", errors)
        return
    _check_prepare_object(value, path, errors)


def _check_prepare_object(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    kind = value.get("action") or value.get("type")
    if not isinstance(kind, str) or kind not in PREPARE_ACTIONS:
        names = ", ".join(sorted(PREPARE_ACTIONS))
        errors.append(f"{path}.action must be one of {names}")
        return
    allowed = PREPARE_ACTIONS[kind] | PREPARE_META
    _unknown_keys(value, allowed, path, errors)
    if kind == "set":
        variables = value.get("vars")
        if not isinstance(variables, dict):
            errors.append(f"{path}.vars must be an object")
        return
    if kind == "dpop_key" and "reset" in value and not isinstance(value["reset"], bool):
        errors.append(f"{path}.reset must be true or false")
    if kind == "cimd_metadata":
        if "port" in value and not _is_int(value["port"]):
            errors.append(f"{path}.port must be an integer")
        if "metadata" in value and not isinstance(value["metadata"], dict):
            errors.append(f"{path}.metadata must be an object")
    for key, item in value.items():
        if key in PREPARE_META or key in {"reset", "vars", "port", "metadata"}:
            continue
        if not isinstance(item, str):
            errors.append(f"{path}.{key} must be a string")


def _check_command(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, str):
        return
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return
    errors.append(f"{path} must be a string or an array of strings")


def _check_string_map(value: Any, path: str, errors: list[str], *, scalars: bool) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return
    for key, item in value.items():
        if not str(key).strip():
            errors.append(f"{path} contains an empty key")
        if scalars:
            if not _is_scalar(item):
                errors.append(f"{path}.{key} must be a string, number, or boolean")
        elif not isinstance(item, str):
            errors.append(f"{path}.{key} must be a string")


def _is_form_data(value: Any) -> bool:
    if isinstance(value, str):
        return True
    if not isinstance(value, dict):
        return False
    return all(_is_scalar(item) or _is_scalar_list(item) for item in value.values())


def _is_scalar_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_scalar(item) for item in value)


def _optional_string(obj: dict[str, Any], key: str, path: str, errors: list[str]) -> None:
    if key in obj and not isinstance(obj[key], str):
        errors.append(f"{path} must be a string")


def _unknown_keys(obj: dict[str, Any], allowed: set[str], path: str, errors: list[str]) -> None:
    for key in obj:
        if key not in allowed:
            errors.append(f"{path}.{key} is not a recognized field")


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | bool) or _is_number(value)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
