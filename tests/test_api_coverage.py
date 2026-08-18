"""Tests for the endpoints and hardening added on top of the initial tool set.

Covers:
- Document creation (file name, MIME type, strict base64, plain-text variant)
- Search and sorting hitting the real /documents endpoint
- Trash handling (restore, permanent delete, empty)
- Custom property definitions and per-document values
- Content size limits and strict text decoding
- Configuration parsing
"""

import base64
import json
import os

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

import papra_mcp


def _request_call(mock_client):
    """Return (method, path, kwargs) of the single outgoing request."""
    call = mock_client.request.call_args
    return call.args[0], call.args[1], call.kwargs


# ---------------------------------------------------------------------------
# Document creation
# ---------------------------------------------------------------------------


class TestCreateDocument:
    """The upload must carry a real file name and reject non-base64 input."""

    @pytest.mark.asyncio
    async def test_sends_file_name_and_content(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"document": {"id": "doc-1"}})
        payload = b"%PDF-1.4 invoice"

        result = await papra_mcp.papra_create_document(
            papra_mcp.CreateDocInput(
                organization_id="org-1",
                file_name="invoice-2026-01.pdf",
                file_content=base64.b64encode(payload).decode(),
            )
        )

        method, path, kwargs = _request_call(mock_client)
        assert method == "POST"
        assert path == "/api/organizations/org-1/documents"
        # File name must reach Papra — it becomes the document name.
        assert kwargs["files"]["file"] == ("invoice-2026-01.pdf", payload)
        assert kwargs["data"] is None
        assert "doc-1" in result

    @pytest.mark.asyncio
    async def test_explicit_content_type_is_forwarded(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"document": {"id": "doc-1"}})

        await papra_mcp.papra_create_document(
            papra_mcp.CreateDocInput(
                organization_id="org-1",
                file_name="scan.tiff",
                file_content=base64.b64encode(b"II*\x00").decode(),
                content_type="image/tiff",
            )
        )

        _, _, kwargs = _request_call(mock_client)
        assert kwargs["files"]["file"] == ("scan.tiff", b"II*\x00", "image/tiff")

    @pytest.mark.asyncio
    async def test_ocr_languages_are_forwarded(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"document": {"id": "doc-1"}})

        await papra_mcp.papra_create_document(
            papra_mcp.CreateDocInput(
                organization_id="org-1",
                file_name="scan.pdf",
                file_content=base64.b64encode(b"%PDF").decode(),
                ocr_languages="deu",
            )
        )

        _, _, kwargs = _request_call(mock_client)
        assert kwargs["data"] == {"ocrLanguages": "deu"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_input",
        [
            "Rechnung 2026",  # decodes to garbage without validate=True
            "Hello world this is my note",
            "not base64!!!",
            "###",
        ],
    )
    async def test_rejects_non_base64(self, mock_client, api_response, bad_input):
        """Plain text must be refused, not silently uploaded as corrupt bytes."""
        with pytest.raises(ToolError, match="not valid base64"):
            await papra_mcp.papra_create_document(
                papra_mcp.CreateDocInput(
                    organization_id="org-1",
                    file_name="note.txt",
                    file_content=bad_input,
                )
            )

        mock_client.request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_content_response(self, mock_client, api_response):
        """A 204 upload response should still report success."""
        mock_client.request.return_value = api_response(status_code=204)
        mock_client.request.return_value.content = b""

        result = await papra_mcp.papra_create_document(
            papra_mcp.CreateDocInput(
                organization_id="org-1",
                file_name="a.txt",
                file_content=base64.b64encode(b"hi").decode(),
            )
        )
        assert "created successfully" in result


class TestCreateTextDocument:
    """Plain text uploads without a base64 round trip."""

    @pytest.mark.asyncio
    async def test_encodes_utf8(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"document": {"id": "doc-2"}})

        await papra_mcp.papra_create_text_document(
            papra_mcp.CreateTextDocInput(
                organization_id="org-1",
                file_name="notiz.md",
                text_content="# Überschrift\n\nInhalt 日本語",
            )
        )

        _, _, kwargs = _request_call(mock_client)
        name, content = kwargs["files"]["file"]
        assert name == "notiz.md"
        assert content.decode("utf-8") == "# Überschrift\n\nInhalt 日本語"


# ---------------------------------------------------------------------------
# Listing, search and sorting
# ---------------------------------------------------------------------------


class TestListAndSearch:
    """Search must hit /documents — /documents/search does not exist in Papra."""

    @pytest.mark.asyncio
    async def test_search_uses_documents_endpoint(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"documents": [], "documentsCount": 0})

        await papra_mcp.papra_search_documents(
            papra_mcp.SearchDocsInput(organization_id="org-1", search_query="tag:invoice")
        )

        method, path, kwargs = _request_call(mock_client)
        assert method == "GET"
        assert path == "/api/organizations/org-1/documents"
        assert kwargs["params"]["searchQuery"] == "tag:invoice"

    @pytest.mark.asyncio
    async def test_list_forwards_sort_parameters(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"documents": [], "documentsCount": 0})

        await papra_mcp.papra_list_documents(
            papra_mcp.ListDocsInput(
                organization_id="org-1", sort_field="documentDate", sort_order="asc"
            )
        )

        _, _, kwargs = _request_call(mock_client)
        assert kwargs["params"]["sortField"] == "documentDate"
        assert kwargs["params"]["sortOrder"] == "asc"

    @pytest.mark.asyncio
    async def test_list_defaults(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"documents": [], "documentsCount": 0})

        await papra_mcp.papra_list_documents(papra_mcp.ListDocsInput(organization_id="org-1"))

        _, _, kwargs = _request_call(mock_client)
        assert kwargs["params"] == {
            "pageIndex": 0,
            "pageSize": 100,
            "sortField": "createdAt",
            "sortOrder": "desc",
        }
        # searchQuery is None and must be stripped, not sent as "None"
        assert "searchQuery" not in kwargs["params"]

    def test_rejects_unknown_sort_field(self):
        with pytest.raises(ValueError):
            papra_mcp.ListDocsInput(organization_id="org-1", sort_field="size")


# ---------------------------------------------------------------------------
# Trash
# ---------------------------------------------------------------------------


class TestTrash:
    """Restore and permanent deletion round out the trash workflow."""

    @pytest.mark.asyncio
    async def test_restore_document(self, mock_client, api_response):
        mock_client.request.return_value = api_response(status_code=204)

        result = await papra_mcp.papra_restore_document(
            papra_mcp.DocBase(organization_id="org-1", document_id="doc-1")
        )

        method, path, _ = _request_call(mock_client)
        assert (method, path) == ("POST", "/api/organizations/org-1/documents/doc-1/restore")
        assert "restored" in result

    @pytest.mark.asyncio
    async def test_delete_permanently(self, mock_client, api_response):
        mock_client.request.return_value = api_response(status_code=204)

        result = await papra_mcp.papra_delete_document_permanently(
            papra_mcp.DocBase(organization_id="org-1", document_id="doc-1")
        )

        method, path, _ = _request_call(mock_client)
        assert (method, path) == ("DELETE", "/api/organizations/org-1/documents/trash/doc-1")
        assert "permanently deleted" in result

    @pytest.mark.asyncio
    async def test_empty_trash(self, mock_client, api_response):
        mock_client.request.return_value = api_response(status_code=204)

        result = await papra_mcp.papra_empty_trash(papra_mcp.OrgBase(organization_id="org-1"))

        method, path, _ = _request_call(mock_client)
        assert (method, path) == ("DELETE", "/api/organizations/org-1/documents/trash")
        assert "org-1" in result

    def test_destructive_tools_are_annotated(self):
        """Irreversible tools must advertise destructiveHint so clients can gate them."""
        for name in (
            "papra_delete_document_permanently",
            "papra_empty_trash",
            "papra_delete_organization",
            "papra_delete_tag",
            "papra_delete_custom_property",
        ):
            tool = papra_mcp.mcp._tool_manager.get_tool(name)
            assert tool.annotations.destructiveHint is True, name


# ---------------------------------------------------------------------------
# Custom properties
# ---------------------------------------------------------------------------


class TestCustomPropertyDefinitions:
    """The eight custom-property endpoints documented by Papra."""

    @pytest.mark.asyncio
    async def test_list(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"propertyDefinitions": []})

        await papra_mcp.papra_list_custom_properties(papra_mcp.OrgBase(organization_id="org-1"))

        method, path, _ = _request_call(mock_client)
        assert (method, path) == ("GET", "/api/organizations/org-1/custom-properties")

    @pytest.mark.asyncio
    async def test_get(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"propertyDefinition": {"id": "cp-1"}})

        await papra_mcp.papra_get_custom_property(
            papra_mcp.PropertyDefBase(organization_id="org-1", property_definition_id="cp-1")
        )

        method, path, _ = _request_call(mock_client)
        assert (method, path) == ("GET", "/api/organizations/org-1/custom-properties/cp-1")

    @pytest.mark.asyncio
    async def test_create_simple(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"propertyDefinition": {"id": "cp-1"}})

        await papra_mcp.papra_create_custom_property(
            papra_mcp.CreatePropertyDefInput(
                organization_id="org-1", name="Amount", type="number"
            )
        )

        method, path, kwargs = _request_call(mock_client)
        assert (method, path) == ("POST", "/api/organizations/org-1/custom-properties")
        assert kwargs["json"] == {"name": "Amount", "type": "number"}

    @pytest.mark.asyncio
    async def test_create_select_wraps_option_names(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"propertyDefinition": {"id": "cp-1"}})

        await papra_mcp.papra_create_custom_property(
            papra_mcp.CreatePropertyDefInput(
                organization_id="org-1",
                name="Status",
                type="select",
                description="Processing state",
                options=["open", "paid"],
            )
        )

        _, _, kwargs = _request_call(mock_client)
        assert kwargs["json"] == {
            "name": "Status",
            "type": "select",
            "description": "Processing state",
            "options": [{"name": "open"}, {"name": "paid"}],
        }

    def test_rejects_unknown_type(self):
        with pytest.raises(ValueError):
            papra_mcp.CreatePropertyDefInput(
                organization_id="org-1", name="X", type="currency"
            )

    @pytest.mark.asyncio
    async def test_update_mixes_existing_and_new_options(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"propertyDefinition": {"id": "cp-1"}})

        await papra_mcp.papra_update_custom_property(
            papra_mcp.UpdatePropertyDefInput(
                organization_id="org-1",
                property_definition_id="cp-1",
                name="Status",
                options=[
                    papra_mcp.PropertyOptionInput(id="opt-1"),
                    papra_mcp.PropertyOptionInput(name="overdue"),
                ],
            )
        )

        method, path, kwargs = _request_call(mock_client)
        assert (method, path) == ("PUT", "/api/organizations/org-1/custom-properties/cp-1")
        # Unset fields must be dropped, not sent as null
        assert kwargs["json"] == {
            "name": "Status",
            "options": [{"id": "opt-1"}, {"name": "overdue"}],
        }

    @pytest.mark.asyncio
    async def test_update_without_fields_errors(self, mock_client, api_response):
        with pytest.raises(ToolError, match="No fields to update"):
            await papra_mcp.papra_update_custom_property(
                papra_mcp.UpdatePropertyDefInput(
                    organization_id="org-1", property_definition_id="cp-1"
                )
            )

    @pytest.mark.asyncio
    async def test_delete(self, mock_client, api_response):
        mock_client.request.return_value = api_response(status_code=204)

        result = await papra_mcp.papra_delete_custom_property(
            papra_mcp.PropertyDefBase(organization_id="org-1", property_definition_id="cp-1")
        )

        method, path, _ = _request_call(mock_client)
        assert (method, path) == ("DELETE", "/api/organizations/org-1/custom-properties/cp-1")
        assert "cp-1" in result


class TestDocumentCustomProperties:
    """Per-document custom property values."""

    @pytest.mark.asyncio
    async def test_list(self, mock_client, api_response):
        mock_client.request.return_value = api_response({"customProperties": []})

        await papra_mcp.papra_list_document_custom_properties(
            papra_mcp.DocBase(organization_id="org-1", document_id="doc-1")
        )

        method, path, _ = _request_call(mock_client)
        assert (method, path) == (
            "GET",
            "/api/organizations/org-1/documents/doc-1/custom-properties",
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "value",
        [
            "2026-01-31",          # date
            "Acme GmbH",           # text
            42,                    # number
            19.99,                 # number
            True,                  # boolean
            ["opt-1", "opt-2"],    # multi_select
        ],
    )
    async def test_set_value_types(self, mock_client, api_response, value):
        mock_client.request.return_value = api_response(status_code=204)

        await papra_mcp.papra_set_document_custom_property(
            papra_mcp.SetDocPropertyInput(
                organization_id="org-1",
                document_id="doc-1",
                property_definition_id="cp-1",
                value=value,
            )
        )

        method, path, kwargs = _request_call(mock_client)
        assert (method, path) == (
            "PUT",
            "/api/organizations/org-1/documents/doc-1/custom-properties/cp-1",
        )
        assert kwargs["json"] == {"value": value}

    @pytest.mark.asyncio
    async def test_boolean_is_not_coerced_to_number(self, mock_client, api_response):
        """Pydantic's union must keep True a bool, not turn it into 1."""
        mock_client.request.return_value = api_response(status_code=204)

        await papra_mcp.papra_set_document_custom_property(
            papra_mcp.SetDocPropertyInput(
                organization_id="org-1",
                document_id="doc-1",
                property_definition_id="cp-1",
                value=True,
            )
        )

        _, _, kwargs = _request_call(mock_client)
        assert kwargs["json"]["value"] is True

    @pytest.mark.asyncio
    async def test_clear(self, mock_client, api_response):
        mock_client.request.return_value = api_response(status_code=204)

        result = await papra_mcp.papra_clear_document_custom_property(
            papra_mcp.DocPropertyBase(
                organization_id="org-1", document_id="doc-1", property_definition_id="cp-1"
            )
        )

        method, path, _ = _request_call(mock_client)
        assert (method, path) == (
            "DELETE",
            "/api/organizations/org-1/documents/doc-1/custom-properties/cp-1",
        )
        assert "cleared" in result


# ---------------------------------------------------------------------------
# Content handling: size limits and strict decoding
# ---------------------------------------------------------------------------


def _make_response(content: bytes, content_type: str) -> httpx.Response:
    return httpx.Response(status_code=200, headers={"content-type": content_type}, content=content)


class TestContentSizeLimits:
    """Oversized payloads must not be dumped into the model's context."""

    @pytest.mark.asyncio
    async def test_large_text_is_truncated(self, doc_params, max_content_bytes, monkeypatch):
        max_content_bytes(500)
        text = "A" * 5000
        monkeypatch.setattr(
            papra_mcp, "papra_file_request",
            _returning(_make_response(text.encode(), "text/plain")),
        )

        result = await papra_mcp.papra_get_document_content(doc_params)

        assert len(result) < 1500
        assert "truncated" in result
        assert "4500 of 5000 bytes omitted" in result

    @pytest.mark.asyncio
    async def test_large_binary_is_refused(self, doc_params, max_content_bytes, monkeypatch):
        max_content_bytes(500)
        monkeypatch.setattr(
            papra_mcp, "papra_file_request",
            _returning(_make_response(b"\x00" * 5000, "image/png")),
        )

        with pytest.raises(ToolError, match="exceeds"):
            await papra_mcp.papra_get_document_content(doc_params)

    @pytest.mark.asyncio
    async def test_binary_at_limit_is_returned(self, doc_params, max_content_bytes, monkeypatch):
        max_content_bytes(500)
        payload = b"\x00" * 500
        monkeypatch.setattr(
            papra_mcp, "papra_file_request",
            _returning(_make_response(payload, "image/png")),
        )

        result = await papra_mcp.papra_get_document_content(doc_params)
        assert base64.b64decode(json.loads(result)["data"]) == payload

    @pytest.mark.asyncio
    async def test_long_pdf_text_is_truncated(self, doc_params, max_content_bytes, monkeypatch):
        max_content_bytes(200)
        import pymupdf

        doc = pymupdf.open()
        for _ in range(5):
            page = doc.new_page()
            page.insert_text((72, 72), "Invoice line item with a reasonably long description")
        pdf_bytes = doc.tobytes()
        doc.close()

        monkeypatch.setattr(
            papra_mcp, "papra_file_request",
            _returning(_make_response(pdf_bytes, "application/pdf")),
        )

        result = await papra_mcp.papra_get_document_content(doc_params)
        assert "truncated" in result


class TestStrictTextDecoding:
    """A mislabelled binary file must fall back to base64, not return mojibake."""

    @pytest.mark.asyncio
    async def test_binary_labelled_as_text_falls_back(self, doc_params, monkeypatch):
        payload = b"\xff\xfe\x00\x80\x81binary"
        monkeypatch.setattr(
            papra_mcp, "papra_file_request",
            _returning(_make_response(payload, "text/plain; charset=utf-8")),
        )

        result = await papra_mcp.papra_get_document_content(doc_params)

        parsed = json.loads(result)
        assert parsed["encoding"] == "base64"
        assert base64.b64decode(parsed["data"]) == payload
        assert "�" not in result  # no replacement characters

    @pytest.mark.asyncio
    async def test_unknown_charset_decodes_as_utf8(self, doc_params, monkeypatch):
        """httpx normalises an unknown charset to utf-8, so valid text still comes back as text."""
        payload = b"plain ascii"
        monkeypatch.setattr(
            papra_mcp, "papra_file_request",
            _returning(_make_response(payload, "text/plain; charset=not-a-real-charset")),
        )

        result = await papra_mcp.papra_get_document_content(doc_params)
        assert result == "plain ascii"

    @pytest.mark.asyncio
    async def test_unknown_charset_with_invalid_bytes_falls_back(self, doc_params, monkeypatch):
        payload = b"\xff\xfe\x80binary"
        monkeypatch.setattr(
            papra_mcp, "papra_file_request",
            _returning(_make_response(payload, "text/plain; charset=not-a-real-charset")),
        )

        result = await papra_mcp.papra_get_document_content(doc_params)
        assert base64.b64decode(json.loads(result)["data"]) == payload

    def test_decode_text_helper(self):
        assert papra_mcp._decode_text(b"hello", "utf-8") == "hello"
        assert papra_mcp._decode_text(b"\xff\xfe\x80", "utf-8") is None
        assert papra_mcp._decode_text(b"hello", "bogus-charset") is None
        assert papra_mcp._decode_text(b"hello", None) == "hello"


def _returning(value):
    async def _call(*_args, **_kwargs):
        return value

    return _call


@pytest.fixture
def doc_params():
    return papra_mcp.DocBase(organization_id="org-1", document_id="doc-1")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfiguration:
    """Timeout and size limits are configurable, with validation."""

    @pytest.fixture(autouse=True)
    def _clean_env(self):
        saved = {k: os.environ.get(k) for k in ("PAPRA_TIMEOUT", "PAPRA_MAX_CONTENT_BYTES")}
        for key in saved:
            os.environ.pop(key, None)
        yield
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_defaults(self):
        assert papra_mcp._env_float("PAPRA_TIMEOUT", 60.0) == 60.0
        assert papra_mcp._env_int("PAPRA_MAX_CONTENT_BYTES", 1024) == 1024

    def test_reads_values(self):
        os.environ["PAPRA_TIMEOUT"] = "120.5"
        os.environ["PAPRA_MAX_CONTENT_BYTES"] = "2048"
        assert papra_mcp._env_float("PAPRA_TIMEOUT", 60.0) == 120.5
        assert papra_mcp._env_int("PAPRA_MAX_CONTENT_BYTES", 1024) == 2048

    @pytest.mark.parametrize("value", ["abc", "-1", "0"])
    def test_rejects_invalid_timeout(self, value):
        os.environ["PAPRA_TIMEOUT"] = value
        with pytest.raises(RuntimeError, match="PAPRA_TIMEOUT"):
            papra_mcp._env_float("PAPRA_TIMEOUT", 60.0)

    @pytest.mark.parametrize("value", ["abc", "-1", "0", "1.5"])
    def test_rejects_invalid_size(self, value):
        os.environ["PAPRA_MAX_CONTENT_BYTES"] = value
        with pytest.raises(RuntimeError, match="PAPRA_MAX_CONTENT_BYTES"):
            papra_mcp._env_int("PAPRA_MAX_CONTENT_BYTES", 1024)

    @pytest.mark.asyncio
    async def test_lifespan_applies_config(self):
        os.environ["PAPRA_BASE_URL"] = "https://papra.example.com"
        os.environ["PAPRA_API_KEY"] = "test-key"
        os.environ["PAPRA_TIMEOUT"] = "5"
        os.environ["PAPRA_MAX_CONTENT_BYTES"] = "1234"

        original = papra_mcp._max_content_bytes
        try:
            async with papra_mcp.lifespan(None):
                assert papra_mcp._client.timeout.read == 5
                assert papra_mcp._client.follow_redirects is True
                assert papra_mcp._max_content_bytes == 1234
        finally:
            papra_mcp._max_content_bytes = original


# ---------------------------------------------------------------------------
# Error semantics
# ---------------------------------------------------------------------------


class TestToolErrorSemantics:
    """Every tool reports failures as ToolError, never as a plausible-looking result."""

    @pytest.mark.asyncio
    async def test_api_error_raises(self, mock_client, api_response):
        request = httpx.Request("GET", "http://test/api/organizations/org-1/tags")
        response = httpx.Response(403, json={"message": "Missing permission"}, request=request)
        mock_client.request.side_effect = httpx.HTTPStatusError(
            "Forbidden", request=request, response=response
        )

        with pytest.raises(ToolError) as excinfo:
            await papra_mcp.papra_list_tags(papra_mcp.OrgBase(organization_id="org-1"))

        assert "403" in str(excinfo.value)
        assert "Missing permission" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_uninitialised_client_raises(self):
        original = papra_mcp._client
        papra_mcp._client = None
        try:
            with pytest.raises(ToolError, match="HTTP client not initialized"):
                await papra_mcp.papra_list_organizations()
        finally:
            papra_mcp._client = original

    @pytest.mark.asyncio
    async def test_upload_without_client_raises(self):
        original = papra_mcp._client
        papra_mcp._client = None
        try:
            with pytest.raises(ToolError, match="HTTP client not initialized"):
                await papra_mcp.papra_create_document(
                    papra_mcp.CreateDocInput(
                        organization_id="org-1",
                        file_name="a.txt",
                        file_content=base64.b64encode(b"hi").decode(),
                    )
                )
        finally:
            papra_mcp._client = original

    def test_tool_errors_preserves_signature(self):
        """The decorator must not hide the signature FastMCP introspects."""
        import inspect

        params = inspect.signature(papra_mcp.papra_get_document).parameters
        assert list(params) == ["params"]
        assert params["params"].annotation is papra_mcp.DocBase
        assert papra_mcp.papra_get_document.__doc__


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Every documented Papra endpoint must be reachable through a tool."""

    EXPECTED_TOOLS = {
        "papra_check_api_key",
        "papra_list_organizations",
        "papra_get_organization",
        "papra_create_organization",
        "papra_update_organization",
        "papra_delete_organization",
        "papra_list_documents",
        "papra_search_documents",
        "papra_list_deleted_documents",
        "papra_create_document",
        "papra_create_text_document",
        "papra_get_document",
        "papra_get_document_content",
        "papra_get_document_statistics",
        "papra_update_document",
        "papra_delete_document",
        "papra_restore_document",
        "papra_delete_document_permanently",
        "papra_empty_trash",
        "papra_get_document_activity",
        "papra_list_tags",
        "papra_create_tag",
        "papra_update_tag",
        "papra_delete_tag",
        "papra_add_tag_to_document",
        "papra_remove_tag_from_document",
        "papra_apply_tagging_rule",
        "papra_list_custom_properties",
        "papra_get_custom_property",
        "papra_create_custom_property",
        "papra_update_custom_property",
        "papra_delete_custom_property",
        "papra_list_document_custom_properties",
        "papra_set_document_custom_property",
        "papra_clear_document_custom_property",
    }

    @pytest.mark.asyncio
    async def test_all_tools_registered(self):
        tools = await papra_mcp.mcp.list_tools()
        assert {tool.name for tool in tools} == self.EXPECTED_TOOLS

    @pytest.mark.asyncio
    async def test_all_tools_have_descriptions(self):
        tools = await papra_mcp.mcp.list_tools()
        for tool in tools:
            assert tool.description, f"{tool.name} has no description"
            assert tool.annotations is not None, f"{tool.name} has no annotations"
            assert tool.annotations.title, f"{tool.name} has no title"
