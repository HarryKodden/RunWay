from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.scenario_schema import ScenarioValidationError, validate_scenario_document
from webapp.workspace import is_collection_document

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_example_scenarios_match_the_schema():
    documents = []
    for path in sorted(EXAMPLES.rglob("*.json")):
        document = _load(path)
        if is_collection_document(document):
            continue
        documents.append(path)
        validate_scenario_document(document)
    assert any(path.name == "token_exchange.json" for path in documents)


def test_schema_rejects_unknown_method_and_unknown_field():
    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document({"steps": [{"method": "NOPE", "path": "/"}]})
    assert any("method" in item for item in caught.value.errors)

    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document({"steps": [{"method": "GET", "path": "/", "bogus": 1}]})
    assert any("bogus" in item for item in caught.value.errors)


def test_schema_rejects_a_collection_document():
    suite = _load(EXAMPLES / "token-exchange" / "suite.json")
    with pytest.raises(ScenarioValidationError) as caught:
        validate_scenario_document(suite)
    assert any("scenarios" in item for item in caught.value.errors)


def test_schema_accepts_runner_step_shapes():
    validate_scenario_document(
        {
            "name": "shapes",
            "export_env": True,
            "steps": [
                {"name": "wait", "method": "SLEEP", "seconds": "{{ vars.pause }}"},
                {
                    "name": "poll",
                    "method": "GET",
                    "path": "/status",
                    "poll": {"interval_seconds": 1, "max_attempts": 3, "until_json_contains": {"ready": True}},
                },
                {
                    "name": "run",
                    "method": "EXEC",
                    "command": ["echo", "ok"],
                    "expected_exit_code": 0,
                },
                {
                    "name": "prepare",
                    "method": "PREPARE",
                    "prepare": [{"action": "pkce"}, {"action": "set", "vars": {"ready": True}}],
                },
                {
                    "name": "call",
                    "method": "POST",
                    "auth": {"type": "bearer", "token": "{{ vars.token }}"},
                    "data": {"scope": "openid"},
                },
            ],
        }
    )
