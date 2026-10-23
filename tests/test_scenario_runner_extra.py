from __future__ import annotations

import ipaddress

import pytest
import requests

from tools.scenario_runner import (
    apply_collection_defaults,
    apply_env_overrides,
    build_random_value,
    collect_placeholders,
    env_flag,
    evaluate_expectations,
    forbidden_api_targets,
    is_blocked_ip,
    is_collection,
    is_user_auth_step,
    merge_defined,
    missing_environment_dependencies,
    normalize_prepare,
    parse_ip_literal,
    parse_target_hostname,
    percentile,
    prepare_output_keys,
    run_prepare,
    save_source_name,
    tls_verify_enabled,
    value_contains,
)


def test_env_flag_and_tls_verify(monkeypatch):
    assert env_flag(True) is True
    assert env_flag(0) is False
    assert env_flag("YES") is True
    assert env_flag("off") is False
    assert env_flag("") is None
    assert env_flag("maybe") is None
    monkeypatch.delenv("RUNWAY_INSECURE_TLS", raising=False)
    assert tls_verify_enabled(None) is True
    assert tls_verify_enabled({"insecure_tls": True}) is False
    monkeypatch.setenv("RUNWAY_INSECURE_TLS", "1")
    assert tls_verify_enabled({}) is False


def test_parse_ip_literal_and_blocked_networks():
    assert parse_ip_literal("8.8.8.8") is not None
    assert parse_ip_literal("::1") is not None
    assert parse_ip_literal("[::1]") is not None
    assert parse_ip_literal("127.1") is not None
    assert parse_ip_literal("not-an-ip") is None
    assert is_blocked_ip(ipaddress.ip_address("10.0.0.1"))
    assert is_blocked_ip(ipaddress.ip_address("169.254.169.254"))
    assert is_blocked_ip(ipaddress.ip_address("::1"))
    assert not is_blocked_ip(ipaddress.ip_address("8.8.8.8"))


def test_parse_target_hostname_and_forbidden_targets():
    assert parse_target_hostname("https://API.Example.org./path") == "api.example.org"
    assert parse_target_hostname("api.example.org") == "api.example.org"
    assert parse_target_hostname("") == ""
    assert set(forbidden_api_targets({"server": "http://127.0.0.1"}, ["https://8.8.8.8/"])) == {
        "server",
        "base_url",
    }


def test_build_random_value_kinds():
    assert isinstance(build_random_value({"type": "uuid"}), str)
    assert 1 <= build_random_value({"type": "int", "min": 1, "max": 3}) <= 3
    assert isinstance(build_random_value({"type": "float", "min": 0, "max": 1, "decimals": 2}), float)
    assert build_random_value({"type": "choice", "items": ["a"]}) == "a"
    assert build_random_value({"type": "choice", "items": []}) == ""
    assert "@example.org" in build_random_value({"type": "email", "prefix": "u", "domain": "example.org"})
    assert len(build_random_value({"type": "string", "length": 5, "alphabet": "ab"})) == 5
    assert build_random_value({"type": "nope"}) == ""


def test_value_contains_and_percentile_and_placeholders():
    assert value_contains("Bearer", "bearer")
    assert value_contains([1, 2, 3], [1, 2])
    assert not value_contains([1, 2], [1, 9])
    assert value_contains({"a": {"b": 1}}, {"a": {"b": 1}})
    assert percentile([1.0, 2.0, 3.0, 4.0], 0) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 100) == 4.0
    assert percentile([], 50) == 0.0
    found = collect_placeholders({"path": "{{ vars.id }}/{{ env.token }}", "nested": ["{{x}}"]})
    assert set(found) == {"vars.id", "env.token", "x"}


def test_prepare_helpers_and_run_prepare(monkeypatch):
    assert normalize_prepare(None) == []
    assert normalize_prepare({"action": "pkce"}) == [{"action": "pkce"}]
    assert normalize_prepare([{"action": "set", "vars": {"a": 1}}])[0]["action"] == "set"
    assert "pkce_verifier" in prepare_output_keys({"action": "pkce"})
    assert "dpop_jkt" in prepare_output_keys({"action": "dpop_key"})
    assert "dpop_proof" in prepare_output_keys({"action": "dpop"})
    assert "basic_auth" in prepare_output_keys({"action": "basic_auth"})
    assert "client_assertion" in prepare_output_keys({"action": "mock_attestation_jwt"})
    assert "cimd_client_id" in prepare_output_keys({"action": "cimd_metadata"})
    assert prepare_output_keys({"action": "set", "vars": {"a": 1, "b": 2}}) == ["a", "b"]
    assert save_source_name("{{ json.id }}") == "json.id"
    assert save_source_name("headers.Authorization") == "headers.Authorization"
    assert is_user_auth_step({"method": "USER_AUTH"})
    assert not is_user_auth_step({"method": "GET"})
    assert is_collection({"scenarios": ["a.json"]})
    assert not is_collection({"steps": []})
    assert merge_defined({"a": 1, "b": None}, {"b": 2, "c": 3}) == {"a": 1, "b": 2, "c": 3}
    assert apply_env_overrides({"a": "1"}, {"a": "2", "b": "3"})["b"] == "3"
    child = apply_collection_defaults({"steps": [{"method": "GET", "path": "/"}]}, {"environments": {"dev": {"x": 1}}})
    assert child["environments"]["dev"]["x"] == 1
    assert missing_environment_dependencies({}, {"path": "{{ env.token }}"}) == ["token"]

    context: dict = {"vars": {}, "env": {}}
    run_prepare({"action": "pkce"}, context, {}, "https://api.example.org")
    assert context["vars"]["pkce_verifier"]
    run_prepare({"action": "dpop_key", "reset": True}, context, {}, "https://api.example.org")
    assert context["vars"]["dpop_jkt"]
    run_prepare({"action": "dpop", "htm": "POST", "htu": "https://api.example.org/token"}, context, {}, "")
    assert context["vars"]["dpop_proof"].count(".") == 2
    run_prepare({"action": "basic_auth", "username": "u", "password": "p"}, context, {}, "")
    assert context["vars"]["basic_auth"].startswith("Basic ")
    run_prepare({"action": "mock_attestation_jwt", "client_id": "c", "audience": "a"}, context, {}, "")
    assert context["vars"]["client_assertion"].count(".") == 2
    run_prepare({"action": "set", "vars": {"ready": True}}, context, {}, "")
    assert context["vars"]["ready"] is True
    with pytest.raises(ValueError):
        run_prepare({"action": "nope"}, context, {}, "")


def test_evaluate_expectations_body_lists():
    response = requests.Response()
    response.status_code = 200
    assert evaluate_expectations(
        {
            "expected_status": 200,
            "expected_body_contains": ["ok", "yes"],
            "expected_body_not_contains": ["error"],
        },
        response,
        {},
        "ok yes",
    )
    assert not evaluate_expectations(
        {"expected_status": 200, "expected_body_contains": ["missing"]},
        response,
        {},
        "ok",
    )
