from __future__ import annotations

from tools.bruno_export import (
    bruno_export_filename,
    collection_to_bruno,
    convert_placeholders,
)


def test_convert_placeholders_strips_prefixes_and_defaults():
    assert convert_placeholders("{{ env.server }}/users/{{ vars.user_id }}") == "{{server}}/users/{{user_id}}"
    assert convert_placeholders('{{ uid : 0 }}') == "{{uid}}"
    assert convert_placeholders('{{ server : "https://api.example.org" }}') == "{{server}}"
    assert convert_placeholders({"token": "{{ _.token }}"}) == {"token": "{{token}}"}
    assert convert_placeholders("/sample-path/{{ encode( vars.sample_url) }}") == "/sample-path/{{sample_url}}"


def test_collection_to_bruno_maps_scenarios_and_envs():
    collection = {
        "name": "demo",
        "description": "Demo collection",
        "environments": {
            "public": {
                "server": "https://jsonplaceholder.typicode.com",
                "posts_url": "{{ server }}/posts",
            }
        },
    }
    scenarios = [
        (
            "lookup_user.json",
            {
                "name": "lookup_user",
                "description": "Lookup",
                "steps": [
                    {
                        "name": "get_user",
                        "method": "GET",
                        "path": "{{ env.server }}/users/{{ env.user_id }}",
                        "expected_status": 200,
                        "headers": {"Accept": "application/json"},
                    },
                    {
                        "name": "create",
                        "method": "POST",
                        "path": "{{ env.server }}/posts",
                        "json": {"title": "{{ vars.title }}", "userId": "{{ env.user_id }}"},
                        "auth": {"type": "bearer", "token": "{{ env.token }}"},
                    },
                ],
            },
        )
    ]
    exported = collection_to_bruno(collection, scenarios)
    assert exported["name"] == "demo"
    assert exported["version"] == "1"
    assert exported["root"]["docs"] == "Demo collection"
    assert len(exported["items"]) == 1
    folder = exported["items"][0]
    assert folder["type"] == "folder"
    assert folder["name"] == "lookup_user"
    assert len(folder["items"]) == 2
    get_req = folder["items"][0]
    assert get_req["type"] == "http-request"
    assert get_req["request"]["method"] == "GET"
    assert get_req["request"]["url"] == "{{server}}/users/{{user_id}}"
    assert get_req["request"]["headers"][0]["name"] == "Accept"
    assert get_req["request"]["assertions"][0]["name"] == "res.status"
    post_req = folder["items"][1]
    assert post_req["request"]["body"]["mode"] == "json"
    assert '"title": "{{title}}"' in post_req["request"]["body"]["json"]
    assert post_req["request"]["auth"]["mode"] == "bearer"
    assert post_req["request"]["auth"]["bearer"]["token"] == "{{token}}"
    assert len(exported["environments"]) == 1
    env = exported["environments"][0]
    assert env["name"] == "public"
    values = {item["name"]: item["value"] for item in env["variables"]}
    assert values["server"] == "https://jsonplaceholder.typicode.com"
    assert values["posts_url"] == "{{server}}/posts"
    assert bruno_export_filename("My Collection!") == "My-Collection.bruno.json"
