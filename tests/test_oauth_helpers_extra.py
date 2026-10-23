from __future__ import annotations

import json
import urllib.request

from tools.oauth_helpers import (
    ath_hash,
    b64url,
    b64url_json,
    form_encode,
    get_dpop_key,
    mock_attestation_jwt,
    query_param,
    reset_dpop_key,
    start_cimd_metadata_server,
    stop_cimd_metadata_servers,
    DPoPKey,
)


def test_b64url_and_ath_hash():
    assert b64url(b"hi") == "aGk"
    assert "=" not in b64url_json({"a": 1})
    digest = ath_hash("token-value")
    assert isinstance(digest, str) and len(digest) > 10


def test_dpop_key_proof_and_store():
    stop_cimd_metadata_servers()
    key = DPoPKey()
    assert key.jwk_public["kty"] == "EC"
    assert key.jkt
    proof = key.proof("POST", "https://idp.example.org/token", nonce="n1", access_token="at")
    parts = proof.split(".")
    assert len(parts) == 3

    first = get_dpop_key("coverage-key")
    again = get_dpop_key("coverage-key")
    assert first is again
    replaced = reset_dpop_key("coverage-key")
    assert replaced is not first
    assert get_dpop_key("coverage-key") is replaced


def test_mock_attestation_jwt_and_form_encode():
    token = mock_attestation_jwt("client-1", "https://aud.example.org")
    header, payload, sig = token.split(".")
    assert header and payload and sig
    assert form_encode(None) is None
    assert form_encode("already=encoded") == "already=encoded"
    assert "a=1" in form_encode({"a": 1, "b": None})
    assert form_encode(99) == "99"


def test_query_param_empty_url_and_cimd_server():
    assert query_param("", "code", "fallback") == "fallback"
    stop_cimd_metadata_servers()
    url = start_cimd_metadata_server(0, {"client_name": "coverage"}, advertise_host="127.0.0.1")
    reused = start_cimd_metadata_server(int(url.rsplit(":", 1)[1].split("/", 1)[0]), {"client_name": "x"})
    assert reused == url
    with urllib.request.urlopen(url, timeout=2) as response:
        body = json.loads(response.read().decode("utf-8"))
    assert body["client_id"] == url
    assert body["client_name"] == "coverage"
    stop_cimd_metadata_servers()
