"""Document metadata and navigable search regressions for Papra 0.3.0."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

import papra_mcp

ORG_ID = "org_111111111111111111111111"
DOC_ID = "doc_222222222222222222222222"
DOCUMENT = {"organization_id": ORG_ID, "document_id": DOC_ID}
QUERY = '  date:>=2026-01-01  date:<2027-01-01 has:date has:tags name:"quarterly report"  '
DOCUMENT_TOOLS = [
    ("papra_list_documents", papra_mcp.ListDocsInput),
    ("papra_search_documents", papra_mcp.SearchDocsInput),
]


@asynccontextmanager
async def _mock_adapter(handler) -> AsyncIterator[httpx.AsyncClient]:
    original = papra_mcp._client
    async with httpx.AsyncClient(
        base_url="https://papra.example", transport=httpx.MockTransport(handler)
    ) as client:
        papra_mcp._client = client
        try:
            yield client
        finally:
            papra_mcp._client = original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"document_date": "2026-11-10"}, {"documentDate": "2026-11-10"}),
        (
            {"document_date": "2026-11-10T13:45:00+02:00"},
            {"documentDate": "2026-11-10T13:45:00+02:00"},
        ),
        (
            {"document_date": "2026-11-10T12:00:00Z"},
            {"documentDate": "2026-11-10T12:00:00Z"},
        ),
        (
            {"document_date": "2026-11-10T00:00:00+02:00"},
            {"documentDate": "2026-11-10T00:00:00+02:00"},
        ),
        ({"document_date": None}, {"documentDate": None}),
        ({"notes": "Keep this note"}, {"notes": "Keep this note"}),
        ({"notes": ""}, {"notes": ""}),
        ({"notes": "a" * 2048}, {"notes": "a" * 2048}),
        ({"notes": "😀" * 1024}, {"notes": "😀" * 1024}),
        (
            {"name": "renamed.txt", "content": "", "document_date": None, "notes": ""},
            {"name": "renamed.txt", "content": "", "documentDate": None, "notes": ""},
        ),
        ({"name": None, "content": None, "notes": "kept"}, {"notes": "kept"}),
    ],
)
async def test_metadata_serializes_only_requested_changes(changes, expected):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"document": {"id": DOC_ID, **expected}})

    async with _mock_adapter(handler):
        result = await papra_mcp.papra_update_document(
            papra_mcp.UpdateDocInput(**DOCUMENT, **changes)
        )

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "PATCH"
    assert request.url.path == f"/api/organizations/{ORG_ID}/documents/{DOC_ID}"
    assert json.loads(request.content) == expected
    assert json.loads(result)["document"] == {"id": DOC_ID, **expected}


@pytest.mark.parametrize(
    "changes",
    [
        {"notes": None},
        {"notes": "a" * 2049},
        {"notes": "😀" * 1025},
        {"notes": "a" * 2047 + "😀"},
        {"document_date": "not-a-date"},
        {"document_date": "2026-02-30"},
        {"document_date": "2026-11-10T12:00:00"},
        {"document_date": "2026-11-10T00:00:00"},
        {"document_date": "2026-11-10T12+01:00"},
        {"document_date": "2026-11-10T12:00:00+01:00:30"},
        {"document_date": "2026-11-10T12:00:00,1+01:00"},
        {"document_date": "2026-11-10T12:00:00+00:60"},
        {"document_date": 1794312000},
    ],
)
def test_metadata_validation_rejects_invalid_values(changes):
    with pytest.raises(ValidationError):
        papra_mcp.UpdateDocInput(**DOCUMENT, **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{}, {"name": None, "content": None}])
async def test_empty_metadata_update_errors_before_http(mock_client, changes):
    with pytest.raises(ToolError, match="No fields to update"):
        await papra_mcp.papra_update_document(
            papra_mcp.UpdateDocInput(**DOCUMENT, **changes)
        )
    mock_client.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_update_rejects_null_notes_before_http(mock_client):
    with pytest.raises(ToolError):
        await papra_mcp.mcp.call_tool(
            "papra_update_document", {"params": {**DOCUMENT, "notes": None}}
        )
    mock_client.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_registered_update_retains_explicit_date_clear():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"document": {"id": DOC_ID, "documentDate": None}})

    async with _mock_adapter(handler):
        await papra_mcp.mcp.call_tool(
            "papra_update_document", {"params": {**DOCUMENT, "document_date": None}}
        )
    assert len(requests) == 1
    assert json.loads(requests[0].content) == {"documentDate": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool_name", "model"), DOCUMENT_TOOLS)
async def test_combined_search_preserves_spaces_quotes_and_sort(tool_name, model):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"documents": [], "documentsCount": 0})

    params = model(
        organization_id=ORG_ID,
        search_query=QUERY,
        page_index=3,
        page_size=25,
        sort_field="documentDate",
        sort_order="asc",
    )
    async with _mock_adapter(handler):
        await getattr(papra_mcp, tool_name)(params)

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == f"/api/organizations/{ORG_ID}/documents"
    assert dict(request.url.params) == {
        "searchQuery": QUERY,
        "pageIndex": "3",
        "pageSize": "25",
        "sortField": "documentDate",
        "sortOrder": "asc",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool_name", "model"), DOCUMENT_TOOLS)
async def test_next_call_navigates_every_result_beyond_one_hundred(tool_name, model):
    documents = [{"id": f"doc_{index:024d}"} for index in range(205)]
    pages = []

    def handler(request):
        page_index = int(request.url.params["pageIndex"])
        page_size = int(request.url.params["pageSize"])
        pages.append(page_index)
        assert request.url.params["searchQuery"] == QUERY
        assert request.url.params["sortField"] == "name"
        assert request.url.params["sortOrder"] == "asc"
        start = page_index * page_size
        return httpx.Response(
            200,
            json={"documents": documents[start:start + page_size], "documentsCount": 205},
        )

    original = {
        "organization_id": ORG_ID,
        "search_query": QUERY,
        "page_size": 100,
        "sort_field": "name",
        "sort_order": "asc",
    }
    call = {"name": tool_name, "arguments": {"params": original}}
    seen = []
    async with _mock_adapter(handler):
        for page_index in range(3):
            assert call["name"] == tool_name
            arguments = call["arguments"]["params"]
            result = json.loads(await getattr(papra_mcp, tool_name)(model(**arguments)))
            assert result["documentsCount"] == 205
            seen.extend(document["id"] for document in result["documents"])
            pagination = result["pagination"]
            assert pagination["page_index"] == page_index
            assert pagination["page_size"] == 100
            assert pagination["has_more"] is (page_index < 2)
            call = pagination["next_call"]
            if page_index < 2:
                assert call == {
                    "name": tool_name,
                    "arguments": {"params": {**original, "page_index": page_index + 1}},
                }
            else:
                assert call is None

    assert pages == [0, 1, 2]
    assert seen == [document["id"] for document in documents]
    assert len(set(seen)) == 205


@pytest.mark.asyncio
@pytest.mark.parametrize(("page_index", "total", "returned"), [(0, 0, 0), (1, 100, 0), (0, 100, 100)])
async def test_navigation_stops_for_empty_out_of_range_and_full_final_page(page_index, total, returned):
    def handler(request):
        return httpx.Response(
            200,
            json={"documents": [{"id": str(i)} for i in range(returned)], "documentsCount": total},
        )

    async with _mock_adapter(handler):
        result = json.loads(await papra_mcp.papra_list_documents(
            papra_mcp.ListDocsInput(organization_id=ORG_ID, page_index=page_index)
        ))
    assert result["documentsCount"] == total
    assert result["pagination"] == {
        "page_index": page_index,
        "page_size": 100,
        "has_more": False,
        "next_call": None,
    }


@pytest.mark.asyncio
async def test_registered_metadata_and_search_contract_is_visible_to_clients():
    tools = {tool.name: tool for tool in await papra_mcp.mcp.list_tools()}
    update = tools["papra_update_document"]
    fields = update.inputSchema["$defs"]["UpdateDocInput"]["properties"]
    date_variants = fields["document_date"]["anyOf"]
    assert {"type": "string"} in date_variants
    assert {"type": "null"} in date_variants
    assert "YYYY-MM-DD" in fields["document_date"]["description"]
    assert "timezone" in fields["document_date"]["description"]
    notes_variants = fields["notes"].get("anyOf", [fields["notes"]])
    assert any(variant.get("maxLength") == 2048 for variant in notes_variants)
    assert all(variant.get("type") != "null" for variant in notes_variants)
    update_description = update.description.lower()
    for word in ("null", "omit", "notes", "2048"):
        assert word in update_description
    assert update.annotations.destructiveHint is True
    assert update.annotations.idempotentHint is True
    assert update.annotations.readOnlyHint is False

    for tool_name, _model in DOCUMENT_TOOLS:
        tool = tools[tool_name]
        description = tool.description
        for text in ("date:", "has:date", "has:tags", "next_call"):
            assert text in description
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
