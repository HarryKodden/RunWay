from __future__ import annotations

from tools.scenario_document import (
    build_scenario_document,
    is_sensitive_key,
    mask_sensitive_text,
    mask_structured,
)


def test_mask_sensitive_text_covers_auth_and_api_key_headers():
    assert "***" in mask_sensitive_text("Authorization: Bearer super-secret")
    assert "***" in mask_sensitive_text("X-API-KEY: 961DA3DF-1E79-47A7-B993-1CE87D438487")
    assert "***" in mask_sensitive_text("-H 'Authorization: Basic dXNlcjpwYXNz'")
    assert mask_sensitive_text("plain text") == "plain text"


def test_mask_structured_redacts_secret_keys():
    assert is_sensitive_key("api_key")
    assert is_sensitive_key("client_secret")
    assert not is_sensitive_key("issuers")
    masked = mask_structured(
        {
            "api_key": "secret-value",
            "issuers": ["a"],
            "nested": {"password": "p", "client_id": "c"},
        }
    )
    assert masked["api_key"] == "***"
    assert masked["issuers"] == ["a"]
    assert masked["nested"]["password"] == "***"
    assert masked["nested"]["client_id"] == "c"


def test_build_scenario_document_includes_steps_foreach_and_masked_curl():
    scenario = {
        "name": "Clients",
        "description": "Register OAuth clients.",
        "constants": {"note": "demo"},
        "environments": {
            "epi.as.dev.eduwallet.nl": {
                "api_key": "961DA3DF-SECRET",
                "issuers": ["epiauth.issuer.dev.eduwallet.nl"],
                "wallets": [{"client_id": "abc", "redirect_uri": ["x://y"]}],
            }
        },
        "selected_environment": "epi.as.dev.eduwallet.nl",
        "steps": [
            {
                "name": "Clients",
                "method": "GET",
                "path": "/clients/",
                "headers": {"X-API-KEY": "{{ env.api_key }}"},
                "expected_status": 200,
                "report": True,
            },
            {
                "name": "Delete Issuers",
                "method": "DELETE",
                "path": "/clients/{{ item }}",
                "foreach": "{{ env.issuers }}",
                "headers": {"X-API-KEY": "{{ env.api_key }}"},
                "expected_status": 200,
                "report": ["client_id"],
            },
            {
                "name": "Register wallet",
                "method": "POST",
                "path": "/register",
                "foreach": "{{ env.wallets }}",
                "headers": {"X-API-KEY": "{{ env.api_key }}"},
                "json": {
                    "client_id": "{{ item.client_id }}",
                    "redirect_uris": "{{ item.redirect_uri }}",
                },
                "expected_status": [200, 201],
                "save_response_as": "registration",
                "report": ["client_id", "client_secret"],
            },
        ],
    }
    result = build_scenario_document(scenario)
    markdown = result["markdown"]
    assert result["name"] == "Clients"
    assert result["step_count"] == 3
    assert "# Scenario: Clients" in markdown
    assert "Register OAuth clients." in markdown
    assert "## Constants" in markdown
    assert "## Environments" in markdown
    assert "`api_key`: `***`" in markdown
    assert "961DA3DF-SECRET" not in markdown
    assert "### 1. Clients" in markdown
    assert "### 2. Delete Issuers" in markdown
    assert "foreach" in markdown.lower()
    assert "Expects HTTP status in [200, 201]." in markdown
    assert "only these fields: `client_id`, `client_secret`" in markdown
    assert "```bash" in markdown
    assert "X-API-KEY: ***" in markdown
    assert "961DA3DF-SECRET" not in markdown
    assert "## Session handling" not in markdown


def test_build_scenario_document_describes_session_handling():
    result = build_scenario_document(
        {
            "name": "Session",
            "session_headers": ["X-Session-Id", "X-Protocol-Version"],
            "steps": [
                {"name": "Start", "method": "POST", "path": "https://api.example.org/start"},
                {
                    "name": "Skip",
                    "method": "POST",
                    "path": "https://api.example.org/skip",
                    "session_headers": False,
                },
                {"name": "Next", "method": "POST", "path": "https://api.example.org/next"},
            ],
        }
    )
    markdown = result["markdown"]
    assert "## Session handling" in markdown
    assert "- Session headers: `X-Session-Id`, `X-Protocol-Version`" in markdown
    assert "stored for the rest of this scenario" in markdown
    assert "{{ session.headers.X-Session-Id }}" in markdown
    assert "{{ vars.X-Session-Id }}" in markdown
    start, skip, nxt = markdown.split("### ")[1:]
    assert "Stores response headers `X-Session-Id`, `X-Protocol-Version`" in start
    assert "Sends stored session headers" not in start
    assert "# Session headers added after capture" not in start
    assert "Skips stored session headers on this request." in skip
    assert "# Session headers added after capture" not in skip
    assert "Sends stored session headers `X-Session-Id`, `X-Protocol-Version` on this request." in nxt
    assert "# Session headers added after capture: X-Session-Id, X-Protocol-Version" in nxt


def test_build_scenario_document_includes_collection_session_headers():
    result = build_scenario_document(
        {
            "name": "child",
            "steps": [
                {"name": "One", "method": "GET", "path": "https://api.example.org/one"},
                {"name": "Two", "method": "GET", "path": "https://api.example.org/two"},
            ],
        },
        collection={"name": "col", "session_headers": "X-Session-Id", "scenarios": ["child.json"]},
    )
    markdown = result["markdown"]
    assert "## Session handling" in markdown
    assert "`X-Session-Id`" in markdown
    assert "Sends stored session headers `X-Session-Id` on this request." in markdown
