from __future__ import annotations

from webapp.app import (
    _convert_insomnia_export,
    _convert_insomnia_v5_collection,
    _convert_postman_collection,
    _extract_insomnia_headers,
    _extract_insomnia_v5_environments,
    _extract_postman_body,
    _extract_postman_headers,
    _extract_postman_url,
    _extract_postman_variables,
    _flatten_postman_items,
    _insomnia_url_to_parts,
    _resolve_base_url_from_environment,
    _to_path_from_url,
)


def test_postman_url_and_headers_helpers():
    assert _to_path_from_url("https://api.example.org/users?x=1") == "/users?x=1"
    base, path = _extract_postman_url("https://api.example.org/clients")
    assert base == "https://api.example.org"
    assert path == "/clients"
    assert _extract_postman_url("{{server}}/users")[1] == "/users"
    assert _extract_postman_url("relative")[1] == "/relative"
    base, path = _extract_postman_url(
        {
            "protocol": "https",
            "host": ["api", "example", "org"],
            "path": ["v1", "users"],
            "query": [{"key": "q", "value": "1"}, {"key": "skip", "disabled": True}],
        }
    )
    assert base == "https://api.example.org"
    assert path == "/v1/users?q=1"
    assert _extract_postman_url({"raw": "https://api.example.org/raw"})[1] == "/raw"
    assert _extract_postman_url(None) == (None, "/")

    headers = _extract_postman_headers(
        [
            {"key": "Accept", "value": "application/json"},
            {"key": "X-Skip", "value": "1", "disabled": True},
            "bad",
        ]
    )
    assert headers == {"Accept": "application/json"}


def test_postman_body_flatten_and_convert():
    assert _extract_postman_body(None) == (None, None)
    assert _extract_postman_body({"mode": "raw", "raw": '{"a":1}'})[0] == {"a": 1}
    assert _extract_postman_body({"mode": "raw", "raw": "plain"})[1] == "plain"
    assert _extract_postman_body(
        {"mode": "urlencoded", "urlencoded": [{"key": "a", "value": "1"}, {"key": "b", "disabled": True}]}
    )[1] == {"a": "1"}

    items = _flatten_postman_items(
        [
            {"name": "nested", "item": [{"name": "leaf", "request": {"method": "GET", "url": "/x"}}]},
            {"name": "direct", "request": {"method": "POST", "url": "https://api.example.org/y"}},
            "skip",
        ]
    )
    assert len(items) == 2
    envs, selected = _extract_postman_variables(
        {"variable": [{"key": "token", "value": "secret"}, {"key": "skip", "disabled": True}]}
    )
    assert selected == "Imported Defaults"
    assert envs["Imported Defaults"]["token"] == "secret"

    converted = _convert_postman_collection(
        {
            "info": {"name": "Demo"},
            "variable": [{"key": "server", "value": "https://api.example.org"}],
            "item": [
                {
                    "name": "list",
                    "request": {
                        "method": "GET",
                        "url": "https://api.example.org/users",
                        "header": [{"key": "Accept", "value": "application/json"}],
                    },
                },
                {
                    "name": "create",
                    "request": {
                        "method": "POST",
                        "url": "{{server}}/users",
                        "body": {"mode": "raw", "raw": '{"name":"Ada"}'},
                    },
                },
            ],
        }
    )
    assert converted["base_url"] == "https://api.example.org"
    assert len(converted["steps"]) == 2
    assert converted["steps"][0]["headers"]["Accept"] == "application/json"
    assert converted["steps"][1]["json"] == {"name": "Ada"}


def test_insomnia_helpers_and_convert():
    assert _insomnia_url_to_parts("https://api.example.org/path?x=1") == ("https://api.example.org", "/path?x=1")
    assert _insomnia_url_to_parts("/relative")[1] == "/relative"
    assert _extract_insomnia_headers([{"name": "X-A", "value": "1"}, {"name": "", "value": "x"}]) == {"X-A": "1"}
    assert _resolve_base_url_from_environment({"server": "https://api.example.org"}) == "https://api.example.org"
    assert _resolve_base_url_from_environment({"base_url": "https://api.example.org/"}) == "https://api.example.org/"
    assert _resolve_base_url_from_environment({}) is None

    envs, selected = _extract_insomnia_v5_environments(
        {
            "environments": {
                "name": "Base",
                "data": {"token": "base"},
                "subEnvironments": [
                    {"name": "dev", "data": {"server": "https://dev.example.org"}},
                    {"name": "prod", "data": {"server": "https://prod.example.org"}},
                ],
            }
        }
    )
    assert selected == "dev"
    assert envs["dev"]["token"] == "base"
    assert envs["dev"]["server"] == "https://dev.example.org"

    legacy = _convert_insomnia_export(
        {
            "_type": "export",
            "__export_format": 4,
            "resources": [
                {"_id": "env_1", "_type": "environment", "name": "dev", "data": {"server": "https://api.example.org"}},
                {
                    "_id": "req_1",
                    "_type": "request",
                    "name": "list",
                    "method": "GET",
                    "url": "https://api.example.org/users",
                    "headers": [{"name": "Accept", "value": "application/json"}],
                    "parameters": [{"name": "q", "value": "1"}],
                },
                {
                    "_id": "req_2",
                    "_type": "request",
                    "name": "create",
                    "method": "POST",
                    "url": "{{ _.server }}/users",
                    "body": {"mimeType": "application/json", "text": '{"name":"Ada"}'},
                },
            ],
        }
    )
    assert len(legacy["steps"]) == 2
    assert legacy["steps"][0]["path"].endswith("q=1")
    assert legacy["steps"][1]["json"] == {"name": "Ada"}

    v5 = _convert_insomnia_v5_collection(
        {
            "type": "collection.insomnia.rest/5",
            "name": "Demo",
            "collection": [
                {
                    "name": "list",
                    "method": "GET",
                    "url": "https://api.example.org/users",
                    "headers": [{"name": "Accept", "value": "application/json"}],
                    "authentication": {"type": "bearer", "token": "secret"},
                },
                {
                    "name": "create",
                    "method": "POST",
                    "url": "/users",
                    "body": {"mimeType": "application/json", "text": '{"ok":true}'},
                },
            ],
            "environments": {
                "name": "Base",
                "data": {"server": "https://api.example.org"},
                "subEnvironments": [{"name": "dev", "data": {}}],
            },
        }
    )
    assert len(v5["steps"]) == 2
    assert v5["steps"][0]["headers"]["Authorization"] == "Bearer secret"
    assert v5["steps"][1]["json"] == {"ok": True}
