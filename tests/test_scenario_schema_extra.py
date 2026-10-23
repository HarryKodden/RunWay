from __future__ import annotations

import pytest

from tools.scenario_schema import ScenarioValidationError, validate_scenario_document


def test_schema_rejects_non_object_and_bad_export_env():
    with pytest.raises(ScenarioValidationError):
        validate_scenario_document([])
    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document({"steps": [], "fail_fast": "yes"})
    assert any("fail_fast" in item for item in caught.value.errors)
    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document({"steps": [], "export_env": ["", "ok"]})
    assert any("export_env" in item for item in caught.value.errors)


def test_schema_rejects_bad_environments_and_generators():
    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document({"steps": [], "environments": []})
    assert any("environments" in item for item in caught.value.errors)
    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document({"steps": [], "environments": {"dev": "nope"}})
    assert any("environments.dev" in item for item in caught.value.errors)
    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document({"steps": [], "environments": {"dev": {"nested": {"x": 1}}}})
    assert any("must be a string" in item for item in caught.value.errors)

    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document({"steps": [], "random_generators": []})
    assert any("random_generators" in item for item in caught.value.errors)
    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document({"steps": [], "random_generators": {"id": "uuid"}})
    assert any("must be an object" in item for item in caught.value.errors)
    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document(
            {"steps": [], "random_generators": {"id": {"type": "nope", "min": "x", "items": [{"a": 1}]}}}
        )
    errors = caught.value.errors
    assert any("type" in item for item in errors)


def test_schema_rejects_bad_step_shapes():
    cases = [
        {"steps": ["nope"]},
        {"steps": [{"method": "GET", "timeout": "slow"}]},
        {"steps": [{"method": "GET", "follow_redirects": "always"}]},
        {"steps": [{"method": "GET", "max_redirects": 1.5}]},
        {"steps": [{"method": "GET", "stop_redirect_hosts": "localhost"}]},
        {"steps": [{"method": "GET", "expected_status": "200"}]},
        {"steps": [{"method": "GET", "expected_body_contains": 1}]},
        {"steps": [{"method": "GET", "auth": "bearer"}]},
        {"steps": [{"method": "GET", "auth": {"type": "digest"}}]},
        {"steps": [{"method": "GET", "poll": []}]},
        {"steps": [{"method": "GET", "poll": {"interval_seconds": "fast"}}]},
        {"steps": [{"method": "USER_AUTH", "user_auth": []}]},
        {"steps": [{"method": "USER_AUTH", "user_auth": {"flows": ["password"]}}]},
        {"steps": [{"method": "PREPARE", "prepare": []}]},
        {"steps": [{"method": "PREPARE", "prepare": {"action": "unknown"}}]},
        {"steps": [{"method": "PREPARE", "prepare": {"action": "set"}}]},
        {"steps": [{"method": "PREPARE", "prepare": {"action": "dpop_key", "reset": "yes"}}]},
        {"steps": [{"method": "PREPARE", "prepare": {"action": "cimd_metadata", "port": "x"}}]},
        {"steps": [{"method": "EXEC", "command": []}]},
        {"steps": [{"method": "GET", "headers": {" ": "x"}}]},
        {"steps": [{"method": "GET", "data": [1, 2]}]},
    ]
    for document in cases:
        with pytest.raises(ScenarioValidationError):
            validate_scenario_document(document)


def test_schema_accepts_rich_valid_document():
    validate_scenario_document(
        {
            "name": "rich",
            "export_env": ["token"],
            "fail_fast": True,
            "environments": {"dev": {"server": "https://api.example.org", "n": 1, "ok": True}},
            "random_generators": {
                "id": {"type": "uuid"},
                "n": {"type": "int", "min": 1, "max": 3},
                "pick": {"type": "choice", "items": ["a", 1, True]},
                "mail": {"type": "email", "prefix": "u", "domain": "example.org"},
            },
            "steps": [
                {
                    "name": "auth",
                    "method": "USER_AUTH",
                    "user_auth": {
                        "flows": ["device_code", "authorization_code"],
                        "client_id": "c",
                        "issuer": "https://idp.example.org",
                    },
                },
                {
                    "name": "prep",
                    "method": "PREPARE",
                    "prepare": [
                        {"action": "pkce"},
                        {"action": "dpop_key", "reset": True},
                        {"action": "basic_auth", "username": "a", "password": "b"},
                        {"action": "set", "vars": {"ready": True}},
                    ],
                },
                {
                    "name": "call",
                    "method": "POST",
                    "path": "/x",
                    "headers": {"Accept": "application/json"},
                    "auth": {"type": "bearer", "token": "t"},
                    "expected_status": [200, 201],
                    "expected_body_contains": ["ok"],
                    "follow_redirects": "smart",
                    "max_redirects": 3,
                    "stop_redirect_hosts": ["127.0.0.1"],
                    "timeout": 5,
                    "data": {"a": "1"},
                    "poll": {"interval_seconds": 0.5, "max_attempts": 2, "until_json_contains": {"ok": True}},
                },
            ],
        }
    )
