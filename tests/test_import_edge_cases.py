from __future__ import annotations

import pytest

from tools.bruno_import import (
    bruno_suggested_name,
    convert_bruno_collection,
    is_bruno_collection,
)
from tools.openapi_import import (
    convert_openapi_document,
    is_openapi_document,
    openapi_suggested_name,
)


def test_bruno_detection_and_bodies():
    assert not is_bruno_collection([])  # type: ignore[arg-type]
    assert not is_bruno_collection({"items": [], "openapi": "3.0.0", "paths": {}})
    assert not is_bruno_collection({"items": [], "type": "collection.insomnia.rest/5"})
    assert is_bruno_collection({"version": "1", "root": {}, "items": []})
    assert is_bruno_collection(
        {"items": [{"type": "http-request", "name": "x", "request": {"method": "GET", "url": "/"}}]}
    )
    assert bruno_suggested_name({"name": "Demo"}) == "Demo"
    assert bruno_suggested_name({"root": {"meta": {"name": "FromRoot"}}}) == "FromRoot"
    assert bruno_suggested_name({}) == "bruno_import"

    with pytest.raises(ValueError):
        convert_bruno_collection({"items": []})

    imported = convert_bruno_collection(
        {
            "name": "edge",
            "root": {"docs": "docs"},
            "environments": [
                {
                    "name": "dev",
                    "variables": [
                        {"name": "server", "value": "https://api.example.org"},
                        {"name": "skip", "enabled": False, "value": "x"},
                        {"name": "", "value": "bad"},
                    ],
                }
            ],
            "items": [
                {
                    "type": "folder",
                    "name": "Users",
                    "items": [
                        {
                            "type": "http-request",
                            "name": "list",
                            "request": {
                                "method": "GET",
                                "url": "{{server}}/users",
                                "params": [
                                    {"name": "q", "value": "1", "type": "query"},
                                    {"name": "skip", "enabled": False, "type": "query"},
                                ],
                                "headers": [{"name": "Accept", "value": "application/json"}],
                                "docs": "List users",
                            },
                        },
                        {
                            "type": "http-request",
                            "name": "create",
                            "request": {
                                "method": "POST",
                                "url": "{{server}}/users",
                                "body": {"mode": "json", "json": '{"name":"Ada"}'},
                                "auth": {"mode": "bearer", "bearer": {"token": "t"}},
                            },
                        },
                        {
                            "type": "http-request",
                            "name": "form",
                            "request": {
                                "method": "POST",
                                "url": "{{server}}/form",
                                "body": {
                                    "mode": "formurlencoded",
                                    "formUrlEncoded": [{"name": "a", "value": "1"}],
                                },
                                "auth": {"mode": "basic", "basic": {"username": "u", "password": "p"}},
                            },
                        },
                        {
                            "type": "http-request",
                            "name": "text",
                            "request": {
                                "method": "POST",
                                "url": "{{server}}/text",
                                "body": {"mode": "text", "text": "hello"},
                            },
                        },
                        {"type": "graphql-request", "name": "skip-me", "request": {"method": "POST"}},
                    ],
                }
            ],
        }
    )
    assert imported["selected_environment"] == "dev"
    assert imported["description"] == "docs"
    assert any(step["name"].startswith("Users / ") for step in imported["steps"])
    create = next(step for step in imported["steps"] if step["name"].endswith("create"))
    assert create["json"] == {"name": "Ada"}
    assert create["auth"]["type"] == "bearer"
    form = next(step for step in imported["steps"] if step["name"].endswith("form"))
    assert form["data"] == {"a": "1"}
    assert form["auth"]["type"] == "basic"
    text = next(step for step in imported["steps"] if step["name"].endswith("text"))
    assert text["data"] == "hello"


def test_openapi_detection_refs_and_swagger2():
    assert not is_openapi_document({})
    assert not is_openapi_document({"openapi": "3.0.0"})
    assert openapi_suggested_name({"info": {"title": "Pets"}}) == "Pets"
    assert openapi_suggested_name({}) == "openapi_import"

    with pytest.raises(ValueError):
        convert_openapi_document({"openapi": "3.0.0", "paths": {}})

    doc = {
        "openapi": "3.0.0",
        "info": {"title": "Coverage API", "description": "desc"},
        "servers": [{"url": "https://api.example.org"}],
        "components": {
            "schemas": {
                "User": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string", "example": "Ada"},
                        "age": {"type": "integer"},
                        "active": {"type": "boolean"},
                        "tags": {"type": "array", "items": {"type": "string", "enum": ["x"]}},
                    },
                }
            }
        },
        "paths": {
            "/users/{id}": {
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "get": {
                    "operationId": "getUser",
                    "summary": "Get user",
                    "parameters": [
                        {"name": "verbose", "in": "query", "schema": {"type": "boolean", "default": True}},
                        {"name": "X-Trace", "in": "header", "example": "1"},
                    ],
                },
                "post": {
                    "summary": "Create user",
                    "requestBody": {
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/User"}},
                        }
                    },
                },
            },
            "/form": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "example": {"a": "1"},
                            }
                        }
                    }
                }
            },
        },
    }
    converted = convert_openapi_document(doc)
    assert converted["name"] == "Coverage API"
    assert converted["environments"]["default"]["server"] == "https://api.example.org"
    assert any(step["method"] == "GET" for step in converted["steps"])
    create = next(step for step in converted["steps"] if step["method"] == "POST" and "users" in step["path"])
    assert create["json"]["name"] == "Ada"
    form = next(step for step in converted["steps"] if "/form" in step["path"])
    assert form.get("json") == {"a": "1"} or form.get("data") == {"a": "1"}

    swagger = {
        "swagger": "2.0",
        "info": {"title": "Swagger"},
        "host": "api.example.org",
        "basePath": "/v1",
        "schemes": ["https"],
        "paths": {
            "/ping": {
                "get": {"operationId": "ping"},
                "post": {
                    "parameters": [
                        {
                            "in": "body",
                            "name": "body",
                            "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                        }
                    ]
                },
            }
        },
    }
    converted_sw = convert_openapi_document(swagger)
    assert converted_sw["environments"]["default"]["server"] == "https://api.example.org/v1"
    assert len(converted_sw["steps"]) >= 2
