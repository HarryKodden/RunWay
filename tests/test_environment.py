from __future__ import annotations

from tools.scenario_runner import (
    apply_env_overrides,
    apply_collection_defaults,
    expand_environment_values,
    expand_placeholder_defaults,
    finalize_environment_values,
    is_collection,
    missing_environment_dependencies,
    parse_placeholder_token,
    resolve_base_url,
    resolve_environment_values,
)
from webapp.app import _base_url_from_steps, _get_scenario_environment_values


SCENARIO_WITH_ENVS = {
    "selected_environment": "",
    "environments": {
        "dev": {
            "server": "https://dev.example.org",
            "token": "dev-token",
        },
        "prod": {
            "server": "https://prod.example.org",
            "token": "",
        },
    },
    "steps": [
        {
            "method": "GET",
            "path": "https://public.example.org/clients",
            "expected_status": 200,
        }
    ],
}


def test_no_environment_selected_returns_empty_values():
    assert resolve_environment_values(SCENARIO_WITH_ENVS, "") == {}
    assert resolve_environment_values(SCENARIO_WITH_ENVS, None) == {}
    assert _get_scenario_environment_values(SCENARIO_WITH_ENVS, "") == {}
    assert _get_scenario_environment_values(SCENARIO_WITH_ENVS, None) == {}


def test_named_environment_is_used_when_selected():
    values = resolve_environment_values(SCENARIO_WITH_ENVS, "dev")
    assert values["server"] == "https://dev.example.org"
    assert values["token"] == "dev-token"
    assert resolve_base_url(SCENARIO_WITH_ENVS, "dev") == "https://dev.example.org"


def test_file_selected_environment_is_the_default():
    scenario = {**SCENARIO_WITH_ENVS, "selected_environment": "dev"}
    assert resolve_environment_values(scenario, None)["token"] == "dev-token"
    assert resolve_base_url(scenario, None) == "https://dev.example.org"


def test_absolute_steps_need_no_environment_values():
    missing = missing_environment_dependencies({}, SCENARIO_WITH_ENVS["steps"])
    assert missing == []


def test_missing_placeholder_is_reported():
    steps = [{"method": "GET", "path": "{{ env.token }}/users", "headers": {"Authorization": "Bearer {{ token }}"}}]
    missing = missing_environment_dependencies({}, steps)
    assert "token" in missing


def test_unused_empty_environment_keys_are_not_required():
    env = {"server": "https://api.example.org", "unused": ""}
    steps = [{"method": "GET", "path": "https://api.example.org/health"}]
    assert missing_environment_dependencies(env, steps) == []


def test_connection_key_satisfied_by_any_host():
    env = {"server": "https://api.example.org"}
    steps = [{"method": "GET", "path": "{{ base_url }}/health"}]
    assert missing_environment_dependencies(env, steps) == []


def test_expand_and_finalize_environment_values():
    values = expand_environment_values(
        {
            "server": "https://staging.example.org",
            "token_url": "{{ server }}/oauth/token",
        }
    )
    assert values["token_url"] == "https://staging.example.org/oauth/token"
    finalized = finalize_environment_values({"server": "https://staging.example.org"})
    assert finalized["server"] == "https://staging.example.org"
    assert finalized["base_url"] == "https://staging.example.org"


def test_meta_environment_and_url_named_environment_as_server():
    with_meta = finalize_environment_values(
        {"server": "{{ meta.environment }}"},
        selected_environment="https://ta.example.org",
    )
    assert with_meta["server"] == "https://ta.example.org"
    named = finalize_environment_values(
        {"api_key": "secret"},
        selected_environment="https://ta.example.org",
    )
    assert named["server"] == "https://ta.example.org"
    fqdn = finalize_environment_values(
        {"api_key": "secret"},
        selected_environment="ta.example.org",
    )
    assert fqdn["server"] == "https://ta.example.org"
    label = finalize_environment_values(
        {"api_key": "secret"},
        selected_environment="staging",
    )
    assert "server" not in label
    assert (
        resolve_base_url(
            {
                "selected_environment": "https://ta.example.org",
                "environments": {"https://ta.example.org": {"api_key": "x"}},
                "base_url": "",
            },
            None,
        )
        == "https://ta.example.org"
    )


def test_apply_env_overrides_nested_and_blank():
    values = apply_env_overrides({"token": "old", "nested": {"id": "1"}}, {"token": "new", "nested.id": "2", "blank": ""})
    assert values["token"] == "new"
    assert values["nested"]["id"] == "2"
    assert "blank" not in values


def test_apply_collection_defaults_merges_environments_without_forcing_selection():
    child = {"environments": {"dev": {"token": "child"}}, "selected_environment": "", "steps": []}
    collection = {
        "environments": {"dev": {"server": "https://collection.example.org", "token": "collection"}},
        "selected_environment": "",
        "base_url": "",
    }
    merged = apply_collection_defaults(child, collection)
    assert merged["environments"]["dev"]["server"] == "https://collection.example.org"
    assert merged["environments"]["dev"]["token"] == "collection"
    assert merged.get("selected_environment", "") == ""


def test_placeholder_default_inside_environment_values():
    expanded = expand_environment_values(
        {
            "server": "https://jsonplaceholder.typicode.com",
            "posts_url": "{{ server }}/posts",
            "user_id": "{{ uid : 0 }}",
        }
    )
    assert expanded["posts_url"] == "https://jsonplaceholder.typicode.com/posts"
    assert expanded["user_id"] == "0"
    overridden = expand_environment_values(
        {
            "uid": "7",
            "user_id": "{{ uid : 0 }}",
        }
    )
    assert overridden["user_id"] == "7"
    finalized = finalize_environment_values(
        {
            "server": "https://jsonplaceholder.typicode.com",
            "user_id": "{{ uid : 0 }}",
        }
    )
    assert finalized["user_id"] == "0"
    assert missing_environment_dependencies(finalized, [{"path": "{{ env.user_id }}"}]) == []


def test_placeholder_default_is_used_when_unset():
    assert parse_placeholder_token('server : "https://api.example.org"') == ("server", "https://api.example.org")
    assert parse_placeholder_token("token : 'abc'") == ("token", "abc")
    assert parse_placeholder_token("port : 8080") == ("port", "8080")
    assert parse_placeholder_token("env.server") == ("env.server", None)
    steps = [{"method": "GET", "path": '{{ server : "https://api.example.org" }}/clients'}]
    assert missing_environment_dependencies({}, steps) == []
    assert expand_placeholder_defaults(steps[0]["path"]) == "https://api.example.org/clients"
    expanded = expand_environment_values({"token_url": '{{ server : "https://api.example.org" }}/token'})
    assert expanded["token_url"] == "https://api.example.org/token"


def test_placeholder_default_is_overridden_when_set():
    steps = [{"method": "GET", "path": '{{ token : "fallback" }}'}]
    assert missing_environment_dependencies({"token": "from-env"}, steps) == []
    mixed = [{"path": "{{ token }}"}, {"path": '{{ token : "fallback" }}'}]
    assert "token" in missing_environment_dependencies({}, mixed)


def test_is_collection_and_base_url_from_steps():
    assert is_collection({"scenarios": ["a.json"], "steps": []})
    assert not is_collection({"scenarios": [], "steps": [{"path": "/"}]})
    assert _base_url_from_steps([{"path": "/relative"}, {"path": "https://api.example.org/clients"}]) == "https://api.example.org"
    assert _base_url_from_steps([{"path": "/relative"}]) == ""
