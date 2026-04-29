#!/usr/bin/env python3
"""Papra MCP Server — MCP server for the Papra document management API."""

import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import httpx
import pymupdf
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

logger = logging.getLogger("papra_mcp")

__all__ = ["main", "mcp"]

# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(_server):
    """Manage the HTTP client lifecycle and validate configuration."""
    global _client
    base_url = os.environ.get("PAPRA_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("PAPRA_API_KEY", "")

    if not base_url:
        raise RuntimeError(
            "PAPRA_BASE_URL environment variable is required. "
            "Set it to your Papra instance URL (e.g. https://papra.example.com)"
        )
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(
            f"PAPRA_BASE_URL is not a valid URL: {base_url!r}. "
            "It must include a scheme (http:// or https://) and a host."
        )
    if not api_key:
        raise RuntimeError(
            "PAPRA_API_KEY environment variable is required. "
            "Create an API key in your Papra account settings."
        )

    _client = httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    )
    try:
        yield {}
    finally:
        await _client.aclose()
        _client = None


async def papra_request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    """Make an authenticated request to the Papra API."""
    if _client is None:
        raise RuntimeError("HTTP client not initialized \u2014 server lifespan not started.")
    # Strip None values from params
    if params:
        params = {k: v for k, v in params.items() if v is not None}

    response = await _client.request(
        method,
        path,
        json=body,
        params=params or None,
    )
    response.raise_for_status()

    if response.status_code == 204:
        return {}
    return response.json()


async def papra_file_request(path: str) -> httpx.Response:
    """Make an authenticated GET request and return the raw response (for file downloads)."""
    if _client is None:
        raise RuntimeError("HTTP client not initialized — server lifespan not started.")

    response = await _client.request("GET", path)
    response.raise_for_status()
    return response


_TEXT_CONTENT_TYPES = frozenset({
    "text/plain",
    "text/html",
    "text/csv",
    "text/xml",
    "text/markdown",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
})


def _is_text_content(content_type: str) -> bool:
    """Check whether the content type represents text that can be returned directly."""
    media_type = content_type.split(";")[0].strip().lower()
    return media_type in _TEXT_CONTENT_TYPES or media_type.startswith("text/")


def _looks_like_pdf(data: bytes) -> bool:
    """Return True if *data* starts with the PDF magic bytes (``%PDF``)."""
    return data[:4] == b"%PDF"


def _extract_pdf_text(data: bytes) -> str | None:
    """Extract text content from PDF bytes using pymupdf.

    Returns the extracted text or ``None`` if no text could be extracted
    (e.g. scanned images without OCR).
    """
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            pages = [text for page in doc if (text := page.get_text().strip())]
            return "\n\n".join(pages) if pages else None
    except Exception:
        logger.debug("PDF text extraction failed", exc_info=True)
        return None


def format_error(exc: Exception) -> str:
    """Format an exception into an actionable error message."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.json().get("message", exc.response.text)
        except Exception:
            detail = exc.response.text
        return (
            f"Papra API error ({exc.response.status_code}): {detail}. "
            "Check the API key permissions and resource IDs."
        )
    return f"Error: {exc}"


def _pretty_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("papra_mcp", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


# -- Base models --


class OrgBase(BaseModel):
    organization_id: str = Field(..., description="The organization ID", min_length=1)


class PaginatedOrgBase(OrgBase):
    page_index: int = Field(default=0, description="Page index (0-based)", ge=0)
    page_size: int = Field(default=100, description="Items per page (1-100)", ge=1, le=100)


class DocBase(OrgBase):
    document_id: str = Field(..., description="The document ID", min_length=1)


# -- Organization models --

OrgId = OrgBase


class CreateOrgInput(BaseModel):
    name: str = Field(..., description="Organization name (3-50 characters)", min_length=3, max_length=50)


class OrgName(OrgBase):
    name: str = Field(..., description="Organization name (3-50 characters)", min_length=3, max_length=50)


# -- Document models --

DocId = DocBase


class ListDocsInput(PaginatedOrgBase):
    search_query: str | None = Field(
        default=None,
        description="Optional search query. Supports filters: name:, content:, tag:, created: and operators AND, OR, NOT",
    )


PaginatedOrgInput = PaginatedOrgBase


class SearchDocsInput(PaginatedOrgBase):
    search_query: str = Field(..., description="Search query string", min_length=1)


class UpdateDocInput(DocBase):
    name: str | None = Field(default=None, description="New document name")
    content: str | None = Field(default=None, description="New document content (for search)")


class CreateDocInput(OrgBase):
    file_content: str = Field(..., description="Base64-encoded file content or text to upload")
    ocr_languages: str | None = Field(default=None, description="OCR languages to use (e.g., 'eng', 'fra', 'deu')")


class DocActivityInput(DocBase):
    page_index: int = Field(default=0, ge=0)
    page_size: int = Field(default=100, ge=1, le=100)


# -- Tag models --


class CreateTagInput(OrgBase):
    name: str = Field(..., description="Tag name", min_length=1)
    color: str = Field(..., description="Hex color (e.g. #FF0000)", pattern=r"^#[0-9a-fA-F]{6}$")
    description: str | None = Field(default=None, description="Optional tag description")


class UpdateTagInput(OrgBase):
    tag_id: str = Field(..., description="The tag ID", min_length=1)
    name: str | None = Field(default=None, description="New tag name")
    color: str | None = Field(default=None, description="New hex color", pattern=r"^#[0-9a-fA-F]{6}$")
    description: str | None = Field(default=None, description="New description")


class TagIdInput(OrgBase):
    tag_id: str = Field(..., description="The tag ID", min_length=1)


class DocTagInput(DocBase):
    tag_id: str = Field(..., description="The tag ID", min_length=1)


class ApplyTaggingRuleInput(OrgBase):
    tagging_rule_id: str = Field(..., description="The tagging rule ID", min_length=1)


# ---------------------------------------------------------------------------
# API Key
# ---------------------------------------------------------------------------


@mcp.tool(
    name="papra_check_api_key",
    annotations={
        "title": "Check API Key",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_check_api_key() -> str:
    """Check the currently used API key. Returns the key's ID, name, and permissions."""
    try:
        data = await papra_request("GET", "/api/api-keys/current")
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


@mcp.tool(
    name="papra_list_organizations",
    annotations={
        "title": "List Organizations",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_list_organizations() -> str:
    """List all organizations accessible to the authenticated user."""
    try:
        data = await papra_request("GET", "/api/organizations")
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_get_organization",
    annotations={
        "title": "Get Organization",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_get_organization(params: OrgId) -> str:
    """Get details of a specific organization by its ID."""
    try:
        data = await papra_request("GET", f"/api/organizations/{params.organization_id}")
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_create_organization",
    annotations={
        "title": "Create Organization",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def papra_create_organization(params: CreateOrgInput) -> str:
    """Create a new organization. The name must be 3-50 characters."""
    try:
        data = await papra_request("POST", "/api/organizations", body={"name": params.name})
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_update_organization",
    annotations={
        "title": "Update Organization",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_update_organization(params: OrgName) -> str:
    """Update an organization's name."""
    try:
        data = await papra_request(
            "PUT", f"/api/organizations/{params.organization_id}", body={"name": params.name}
        )
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_delete_organization",
    annotations={
        "title": "Delete Organization",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def papra_delete_organization(params: OrgId) -> str:
    """Delete an organization by its ID. This is a destructive operation."""
    try:
        await papra_request("DELETE", f"/api/organizations/{params.organization_id}")
        return f"Organization {params.organization_id} deleted successfully."
    except Exception as exc:
        return format_error(exc)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@mcp.tool(
    name="papra_list_documents",
    annotations={
        "title": "List Documents",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_list_documents(params: ListDocsInput) -> str:
    """List documents in an organization with optional pagination and search.

    The searchQuery supports advanced filters like name:, content:, tag:, created:
    and logical operators AND, OR, NOT.
    """
    try:
        data = await papra_request(
            "GET",
            f"/api/organizations/{params.organization_id}/documents",
            params={
                "pageIndex": params.page_index,
                "pageSize": params.page_size,
                "searchQuery": params.search_query,
            },
        )
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_list_deleted_documents",
    annotations={
        "title": "List Deleted Documents (Trash)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_list_deleted_documents(params: PaginatedOrgInput) -> str:
    """List deleted documents (trash) in an organization."""
    try:
        data = await papra_request(
            "GET",
            f"/api/organizations/{params.organization_id}/documents/deleted",
            params={"pageIndex": params.page_index, "pageSize": params.page_size},
        )
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_create_document",
    annotations={
        "title": "Create Document",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def papra_create_document(params: CreateDocInput) -> str:
    """Create a new document in an organization by uploading a file.

    The file_content parameter must be base64-encoded file content.
    The ocr_languages parameter is optional and specifies the languages
    to use for OCR (e.g. 'eng', 'fra', 'deu').
    """
    try:
        file_bytes = base64.b64decode(params.file_content)
        files = {"file": ("upload", file_bytes)}
        data = {"ocrLanguages": params.ocr_languages} if params.ocr_languages else None

        response = await _client.request(
            "POST",
            f"/api/organizations/{params.organization_id}/documents",
            files=files,
            data=data,
        )
        response.raise_for_status()

        if response.status_code == 204:
            return "Document created successfully."

        return _pretty_json(response.json())
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_get_document",
    annotations={
        "title": "Get Document",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_get_document(params: DocId) -> str:
    """Get a document's metadata by its ID."""
    try:
        data = await papra_request(
            "GET",
            f"/api/organizations/{params.organization_id}/documents/{params.document_id}",
        )
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_get_document_content",
    annotations={
        "title": "Get Document Content",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_get_document_content(params: DocId) -> str:
    """Get the file content of a document by its ID.

    For text-based documents (plain text, HTML, CSV, JSON, XML, Markdown, etc.)
    the content is returned directly as text. For PDF documents the text is
    extracted and returned as plain text so that LLMs can process it directly.
    For other binary documents (images, archives, etc.) the content is returned
    as a base64-encoded string together with the content type so the caller can
    decode it.
    """
    try:
        response = await papra_file_request(
            f"/api/organizations/{params.organization_id}/documents/{params.document_id}/file",
        )
        content_type = response.headers.get("content-type", "application/octet-stream")

        if _is_text_content(content_type):
            try:
                return response.text
            except UnicodeDecodeError:
                pass  # Fall through to base64 fallback

        media_type = content_type.split(";")[0].strip().lower()
        if media_type == "application/pdf" or _looks_like_pdf(response.content):
            text = _extract_pdf_text(response.content)
            if text is not None:
                return text

        encoded = base64.b64encode(response.content).decode("ascii")
        return _pretty_json({
            "content_type": content_type,
            "encoding": "base64",
            "data": encoded,
        })
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_search_documents",
    annotations={
        "title": "Search Documents",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_search_documents(params: SearchDocsInput) -> str:
    """Search documents by name or content. Supports advanced search syntax with
    filters (name:, content:, tag:, created:), logical operators (AND, OR, NOT),
    and grouping with parentheses.
    """
    try:
        data = await papra_request(
            "GET",
            f"/api/organizations/{params.organization_id}/documents/search",
            params={
                "searchQuery": params.search_query,
                "pageIndex": params.page_index,
                "pageSize": params.page_size,
            },
        )
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_get_document_statistics",
    annotations={
        "title": "Get Document Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_get_document_statistics(params: OrgId) -> str:
    """Get statistics (document count and total size) for an organization."""
    try:
        data = await papra_request(
            "GET",
            f"/api/organizations/{params.organization_id}/documents/statistics",
        )
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_update_document",
    annotations={
        "title": "Update Document",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_update_document(params: UpdateDocInput) -> str:
    """Update a document's name or content (for search indexing). Both fields are optional."""
    try:
        body: dict[str, Any] = {}
        if params.name is not None:
            body["name"] = params.name
        if params.content is not None:
            body["content"] = params.content

        if not body:
            return "No fields to update."

        data = await papra_request(
            "PATCH",
            f"/api/organizations/{params.organization_id}/documents/{params.document_id}",
            body=body,
        )
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_delete_document",
    annotations={
        "title": "Delete Document",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def papra_delete_document(params: DocId) -> str:
    """Soft-delete a document (moves to trash). Permanently deleted after retention period."""
    try:
        await papra_request(
            "DELETE",
            f"/api/organizations/{params.organization_id}/documents/{params.document_id}",
        )
        return f"Document {params.document_id} deleted successfully."
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_get_document_activity",
    annotations={
        "title": "Get Document Activity",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_get_document_activity(params: DocActivityInput) -> str:
    """Get the activity log of a document."""
    try:
        data = await papra_request(
            "GET",
            f"/api/organizations/{params.organization_id}/documents/{params.document_id}/activity",
            params={"pageIndex": params.page_index, "pageSize": params.page_size},
        )
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


@mcp.tool(
    name="papra_list_tags",
    annotations={
        "title": "List Tags",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_list_tags(params: OrgId) -> str:
    """List all tags in an organization."""
    try:
        data = await papra_request("GET", f"/api/organizations/{params.organization_id}/tags")
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_create_tag",
    annotations={
        "title": "Create Tag",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def papra_create_tag(params: CreateTagInput) -> str:
    """Create a new tag in an organization with a name, color, and optional description."""
    try:
        body: dict[str, Any] = {"name": params.name, "color": params.color}
        if params.description is not None:
            body["description"] = params.description

        data = await papra_request(
            "POST", f"/api/organizations/{params.organization_id}/tags", body=body
        )
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_update_tag",
    annotations={
        "title": "Update Tag",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_update_tag(params: UpdateTagInput) -> str:
    """Update a tag's name, color, or description. All fields are optional."""
    try:
        body: dict[str, Any] = {}
        if params.name is not None:
            body["name"] = params.name
        if params.color is not None:
            body["color"] = params.color
        if params.description is not None:
            body["description"] = params.description

        if not body:
            return "No fields to update."

        data = await papra_request(
            "PUT",
            f"/api/organizations/{params.organization_id}/tags/{params.tag_id}",
            body=body,
        )
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_delete_tag",
    annotations={
        "title": "Delete Tag",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def papra_delete_tag(params: TagIdInput) -> str:
    """Delete a tag by its ID."""
    try:
        await papra_request(
            "DELETE",
            f"/api/organizations/{params.organization_id}/tags/{params.tag_id}",
        )
        return f"Tag {params.tag_id} deleted successfully."
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_add_tag_to_document",
    annotations={
        "title": "Add Tag to Document",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_add_tag_to_document(params: DocTagInput) -> str:
    """Associate a tag with a document."""
    try:
        await papra_request(
            "POST",
            f"/api/organizations/{params.organization_id}/documents/{params.document_id}/tags",
            body={"tagId": params.tag_id},
        )
        return f"Tag {params.tag_id} added to document {params.document_id} successfully."
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_remove_tag_from_document",
    annotations={
        "title": "Remove Tag from Document",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def papra_remove_tag_from_document(params: DocTagInput) -> str:
    """Remove a tag association from a document."""
    try:
        await papra_request(
            "DELETE",
            f"/api/organizations/{params.organization_id}/documents/{params.document_id}/tags/{params.tag_id}",
        )
        return f"Tag {params.tag_id} removed from document {params.document_id} successfully."
    except Exception as exc:
        return format_error(exc)


@mcp.tool(
    name="papra_apply_tagging_rule",
    annotations={
        "title": "Apply Tagging Rule",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def papra_apply_tagging_rule(params: ApplyTaggingRuleInput) -> str:
    """Enqueue a background task to apply a tagging rule to all existing documents.
    Returns a task ID for tracking.
    """
    try:
        data = await papra_request(
            "POST",
            f"/api/organizations/{params.organization_id}/tagging-rules/{params.tagging_rule_id}/apply",
        )
        return _pretty_json(data)
    except Exception as exc:
        return format_error(exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the Papra MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
