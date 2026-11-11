"""Security and API-contract regressions for the supported Papra server version."""

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

import papra_mcp

ORG_ID = f"org_{'1' * 24}"
OTHER_ORG_ID = f"org_{'2' * 24}"
DOC_ID = f"doc_{'3' * 24}"
OTHER_DOC_ID = f"doc_{'4' * 24}"
TAG_ID = f"tag_{'5' * 24}"
PROPERTY_ID = f"cpd_{'6' * 24}"
OPTION_ID = f"cpso_{'7' * 24}"
OTHER_OPTION_ID = f"cpso_{'8' * 24}"
USER_ID = f"usr_{'9' * 24}"
OTHER_USER_ID = f"usr_{'a' * 24}"

INVALID_IDS = [
    "",
    ".",
    "..",
    "../other",
    "org/another",
    "org\\another",
    "org?another",
    "org#another",
    "%2f",
    "%2e",
    "org_%2e%2e",
    "org_%2F",
    "org_%252f",
    "org_%25252f",
    "org_../../../other",
    ORG_ID.upper(),
    ORG_ID + "/",
]

READ_TOOLS = {
    "papra_check_api_key",
    "papra_list_organizations",
    "papra_get_organization",
    "papra_list_documents",
    "papra_search_documents",
    "papra_list_deleted_documents",
    "papra_get_document",
    "papra_get_document_content",
    "papra_get_document_statistics",
    "papra_get_document_activity",
    "papra_list_tags",
    "papra_list_custom_properties",
    "papra_get_custom_property",
    "papra_list_document_custom_properties",
}
CREATE_TOOLS = {
    "papra_create_organization",
    "papra_create_document",
    "papra_create_text_document",
    "papra_create_tag",
    "papra_create_custom_property",
}
DESTRUCTIVE_IDEMPOTENT_TOOLS = {
    "papra_update_organization",
    "papra_delete_organization",
    "papra_update_document",
    "papra_delete_document",
    "papra_update_tag",
    "papra_delete_tag",
    "papra_remove_tag_from_document",
    "papra_delete_custom_property",
    "papra_set_document_custom_property",
    "papra_clear_document_custom_property",
}
EXPECTED_TOOLS = (
    READ_TOOLS
    | CREATE_TOOLS
    | DESTRUCTIVE_IDEMPOTENT_TOOLS
    | {
        "papra_add_tag_to_document",
        "papra_reprocess_document",
        "papra_update_custom_property",
    }
)
SESSION_ONLY_TOOLS = {
    "papra_restore_document",
    "papra_delete_document_permanently",
    "papra_empty_trash",
    "papra_apply_tagging_rule",
}


def _model_case(model, kwargs, field):
    return model, kwargs, field


ID_MODEL_CASES = [
    _model_case(papra_mcp.OrgBase, {"organization_id": ORG_ID}, "organization_id"),
    _model_case(
        papra_mcp.DocBase,
        {"organization_id": ORG_ID, "document_id": DOC_ID},
        "document_id",
    ),
    _model_case(
        papra_mcp.UpdateTagInput,
        {"organization_id": ORG_ID, "tag_id": TAG_ID},
        "tag_id",
    ),
    _model_case(
        papra_mcp.TagIdInput,
        {"organization_id": ORG_ID, "tag_id": TAG_ID},
        "tag_id",
    ),
    _model_case(
        papra_mcp.DocTagInput,
        {"organization_id": ORG_ID, "document_id": DOC_ID, "tag_id": TAG_ID},
        "tag_id",
    ),
    _model_case(
        papra_mcp.PropertyDefBase,
        {"organization_id": ORG_ID, "property_definition_id": PROPERTY_ID},
        "property_definition_id",
    ),
    _model_case(
        papra_mcp.DocPropertyBase,
        {
            "organization_id": ORG_ID,
            "document_id": DOC_ID,
            "property_definition_id": PROPERTY_ID,
        },
        "property_definition_id",
    ),
    _model_case(
        papra_mcp.PropertyOptionInput,
        {"id": OPTION_ID, "name": "Open"},
        "id",
    ),
]


@pytest.mark.parametrize(("model", "kwargs", "field"), ID_MODEL_CASES)
def test_models_accept_actual_papra_ids(model, kwargs, field):
    assert getattr(model(**kwargs), field) == kwargs[field]


@pytest.mark.parametrize("bad_id", INVALID_IDS)
@pytest.mark.parametrize(("model", "kwargs", "field"), ID_MODEL_CASES)
def test_models_reject_navigation_and_non_papra_ids(model, kwargs, field, bad_id):
    with pytest.raises(ValidationError):
        model(**{**kwargs, field: bad_id})


@pytest.mark.parametrize(("model", "kwargs", "field"), ID_MODEL_CASES)
def test_resource_ids_are_bound_to_their_prefix(model, kwargs, field):
    wrong_prefix_id = TAG_ID if field == "organization_id" else OTHER_ORG_ID
    with pytest.raises(ValidationError):
        model(**{**kwargs, field: wrong_prefix_id})


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", INVALID_IDS)
async def test_registered_tool_rejects_bad_ids_before_http(mock_client, bad_id):
    with pytest.raises(ToolError):
        await papra_mcp.mcp.call_tool(
            "papra_clear_document_custom_property",
            {
                "params": {
                    "organization_id": ORG_ID,
                    "document_id": DOC_ID,
                    "property_definition_id": bad_id,
                }
            },
        )

    mock_client.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_ids_keep_their_normalized_origin_method_and_route():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    original = papra_mcp._client
    async with httpx.AsyncClient(
        base_url="https://papra.example",
        transport=httpx.MockTransport(handler),
    ) as client:
        papra_mcp._client = client
        try:
            await papra_mcp.papra_clear_document_custom_property(
                papra_mcp.DocPropertyBase(
                    organization_id=ORG_ID,
                    document_id=DOC_ID,
                    property_definition_id=PROPERTY_ID,
                )
            )
            await papra_mcp.papra_remove_tag_from_document(
                papra_mcp.DocTagInput(
                    organization_id=OTHER_ORG_ID,
                    document_id=OTHER_DOC_ID,
                    tag_id=TAG_ID,
                )
            )
        finally:
            papra_mcp._client = original

    assert [(request.method, str(request.url)) for request in requests] == [
        (
            "DELETE",
            f"https://papra.example/api/organizations/{ORG_ID}/documents/{DOC_ID}"
            f"/custom-properties/{PROPERTY_ID}",
        ),
        (
            "DELETE",
            f"https://papra.example/api/organizations/{OTHER_ORG_ID}/documents"
            f"/{OTHER_DOC_ID}/tags/{TAG_ID}",
        ),
    ]


@pytest.mark.asyncio
async def test_serialized_tool_list_contains_only_supported_api_key_tools():
    tools = await papra_mcp.mcp.list_tools()
    serialized = {
        tool.name: tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        for tool in tools
    }

    assert set(serialized) == EXPECTED_TOOLS
    assert set(serialized).isdisjoint(SESSION_ONLY_TOOLS)

    for name, tool in serialized.items():
        annotations = tool["annotations"]
        assert annotations["openWorldHint"] is True
        if name in READ_TOOLS:
            expected = (True, False, True)
        elif name in CREATE_TOOLS or name == "papra_reprocess_document":
            expected = (False, False, False)
        elif name == "papra_update_custom_property":
            expected = (False, True, False)
        elif name == "papra_add_tag_to_document":
            expected = (False, False, True)
        else:
            expected = (False, True, True)
        assert (
            annotations["readOnlyHint"],
            annotations["destructiveHint"],
            annotations["idempotentHint"],
        ) == expected, name


def test_property_options_require_name_and_valid_optional_id():
    assert papra_mcp.PropertyOptionInput(name="New").name == "New"
    kept = papra_mcp.PropertyOptionInput(id=OPTION_ID, name="Open")
    assert kept.model_dump(exclude_none=True) == {"id": OPTION_ID, "name": "Open"}

    for value in ({}, {"id": OPTION_ID}, {"name": ""}, {"id": "opt-1", "name": "Open"}):
        with pytest.raises(ValidationError):
            papra_mcp.PropertyOptionInput(**value)


@pytest.mark.parametrize("model", [papra_mcp.CreatePropertyDefInput, papra_mcp.UpdatePropertyDefInput])
def test_definition_option_lists_cannot_be_empty(model):
    kwargs = {"organization_id": ORG_ID, "name": "Status", "options": []}
    if model is papra_mcp.CreatePropertyDefInput:
        kwargs["type"] = "select"
    else:
        kwargs["property_definition_id"] = PROPERTY_ID

    with pytest.raises(ValidationError):
        model(**kwargs)


def test_option_replacement_requires_the_current_or_new_definition_name():
    base = {"organization_id": ORG_ID, "property_definition_id": PROPERTY_ID}
    options = [{"id": OPTION_ID, "name": "Open"}]

    with pytest.raises(ValidationError, match="name is required when replacing options"):
        papra_mcp.UpdatePropertyDefInput(**base, options=options)

    params = papra_mcp.UpdatePropertyDefInput(**base, name="Status", options=options)
    assert params.name == "Status"


@pytest.mark.parametrize("property_type", ["multi_select", "user_relation", "document_relation"])
def test_multi_valued_properties_require_arrays(property_type):
    values = {
        "multi_select": [OPTION_ID, OTHER_OPTION_ID],
        "user_relation": [USER_ID, OTHER_USER_ID],
        "document_relation": [DOC_ID, OTHER_DOC_ID],
    }
    base = {
        "organization_id": ORG_ID,
        "document_id": DOC_ID,
        "property_definition_id": PROPERTY_ID,
        "property_type": property_type,
    }

    for value in ([], values[property_type][:1], values[property_type]):
        params = papra_mcp.SetDocPropertyInput(**base, value=value)
        assert params.value == value

    with pytest.raises(ValidationError, match=property_type):
        papra_mcp.SetDocPropertyInput(**base, value=values[property_type][0])


@pytest.mark.parametrize(
    ("property_type", "value"),
    [
        ("text", "hello"),
        ("number", 42),
        ("number", 19.99),
        ("date", "2026-01-31"),
        ("boolean", True),
        ("select", OPTION_ID),
        ("multi_select", []),
    ],
)
def test_scalar_and_select_values_match_the_declared_property_type(property_type, value):
    params = papra_mcp.SetDocPropertyInput(
        organization_id=ORG_ID,
        document_id=DOC_ID,
        property_definition_id=PROPERTY_ID,
        property_type=property_type,
        value=value,
    )
    assert params.value == value


@pytest.mark.parametrize(
    ("property_type", "value"),
    [
        ("text", ["not", "scalar"]),
        ("number", True),
        ("boolean", 1),
        ("select", [OPTION_ID]),
        ("select", "not-an-option"),
        ("document_relation", ["not-a-document"]),
        ("multi_select", ["not-an-option"]),
    ],
)
def test_values_reject_shapes_invalid_for_the_declared_type(property_type, value):
    with pytest.raises(ValidationError, match=property_type):
        papra_mcp.SetDocPropertyInput(
            organization_id=ORG_ID,
            document_id=DOC_ID,
            property_definition_id=PROPERTY_ID,
            property_type=property_type,
            value=value,
        )


@pytest.mark.asyncio
async def test_serialized_property_schema_requires_names_and_type():
    tools = {tool.name: tool for tool in await papra_mcp.mcp.list_tools()}
    update_schema = tools["papra_update_custom_property"].inputSchema
    option_schema = update_schema["$defs"]["PropertyOptionInput"]
    assert option_schema["required"] == ["name"]
    assert option_schema["properties"]["id"]["pattern"] == r"^cpso_[a-z0-9]{24}$"

    set_schema = tools["papra_set_document_custom_property"].inputSchema
    required = set_schema["$defs"]["SetDocPropertyInput"]["required"]
    assert "property_type" in required
    assert "replaces" in tools["papra_set_document_custom_property"].description
    assert "[]" in tools["papra_set_document_custom_property"].description
