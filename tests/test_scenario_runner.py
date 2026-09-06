from __future__ import annotations

from types import SimpleNamespace

from tools.scenario_runner import (
    apply_save,
    build_http_request,
    evaluate_expectations,
    exported_context_vars,
    format_curl_command,
    is_forbidden_api_host,
    is_forbidden_api_target,
    missing_var_tokens,
    path_get,
    preview_step_request,
    render_template,
    require_routable_api_targets,
    to_status_list,
    value_contains,
)


def test_forbidden_api_hosts():
    assert is_forbidden_api_host("localhost")
    assert is_forbidden_api_host("127.0.0.1")
    assert is_forbidden_api_host("host.docker.internal")
    assert is_forbidden_api_host("app.localhost")
    assert not is_forbidden_api_host("api.example.org")
    assert not is_forbidden_api_host("")
    assert is_forbidden_api_target("http://127.0.0.1:8080")
    assert not is_forbidden_api_target("{{ server }}")
    assert not is_forbidden_api_target("https://api.example.org")


def test_require_routable_api_targets_allows_empty_or_public_host():
    require_routable_api_targets({}, extra_urls=[""])
    require_routable_api_targets({"server": "https://api.example.org"})


def test_require_routable_api_targets_rejects_localhost():
    try:
        require_routable_api_targets({"server": "http://localhost:8080"})
    except SystemExit as exc:
        assert "localhost" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_path_get_and_value_contains():
    data = {"items": [{"id": 7, "name": "Ada"}], "token_type": "Bearer"}
    assert path_get(data, "items.0.name") == "Ada"
    assert path_get(data, "items.9.name", "missing") == "missing"
    assert value_contains(data, {"token_type": "bearer"})
    assert value_contains(data, {"items": [{"id": 7}]})
    assert not value_contains(data, {"items": [{"id": 8}]})


def test_render_template_env_vars_and_meta():
    context = {"vars": {"user_id": 42}, "env": {"token": "abc"}, "meta": {"environment": "staging"}}
    assert render_template("{{ vars.user_id }}", context, {}) == 42
    assert render_template("Bearer {{ env.token }}", context, {}) == "Bearer abc"
    assert render_template("{{ token }}", context, {}) == "abc"
    now = render_template("{{ meta.now }}", context, {})
    assert isinstance(now, str) and "T" in now
    unix = render_template("{{ meta.unix }}", context, {})
    assert isinstance(unix, int) and unix > 0
    assert render_template("{{ meta.environment }}", context, {}) == "staging"
    assert render_template("env={{ meta.environment }}", context, {}) == "env=staging"
    assert render_template('{{ meta.environment : "default" }}', {"vars": {}, "env": {}, "meta": {}}, {}) == "default"


def test_render_template_uses_placeholder_defaults():
    empty = {"vars": {}, "env": {}}
    assert render_template('{{ server : "https://api.example.org" }}', empty, {}) == "https://api.example.org"
    assert render_template("{{ token : 'none' }}", empty, {}) == "none"
    assert render_template("{{ port : 8080 }}", empty, {}) == "8080"
    assert render_template('Prefix {{ token : "x" }}', empty, {}) == "Prefix x"
    overridden = {"vars": {}, "env": {"server": "https://other.example.org", "token": ""}}
    assert render_template('{{ server : "https://api.example.org" }}', overridden, {}) == "https://other.example.org"
    assert render_template('{{ token : "fallback" }}', overridden, {}) == "fallback"
    assert render_template('{{ vars.user_id : "anon" }}', empty, {}) == "anon"
    assert render_template('{{ vars.user_id : "anon" }}', {"vars": {"user_id": 7}, "env": {}}, {}) == 7
    assert missing_var_tokens({"path": '{{ vars.user_id : "anon" }}'}, empty) == []
    assert missing_var_tokens({"path": "{{ vars.user_id }}"}, empty) == ["vars.user_id"]


def test_build_http_request_uses_defaulted_server():
    request = build_http_request(
        {"method": "GET", "path": '{{ server : "https://api.example.org" }}/clients'},
        "",
        {"env": {}, "vars": {}},
        {},
    )
    assert request["url"] == "https://api.example.org/clients"


def test_build_http_request_keeps_absolute_url():
    request = build_http_request(
        {"method": "GET", "path": "https://api.example.org/clients", "headers": {"X-API-Key": "{{ env.key }}"}},
        "",
        {"env": {"key": "secret"}, "vars": {}},
        {},
    )
    assert request["url"] == "https://api.example.org/clients"
    assert request["headers"]["X-API-Key"] == "secret"


def test_build_http_request_joins_relative_path():
    request = build_http_request({"method": "GET", "path": "users"}, "https://api.example.org", {"env": {}, "vars": {}}, {})
    assert request["url"] == "https://api.example.org/users"


def test_apply_save_and_expectations():
    context = {"vars": {}}
    response = SimpleNamespace(
        headers={"Authorization": "Bearer xyz", "Location": "https://api.example.org/cb?code=99"},
        status_code=201,
        url="https://api.example.org/users/5",
    )
    apply_save(
        {
            "save": {
                "user_id": "json.id",
                "auth": "headers.Authorization",
                "code": "url_query.code",
                "status": "status_code",
            },
            "save_response_as": "payload",
        },
        {"id": 5, "name": "Ada"},
        response,
        context,
        final_url="https://api.example.org/users/5?code=99",
        body_text='{"id":5}',
    )
    assert context["vars"]["user_id"] == 5
    assert context["vars"]["auth"] == "Bearer xyz"
    assert context["vars"]["code"] == "99"
    assert context["vars"]["status"] == 201
    assert context["vars"]["payload"]["name"] == "Ada"

    step = {
        "expected_status": [200, 201],
        "expected_json_contains": {"name": "Ada"},
        "expected_body_contains": "Ada",
        "expected_body_not_contains": "error",
    }
    assert evaluate_expectations(step, response, {"name": "Ada"}, '{"name":"Ada"}')
    assert not evaluate_expectations({**step, "expected_status": 200}, response, {"name": "Ada"}, '{"name":"Ada"}')


def test_exported_context_vars_and_status_list():
    assert exported_context_vars({"worker_id": 1, "user_id": 5, "empty": ""}, True) == {"user_id": 5}
    assert exported_context_vars({"user_id": 5, "other": 1}, ["user_id"]) == {"user_id": 5}
    assert to_status_list(201) == [201]
    assert to_status_list([200, "201"]) == [200, 201]


def test_format_curl_and_preview_absolute_step():
    curl = format_curl_command("POST", "https://api.example.org/items", {"Accept": "application/json"}, {"name": "Ada"})
    assert "curl -sS -X POST" in curl
    assert "https://api.example.org/items" in curl
    preview = preview_step_request(
        steps=[{"name": "list", "method": "GET", "path": "https://api.example.org/clients"}],
        step_index=0,
        base_url="",
        context={"vars": {}, "env": {}},
        random_generators={},
    )
    assert preview["request"]["url"] == "https://api.example.org/clients"
    assert preview["unresolved"] == []
