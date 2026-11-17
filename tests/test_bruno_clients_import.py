from __future__ import annotations

from tools.bruno_export import collection_to_bruno, step_to_bruno_item
from tools.bruno_import import bruno_suggested_name, convert_bruno_collection
from tools.scenario_runner import coerce_structured_value, resolve_foreach_items


def test_coerce_python_literal_env_lists():
    wallets = (
        "[{'client_name': 'Unime', 'client_id': 'abc', "
        "'redirect_uri': ['unime://callback']}]"
    )
    parsed = coerce_structured_value(wallets)
    assert isinstance(parsed, list) and parsed[0]["client_id"] == "abc"
    assert coerce_structured_value("['epiauth.issuer.dev.eduwallet.nl']") == [
        "epiauth.issuer.dev.eduwallet.nl"
    ]
    assert coerce_structured_value("[]") == []


def test_bruno_clients_folder_infers_foreach_and_parses_env_lists():
    payload = {
        "uid": "col",
        "name": "OAuth2 Server",
        "version": "1",
        "items": [
            {
                "uid": "folder",
                "name": "Clients",
                "type": "folder",
                "items": [
                    {
                        "uid": "1",
                        "name": "Clients",
                        "type": "http-request",
                        "request": {
                            "url": "/clients/",
                            "method": "GET",
                            "headers": [{"name": "X-API-KEY", "value": "{{api_key}}", "enabled": True}],
                            "assertions": [{"name": "res.status", "value": "eq 200", "enabled": True}],
                        },
                    },
                    {
                        "uid": "2",
                        "name": "Delete Issuers",
                        "type": "http-request",
                        "request": {
                            "url": "/clients/{{item}}",
                            "method": "DELETE",
                            "assertions": [{"name": "res.status", "value": "eq 200", "enabled": True}],
                        },
                    },
                    {
                        "uid": "3",
                        "name": "Register Issuer",
                        "type": "http-request",
                        "request": {
                            "url": "/register",
                            "method": "POST",
                            "body": {
                                "mode": "json",
                                "json": '{"client_id": "{{item}}", "redirect_uris": []}',
                            },
                            "assertions": [
                                {"name": "res.status", "value": "eq [200, 201]", "enabled": True}
                            ],
                        },
                    },
                    {
                        "uid": "4",
                        "name": "register unime",
                        "type": "http-request",
                        "request": {
                            "url": "https://epi.as.dev.eduwallet.nl/register",
                            "method": "POST",
                            "body": {
                                "mode": "json",
                                "json": (
                                    '{"client_id": "{{item.client_id}}", '
                                    '"redirect_uris": "{{item.redirect_uri}}", '
                                    '"client_name": "{{item.client_name}}"}'
                                ),
                            },
                            "assertions": [{"name": "res.status", "value": "eq 200", "enabled": True}],
                        },
                        "settings": {"followRedirects": True, "maxRedirects": 5},
                    },
                ],
            }
        ],
        "environments": [
            {
                "name": "epi.as.dev.eduwallet.nl",
                "variables": [
                    {"name": "api_key", "value": "key", "enabled": True},
                    {
                        "name": "issuers",
                        "value": "['epiauth.issuer.dev.eduwallet.nl']",
                        "enabled": True,
                    },
                    {
                        "name": "wallets",
                        "value": (
                            "[{'client_name': 'Unime', 'client_id': '3FA30B6A', "
                            "'redirect_uri': ['unime://callback']}]"
                        ),
                        "enabled": True,
                    },
                ],
            }
        ],
        "root": {"docs": "", "meta": {"name": "OAuth2 Server"}},
    }

    assert bruno_suggested_name(payload) == "Clients"
    imported = convert_bruno_collection(payload)
    assert imported["name"] == "Clients"
    assert [step["name"] for step in imported["steps"]] == [
        "Clients",
        "Delete Issuers",
        "Register Issuer",
        "register unime",
    ]

    by_name = {step["name"]: step for step in imported["steps"]}
    assert "foreach" not in by_name["Clients"]
    assert by_name["Delete Issuers"]["foreach"] == "{{ env.issuers }}"
    assert by_name["Register Issuer"]["foreach"] == "{{ env.issuers }}"
    assert by_name["Register Issuer"]["expected_status"] == [200, 201]
    assert by_name["register unime"]["foreach"] == "{{ env.wallets }}"
    assert by_name["register unime"]["follow_redirects"] is True
    assert by_name["Clients"]["expected_status"] == 200

    env = imported["environments"]["epi.as.dev.eduwallet.nl"]
    assert env["issuers"] == ["epiauth.issuer.dev.eduwallet.nl"]
    assert env["wallets"][0]["client_id"] == "3FA30B6A"

    context = {"env": env, "vars": {}, "constants": {}}
    assert resolve_foreach_items(by_name["Delete Issuers"], context, {}) == env["issuers"]
    assert resolve_foreach_items(by_name["register unime"], context, {}) == env["wallets"]


def test_bruno_export_preserves_foreach_and_list_envs():
    exported = collection_to_bruno(
        {
            "name": "OAuth2 Server",
            "environments": {
                "epi": {
                    "issuers": ["epiauth.issuer.dev.eduwallet.nl"],
                    "wallets": [{"client_id": "abc", "redirect_uri": ["x://y"]}],
                }
            },
        },
        [
            (
                "Clients.json",
                {
                    "name": "Clients",
                    "steps": [
                        {
                            "name": "Register Issuer",
                            "method": "POST",
                            "path": "/register",
                            "foreach": "{{ env.issuers }}",
                            "json": {"client_id": "{{ item }}"},
                            "expected_status": [200, 201],
                        }
                    ],
                },
            )
        ],
    )
    folder = exported["items"][0]
    step = folder["items"][0]
    assert step["foreach"] == "{{issuers}}"
    assert step["request"]["assertions"][0]["value"] == "eq [200, 201]"
    env_vars = {item["name"]: item["value"] for item in exported["environments"][0]["variables"]}
    assert env_vars["issuers"] == '["epiauth.issuer.dev.eduwallet.nl"]'
    assert '"client_id": "abc"' in env_vars["wallets"]

    round_trip = convert_bruno_collection(exported)
    assert round_trip["steps"][0]["foreach"] == "{{issuers}}"
    assert round_trip["environments"]["epi"]["issuers"] == ["epiauth.issuer.dev.eduwallet.nl"]


def test_step_to_bruno_item_keeps_report_fields():
    item = step_to_bruno_item(
        {
            "name": "Clients",
            "method": "GET",
            "path": "/clients/",
            "save_response_as": "clients",
            "report": True,
        },
        1,
    )
    assert item["save_response_as"] == "clients"
    assert item["report"] is True
