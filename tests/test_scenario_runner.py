from __future__ import annotations

from types import SimpleNamespace

import requests

from tools.scenario_runner import (
    apply_save,
    build_http_request,
    configure_http_session,
    ensure_safe_request_url,
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


def test_forbidden_api_hosts_blocks_alternate_loopback_and_private_forms():
    for host in (
        "127.1",
        "127.0.1",
        "0x7f000001",
        "2130706433",
        "0177.0.0.1",
        "0x7f.0.0.1",
        "10.0.1.20",
        "192.168.1.1",
        "172.16.5.5",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "[::1]",
    ):
        assert is_forbidden_api_host(host), host
    assert is_forbidden_api_target("http://127.1:8080/api/health")
    assert is_forbidden_api_target("http://0x7f000001:8080/")
    assert is_forbidden_api_target("http://169.254.169.254/latest/meta-data/")
    # Public IP literals are also rejected; only FQDNs are allowed.
    assert is_forbidden_api_host("8.8.8.8")
    assert is_forbidden_api_host("2001:4860:4860::8888")
    assert is_forbidden_api_target("https://1.1.1.1/dns-query")


def test_require_routable_api_targets_allows_empty_or_public_host():
    require_routable_api_targets({}, extra_urls=[""])
    require_routable_api_targets({"server": "https://api.example.org"})


def test_require_routable_api_targets_rejects_localhost():
    try:
        require_routable_api_targets({"server": "http://localhost:8080"})
    except SystemExit as exc:
        assert "localhost" in str(exc).lower() or "fqdn" in str(exc).lower()
    else:
        raise AssertionError("expected SystemExit")


def test_require_routable_api_targets_rejects_short_loopback():
    try:
        require_routable_api_targets({"server": "http://127.1:8080"})
    except SystemExit:
        return
    raise AssertionError("expected SystemExit")


def test_ensure_safe_request_url_rejects_blocked_destinations(monkeypatch):
    import ipaddress

    monkeypatch.setattr(
        "tools.scenario_runner.resolve_host_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )
    assert ensure_safe_request_url("https://example.org/path")
    for url in (
        "http://127.1:8080/api/health",
        "http://0x7f000001/",
        "file:///etc/passwd",
        "http://169.254.169.254/",
        "http://[::1]/",
        "https://8.8.8.8/",
        "https://[2001:4860:4860::8888]/",
        "https://1.1.1.1/dns-query",
    ):
        try:
            ensure_safe_request_url(url)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {url}")


def test_ensure_safe_request_url_rejects_dns_to_private(monkeypatch):
    import ipaddress

    monkeypatch.setattr(
        "tools.scenario_runner.resolve_host_ips",
        lambda host: [ipaddress.ip_address("10.0.1.20")],
    )
    try:
        ensure_safe_request_url("https://internal.example.org/secret")
    except ValueError as exc:
        assert "internal.example.org" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_egress_allowlist_and_dns_pin(monkeypatch):
    import ipaddress
    import socket

    from tools.scenario_runner import host_permitted_by_allowlist, pinned_host_resolution

    monkeypatch.setenv("RUNWAY_ALLOWED_HOSTS", "example.org,")
    monkeypatch.delenv("RUNWAY_REQUIRE_HOST_ALLOWLIST", raising=False)
    assert host_permitted_by_allowlist("api.example.org")
    assert not host_permitted_by_allowlist("evil.example.com")

    monkeypatch.setattr(
        "tools.scenario_runner.resolve_host_ips",
        lambda host: [ipaddress.ip_address("93.184.216.34")],
    )
    assert ensure_safe_request_url("https://api.example.org/x")
    try:
        ensure_safe_request_url("https://evil.example.com/x")
    except ValueError as exc:
        assert "RUNWAY_ALLOWED_HOSTS" in str(exc)
    else:
        raise AssertionError("expected allowlist rejection")

    pinned = [ipaddress.ip_address("93.184.216.34")]
    with pinned_host_resolution("example.org", pinned):
        infos = socket.getaddrinfo("example.org", 443, type=socket.SOCK_STREAM)
    assert infos
    assert infos[0][4][0] == "93.184.216.34"


def test_exec_disabled_by_default_and_rejects_shell_strings(monkeypatch):
    from tools.scenario_runner import run_exec_step

    context: dict = {"vars": {}, "env": {}}
    ok, mismatch, _latency = run_exec_step(
        {"method": "EXEC", "command": "echo pwned"},
        context,
        {},
    )
    assert ok is False and mismatch is True
    assert "EXEC disabled" in context["vars"]["exec_error"]

    monkeypatch.setenv("RUNWAY_ALLOW_EXEC", "true")
    context = {"vars": {}, "env": {}}
    ok, mismatch, _latency = run_exec_step(
        {"method": "EXEC", "command": "echo pwned"},
        context,
        {},
    )
    assert ok is False and mismatch is True
    assert "argv list" in context["vars"]["exec_error"]

    context = {"vars": {}, "env": {}}
    ok, mismatch, _latency = run_exec_step(
        {"method": "EXEC", "command": ["true"]},
        context,
        {},
    )
    assert ok is True and mismatch is False


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


def test_render_template_encode_url_encodes_values():
    context = {
        "vars": {"sample_url": "https://example.org/a b?x=1"},
        "env": {"redirect_uri": "https://app.example.org/callback"},
    }
    assert (
        render_template("/sample-path/{{ encode( vars.sample_url) }}", context, {})
        == "/sample-path/https%3A%2F%2Fexample.org%2Fa%20b%3Fx%3D1"
    )
    assert (
        render_template("{{ encode(env.redirect_uri) }}", context, {})
        == "https%3A%2F%2Fapp.example.org%2Fcallback"
    )
    assert (
        render_template('{{ encode(vars.missing : "https://fallback.example.org/") }}', {"vars": {}, "env": {}}, {})
        == "https%3A%2F%2Ffallback.example.org%2F"
    )
    assert missing_var_tokens({"path": "{{ encode(vars.sample_url) }}"}, {"vars": {}}) == ["vars.sample_url"]
    assert missing_var_tokens(
        {"path": '{{ encode(vars.sample_url : "https://x") }}'},
        {"vars": {}},
    ) == []


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
                "user_id": "{{ json.id }}",
                "user_name": "{{ json.name }}",
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
    assert context["vars"]["user_name"] == "Ada"
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


def test_insecure_tls_disables_certificate_checks(monkeypatch):
    session = requests.Session()
    configure_http_session(session, {})
    assert session.verify is True
    configure_http_session(session, {"insecure_tls": True})
    assert session.verify is False
    configure_http_session(session, {"insecure_tls": "false"})
    assert session.verify is True
    monkeypatch.setenv("RUNWAY_INSECURE_TLS", "true")
    configure_http_session(session, {})
    assert session.verify is False
    configure_http_session(session, {"insecure_tls": False})
    assert session.verify is True
    curl = format_curl_command("GET", "https://oauth2-server.k3s.lab/.well-known/openid-configuration", insecure=True)
    assert "curl -sS -k -X GET" in curl


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
