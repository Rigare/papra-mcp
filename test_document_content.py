"""Tests for document content reading via the papra MCP server.

Verifies that papra_get_document_content correctly handles:
- Text content types (returned as plain text)
- Binary content types (returned as base64-encoded JSON)
- Error scenarios (HTTP errors, network failures)
- The _is_text_content helper
"""

import base64
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# Import the module under test
import papra_mcp


# ---------------------------------------------------------------------------
# _is_text_content helper tests
# ---------------------------------------------------------------------------


class TestIsTextContent:
    """Tests for the _is_text_content helper function."""

    @pytest.mark.parametrize(
        "content_type",
        [
            "text/plain",
            "text/html",
            "text/csv",
            "text/xml",
            "text/markdown",
            "application/json",
            "application/xml",
            "application/xhtml+xml",
        ],
    )
    def test_known_text_types(self, content_type: str):
        assert papra_mcp._is_text_content(content_type) is True

    @pytest.mark.parametrize(
        "content_type",
        [
            "text/plain; charset=utf-8",
            "text/html; charset=iso-8859-1",
            "application/json; charset=utf-8",
        ],
    )
    def test_text_types_with_charset(self, content_type: str):
        assert papra_mcp._is_text_content(content_type) is True

    def test_unknown_text_subtype_still_matches(self):
        """Any text/* type should be treated as text."""
        assert papra_mcp._is_text_content("text/rtf") is True
        assert papra_mcp._is_text_content("text/css") is True
        assert papra_mcp._is_text_content("text/javascript") is True

    @pytest.mark.parametrize(
        "content_type",
        [
            "application/pdf",
            "application/octet-stream",
            "image/png",
            "image/jpeg",
            "application/zip",
            "audio/mpeg",
            "video/mp4",
        ],
    )
    def test_binary_types(self, content_type: str):
        assert papra_mcp._is_text_content(content_type) is False

    def test_case_insensitive(self):
        assert papra_mcp._is_text_content("Text/Plain") is True
        assert papra_mcp._is_text_content("APPLICATION/JSON") is True

    def test_empty_string(self):
        assert papra_mcp._is_text_content("") is False


# ---------------------------------------------------------------------------
# papra_get_document_content integration tests
# ---------------------------------------------------------------------------


def _make_response(
    content: bytes,
    content_type: str,
    status_code: int = 200,
) -> httpx.Response:
    """Build a fake httpx.Response with the given body and content-type."""
    return httpx.Response(
        status_code=status_code,
        headers={"content-type": content_type},
        content=content,
    )


@pytest.fixture
def doc_params():
    """Standard parameters for papra_get_document_content."""
    return papra_mcp.DocId(organization_id="org-1", document_id="doc-1")


class TestGetDocumentContentText:
    """Text documents should be returned as plain text strings."""

    @pytest.mark.asyncio
    async def test_plain_text(self, doc_params):
        text = "Hello, this is a plain text document."
        response = _make_response(text.encode(), "text/plain; charset=utf-8")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert result == text

    @pytest.mark.asyncio
    async def test_html_content(self, doc_params):
        html = "<html><body><h1>Title</h1></body></html>"
        response = _make_response(html.encode(), "text/html")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert result == html

    @pytest.mark.asyncio
    async def test_json_content(self, doc_params):
        data = json.dumps({"key": "value", "count": 42})
        response = _make_response(data.encode(), "application/json")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert result == data

    @pytest.mark.asyncio
    async def test_csv_content(self, doc_params):
        csv_text = "name,age\nAlice,30\nBob,25"
        response = _make_response(csv_text.encode(), "text/csv")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert result == csv_text

    @pytest.mark.asyncio
    async def test_markdown_content(self, doc_params):
        md = "# Heading\n\nSome **bold** text."
        response = _make_response(md.encode(), "text/markdown")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert result == md

    @pytest.mark.asyncio
    async def test_xml_content(self, doc_params):
        xml = '<?xml version="1.0"?><root><item>test</item></root>'
        response = _make_response(xml.encode(), "application/xml")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert result == xml


class TestGetDocumentContentBinary:
    """Binary documents should be returned as base64-encoded JSON."""

    @pytest.mark.asyncio
    async def test_pdf_content(self, doc_params):
        pdf_bytes = b"%PDF-1.4 fake pdf content here"
        response = _make_response(pdf_bytes, "application/pdf")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        parsed = json.loads(result)
        assert parsed["content_type"] == "application/pdf"
        assert parsed["encoding"] == "base64"
        assert base64.b64decode(parsed["data"]) == pdf_bytes

    @pytest.mark.asyncio
    async def test_png_image(self, doc_params):
        # Minimal PNG header
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        response = _make_response(png_bytes, "image/png")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        parsed = json.loads(result)
        assert parsed["content_type"] == "image/png"
        assert parsed["encoding"] == "base64"
        assert base64.b64decode(parsed["data"]) == png_bytes

    @pytest.mark.asyncio
    async def test_jpeg_image(self, doc_params):
        jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 30
        response = _make_response(jpeg_bytes, "image/jpeg")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        parsed = json.loads(result)
        assert parsed["content_type"] == "image/jpeg"
        assert base64.b64decode(parsed["data"]) == jpeg_bytes

    @pytest.mark.asyncio
    async def test_octet_stream_fallback(self, doc_params):
        """Unknown content types default to binary handling."""
        raw = b"\x00\x01\x02\x03"
        response = _make_response(raw, "application/octet-stream")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        parsed = json.loads(result)
        assert parsed["encoding"] == "base64"
        assert base64.b64decode(parsed["data"]) == raw

    @pytest.mark.asyncio
    async def test_zip_archive(self, doc_params):
        zip_bytes = b"PK\x03\x04" + b"\x00" * 40
        response = _make_response(zip_bytes, "application/zip")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        parsed = json.loads(result)
        assert parsed["content_type"] == "application/zip"
        assert base64.b64decode(parsed["data"]) == zip_bytes


class TestGetDocumentContentMissingHeader:
    """When the content-type header is missing, fall back to binary."""

    @pytest.mark.asyncio
    async def test_no_content_type_header(self, doc_params):
        raw = b"some binary data"
        resp = httpx.Response(status_code=200, content=raw)
        # No content-type header at all

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=resp):
            result = await papra_mcp.papra_get_document_content(doc_params)

        parsed = json.loads(result)
        assert parsed["encoding"] == "base64"
        assert base64.b64decode(parsed["data"]) == raw


class TestGetDocumentContentErrors:
    """Error handling for document content retrieval."""

    @pytest.mark.asyncio
    async def test_http_404(self, doc_params):
        """A 404 should produce a human-readable error, not crash."""
        error_resp = httpx.Response(
            status_code=404,
            json={"message": "Document not found"},
            request=httpx.Request("GET", "http://test/api/organizations/org-1/documents/doc-1/file"),
        )
        exc = httpx.HTTPStatusError("Not Found", request=error_resp.request, response=error_resp)

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, side_effect=exc):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert "404" in result
        assert "Document not found" in result

    @pytest.mark.asyncio
    async def test_http_403(self, doc_params):
        error_resp = httpx.Response(
            status_code=403,
            json={"message": "Forbidden"},
            request=httpx.Request("GET", "http://test/api/organizations/org-1/documents/doc-1/file"),
        )
        exc = httpx.HTTPStatusError("Forbidden", request=error_resp.request, response=error_resp)

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, side_effect=exc):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert "403" in result
        assert "Forbidden" in result

    @pytest.mark.asyncio
    async def test_network_error(self, doc_params):
        """Connection failures should be caught and formatted."""
        with patch.object(
            papra_mcp, "papra_file_request",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert "Error" in result
        assert "Connection refused" in result

    @pytest.mark.asyncio
    async def test_timeout_error(self, doc_params):
        with patch.object(
            papra_mcp, "papra_file_request",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("Read timed out"),
        ):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert "Error" in result


class TestGetDocumentContentEdgeCases:
    """Edge cases for document content."""

    @pytest.mark.asyncio
    async def test_empty_text_document(self, doc_params):
        response = _make_response(b"", "text/plain")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert result == ""

    @pytest.mark.asyncio
    async def test_empty_binary_document(self, doc_params):
        response = _make_response(b"", "application/pdf")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        parsed = json.loads(result)
        assert parsed["data"] == ""  # base64 of empty bytes
        assert base64.b64decode(parsed["data"]) == b""

    @pytest.mark.asyncio
    async def test_unicode_text_document(self, doc_params):
        text = "Ünïcödé Dökümënt: 日本語テスト 🎉"
        response = _make_response(text.encode("utf-8"), "text/plain; charset=utf-8")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert result == text

    @pytest.mark.asyncio
    async def test_large_text_document(self, doc_params):
        """Ensure large text documents are returned correctly."""
        text = "Line of text content.\n" * 10_000
        response = _make_response(text.encode(), "text/plain")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        assert result == text

    @pytest.mark.asyncio
    async def test_large_binary_document(self, doc_params):
        """Ensure large binary documents encode correctly."""
        binary_data = bytes(range(256)) * 1000  # 256 KB
        response = _make_response(binary_data, "application/pdf")

        with patch.object(papra_mcp, "papra_file_request", new_callable=AsyncMock, return_value=response):
            result = await papra_mcp.papra_get_document_content(doc_params)

        parsed = json.loads(result)
        assert base64.b64decode(parsed["data"]) == binary_data


# ---------------------------------------------------------------------------
# papra_file_request tests
# ---------------------------------------------------------------------------


class TestPapraFileRequest:
    """Tests for the file request helper."""

    @pytest.mark.asyncio
    async def test_client_not_initialized(self):
        """Should raise RuntimeError when client is None."""
        original = papra_mcp._client
        papra_mcp._client = None
        try:
            with pytest.raises(RuntimeError, match="HTTP client not initialized"):
                await papra_mcp.papra_file_request("/some/path")
        finally:
            papra_mcp._client = original


# ---------------------------------------------------------------------------
# format_error tests
# ---------------------------------------------------------------------------


class TestFormatError:
    """Tests for the error formatting helper."""

    def test_http_status_error_with_json(self):
        resp = httpx.Response(
            status_code=422,
            json={"message": "Validation failed"},
            request=httpx.Request("GET", "http://test/"),
        )
        exc = httpx.HTTPStatusError("Unprocessable", request=resp.request, response=resp)

        result = papra_mcp.format_error(exc)
        assert "422" in result
        assert "Validation failed" in result

    def test_http_status_error_without_json(self):
        resp = httpx.Response(
            status_code=500,
            text="Internal Server Error",
            request=httpx.Request("GET", "http://test/"),
        )
        exc = httpx.HTTPStatusError("Server error", request=resp.request, response=resp)

        result = papra_mcp.format_error(exc)
        assert "500" in result

    def test_generic_exception(self):
        exc = ValueError("something went wrong")
        result = papra_mcp.format_error(exc)
        assert "something went wrong" in result
