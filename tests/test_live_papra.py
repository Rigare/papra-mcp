"""Opt-in integration checks against a disposable, version-pinned Papra server."""

import asyncio
import json
import os
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

import papra_mcp

EXPECTED_VERSION = "0.3.0"
EXPECTED_COMMIT = "648c223b807500f0258c822e0321d6ef4964f064"
LIVE_BASE_URL = os.environ.get("PAPRA_LIVE_BASE_URL", "http://127.0.0.1:37065").rstrip("/")
ALL_SCOPES = [
    f"{resource}:{operation}"
    for resource in ("organizations", "documents", "tags", "custom-properties")
    for operation in ("create", "read", "update", "delete")
]

pytestmark = pytest.mark.skipif(
    os.environ.get("PAPRA_RUN_LIVE_TESTS") != "1",
    reason="Set PAPRA_RUN_LIVE_TESTS=1 only for a disposable local Papra server",
)


def _validate_loopback_url() -> None:
    url = httpx.URL(LIVE_BASE_URL)
    assert url.scheme == "http"
    assert url.host in {"127.0.0.1", "::1", "localhost"}
    assert url.path in {"", "/"}
    assert not url.query and not url.fragment and not url.userinfo


_validate_loopback_url()


async def _bootstrap() -> dict[str, Any]:
    async with httpx.AsyncClient(
        base_url=LIVE_BASE_URL,
        headers={"Origin": LIVE_BASE_URL},
        follow_redirects=False,
        timeout=30,
    ) as session:
        response = await session.get("/api/config")
        assert response.status_code == 200, response.text
        config = response.json()["config"]
        assert config["version"] == EXPECTED_VERSION
        assert config["gitCommitSha"] == EXPECTED_COMMIT

        unique = uuid.uuid4().hex
        response = await session.post(
            "/api/auth/sign-up/email",
            json={
                "email": f"ope11-{unique}@example.invalid",
                "password": f"{secrets.token_urlsafe(24)}aA1!",
                "name": "OPE-11 disposable test user",
            },
        )
        assert response.status_code == 200, response.text

        response = await session.get("/api/auth/get-session")
        assert response.status_code == 200, response.text
        user_id = response.json()["user"]["id"]
        assert user_id.startswith("usr_")

        tokens = {}
        for name, scopes in (("full", ALL_SCOPES), ("minimal", ["documents:read"])):
            response = await session.post(
                "/api/api-keys",
                json={"name": f"OPE-11 {name} {unique}", "permissions": scopes},
            )
            assert response.status_code == 200, response.text
            tokens[name] = response.json()["token"]

        return {"user_id": user_id, **tokens}


@pytest.fixture(scope="module")
def live_server() -> dict[str, Any]:
    return asyncio.run(_bootstrap())


@asynccontextmanager
async def _adapter_client(api_key: str) -> AsyncIterator[httpx.AsyncClient]:
    original = papra_mcp._client
    async with httpx.AsyncClient(
        base_url=LIVE_BASE_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        follow_redirects=False,
        timeout=30,
    ) as client:
        papra_mcp._client = client
        try:
            yield client
        finally:
            papra_mcp._client = original


async def _create_organization() -> str:
    result = await papra_mcp.papra_create_organization(
        papra_mcp.CreateOrgInput(name=f"OPE-11 {uuid.uuid4().hex[:16]}")
    )
    return json.loads(result)["organization"]["id"]


async def _create_document(organization_id: str, name: str) -> str:
    result = await papra_mcp.papra_create_text_document(
        papra_mcp.CreateTextDocInput(
            organization_id=organization_id,
            file_name=name,
            text_content=f"Disposable OPE-11 content for {name}",
        )
    )
    return json.loads(result)["document"]["id"]


async def _create_definition(
    organization_id: str,
    name: str,
    property_type: str,
    options: list[str] | None = None,
) -> str:
    result = await papra_mcp.papra_create_custom_property(
        papra_mcp.CreatePropertyDefInput(
            organization_id=organization_id,
            name=name,
            type=property_type,
            options=options,
        )
    )
    return json.loads(result)["propertyDefinition"]["id"]


async def _definitions(organization_id: str) -> dict[str, dict[str, Any]]:
    result = await papra_mcp.papra_list_custom_properties(
        papra_mcp.OrgBase(organization_id=organization_id)
    )
    definitions = json.loads(result)["propertyDefinitions"]
    return {definition["id"]: definition for definition in definitions}


async def _property_values(organization_id: str, document_id: str) -> dict[str, Any]:
    result = await papra_mcp.papra_list_document_custom_properties(
        papra_mcp.DocBase(organization_id=organization_id, document_id=document_id)
    )
    values = json.loads(result)["customProperties"]
    return {value["propertyDefinitionId"]: value["value"] for value in values}


async def _set_property(
    organization_id: str,
    document_id: str,
    property_id: str,
    property_type: str,
    value: Any,
) -> None:
    await papra_mcp.papra_set_document_custom_property(
        papra_mcp.SetDocPropertyInput(
            organization_id=organization_id,
            document_id=document_id,
            property_definition_id=property_id,
            property_type=property_type,
            value=value,
        )
    )


def _value_path(organization_id: str, document_id: str, property_id: str) -> str:
    return (
        f"/api/organizations/{organization_id}/documents/{document_id}"
        f"/custom-properties/{property_id}"
    )


@pytest.mark.asyncio
async def test_real_api_key_scopes_and_current_key(live_server):
    async with _adapter_client(live_server["full"]):
        organization_id = await _create_organization()

    async with _adapter_client(live_server["minimal"]):
        current = json.loads(await papra_mcp.papra_check_api_key())
        assert current["apiKey"]["permissions"] == ["documents:read"]

        documents = json.loads(
            await papra_mcp.papra_list_documents(
                papra_mcp.ListDocsInput(organization_id=organization_id)
            )
        )
        assert documents["documents"] == []

        with pytest.raises(ToolError, match="401"):
            await papra_mcp.papra_get_organization(
                papra_mcp.OrgBase(organization_id=organization_id)
            )
        with pytest.raises(ToolError, match="401"):
            await papra_mcp.papra_create_organization(
                papra_mcp.CreateOrgInput(name="OPE-11 must be denied")
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/organizations/org_111111111111111111111111/documents/doc_222222222222222222222222/restore"),
        ("DELETE", "/api/organizations/org_111111111111111111111111/documents/trash/doc_222222222222222222222222"),
        ("DELETE", "/api/organizations/org_111111111111111111111111/documents/trash"),
        ("POST", "/api/organizations/org_111111111111111111111111/tagging-rules/tr_333333333333333333333333/apply"),
    ],
)
async def test_session_only_routes_reject_even_a_full_api_key(live_server, method, path):
    async with _adapter_client(live_server["full"]) as client:
        response = await client.request(method, path)

    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "auth.unauthorized",
        "message": "Unauthorized",
    }


@pytest.mark.asyncio
async def test_real_property_validators_and_replacement_semantics(live_server):
    async with _adapter_client(live_server["full"]) as client:
        organization_id = await _create_organization()
        document_id = await _create_document(organization_id, "properties.txt")
        other_document_id = await _create_document(organization_id, "related-one.txt")
        third_document_id = await _create_document(organization_id, "related-two.txt")

        select_id = await _create_definition(organization_id, "Status", "select", ["Open", "Closed"])
        multi_id = await _create_definition(
            organization_id, "Review states", "multi_select", ["First", "Second"]
        )
        documents_id = await _create_definition(organization_id, "Related files", "document_relation")
        users_id = await _create_definition(organization_id, "Reviewers", "user_relation")

        definitions = await _definitions(organization_id)
        select_options = {option["name"]: option["id"] for option in definitions[select_id]["options"]}
        multi_options = {option["name"]: option["id"] for option in definitions[multi_id]["options"]}
        assert len(select_options) == len(multi_options) == 2

        await _set_property(organization_id, document_id, select_id, "select", select_options["Open"])
        await papra_mcp.papra_update_custom_property(
            papra_mcp.UpdatePropertyDefInput(
                organization_id=organization_id,
                property_definition_id=select_id,
                name="Status",
                options=[
                    papra_mcp.PropertyOptionInput(
                        id=select_options["Open"], name="Open renamed"
                    )
                ],
            )
        )
        definitions = await _definitions(organization_id)
        assert [(option["id"], option["name"]) for option in definitions[select_id]["options"]] == [
            (select_options["Open"], "Open renamed")
        ]

        replacement = papra_mcp.UpdatePropertyDefInput(
            organization_id=organization_id,
            property_definition_id=select_id,
            name="Status",
            options=[
                papra_mcp.PropertyOptionInput(id=select_options["Open"], name="Open renamed"),
                papra_mcp.PropertyOptionInput(name="New"),
            ],
        )
        await papra_mcp.papra_update_custom_property(replacement)
        definitions = await _definitions(organization_id)
        options = definitions[select_id]["options"]
        assert [(option["id"], option["name"]) for option in options[:1]] == [
            (select_options["Open"], "Open renamed")
        ]
        assert len(options) == 2
        assert options[1]["name"] == "New"
        assert options[1]["id"].startswith("cpso_")

        response = await client.put(
            f"/api/organizations/{organization_id}/custom-properties/{select_id}",
            json={
                "options": [
                    {"id": select_options["Open"], "name": "Open renamed"},
                    {"id": options[1]["id"], "name": "New"},
                ]
            },
        )
        assert response.status_code == 500
        assert response.json()["error"] == {
            "code": "internal.error",
            "message": "An error occurred",
        }

        response = await client.put(
            f"/api/organizations/{organization_id}/custom-properties/{select_id}",
            json={"options": [{"id": select_options["Open"]}]},
        )
        assert response.status_code == 400
        assert response.json()["error"] == {
            "code": "custom_property_definition.invalid_update",
            "message": "The provided update data is invalid for this property type.",
        }

        two_values = [multi_options["First"], multi_options["Second"]]
        for value, expected in (
            (two_values, set(two_values)),
            (two_values[:1], {two_values[0]}),
        ):
            await _set_property(organization_id, document_id, multi_id, "multi_select", value)
            values = await _property_values(organization_id, document_id)
            assert {item["optionId"] for item in values[multi_id]} == expected

        await _set_property(organization_id, document_id, multi_id, "multi_select", [])
        assert multi_id not in await _property_values(organization_id, document_id)

        two_documents = [other_document_id, third_document_id]
        for value, expected in (
            (two_documents, set(two_documents)),
            (two_documents[:1], {other_document_id}),
        ):
            await _set_property(organization_id, document_id, documents_id, "document_relation", value)
            values = await _property_values(organization_id, document_id)
            assert {item["documentId"] for item in values[documents_id]} == expected

        await _set_property(organization_id, document_id, documents_id, "document_relation", [])
        assert documents_id not in await _property_values(organization_id, document_id)

        await _set_property(
            organization_id, document_id, users_id, "user_relation", [live_server["user_id"]]
        )
        values = await _property_values(organization_id, document_id)
        assert [item["userId"] for item in values[users_id]] == [live_server["user_id"]]
        await _set_property(organization_id, document_id, users_id, "user_relation", [])
        assert users_id not in await _property_values(organization_id, document_id)

        for property_id, invalid_value in (
            (multi_id, multi_options["First"]),
            (documents_id, other_document_id),
            (users_id, live_server["user_id"]),
        ):
            response = await client.put(
                _value_path(organization_id, document_id, property_id),
                json={"value": invalid_value},
            )
            assert response.status_code == 400
            assert response.json()["error"] == {
                "code": "custom_property_value.invalid",
                "message": "The provided value is invalid for this property type.",
            }

        await papra_mcp.papra_clear_document_custom_property(
            papra_mcp.DocPropertyBase(
                organization_id=organization_id,
                document_id=document_id,
                property_definition_id=select_id,
            )
        )
        assert select_id not in await _property_values(organization_id, document_id)
