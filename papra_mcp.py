#!/usr/bin/env python3
"""Papra MCP Server — MCP server for the Papra document management API."""

import base64
import binascii
import functools
import json
import logging
import math
import os
import re
import sys
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, ParamSpec
from urllib.parse import quote, urlparse

import httpx
import pymupdf
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, StringConstraints, model_validator

logger = logging.getLogger("papra_mcp")

__all__ = ["main", "mcp"]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_CONTENT_BYTES = 5 * 1024 * 1024

# Upper bound for document content returned through a tool result. Binary
# payloads above this are rejected outright (a truncated base64 blob is
# useless), text is truncated with an explicit marker.
_max_content_bytes = DEFAULT_MAX_CONTENT_BYTES


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than 0, got {raw!r}")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than 0, got {raw!r}")
    return value


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(_server):
    """Manage the HTTP client lifecycle and validate configuration."""
    global _client, _max_content_bytes
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

    _max_content_bytes = _env_int("PAPRA_MAX_CONTENT_BYTES", DEFAULT_MAX_CONTENT_BYTES)

    _client = httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_env_float("PAPRA_TIMEOUT", DEFAULT_TIMEOUT),
        follow_redirects=True,
    )
    try:
        yield {}
    finally:
        await _client.aclose()
        _client = None


def _require_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client not initialized — server lifespan not started.")
    return _client


async def papra_request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    """Make an authenticated request to the Papra API."""
    client = _require_client()
    # Strip None values from params
    if params:
        params = {k: v for k, v in params.items() if v is not None}

    response = await client.request(
        method,
        path,
        json=body,
        params=params or None,
    )
    response.raise_for_status()

    if response.status_code == 204 or not response.content:
        return {}
    return response.json()


async def papra_file_request(path: str) -> httpx.Response:
    """Make an authenticated GET request and return the raw response (for file downloads)."""
    client = _require_client()

    response = await client.request("GET", path)
    response.raise_for_status()
    return response


async def papra_upload_request(
    path: str,
    *,
    files: dict[str, Any],
    data: dict[str, Any] | None = None,
) -> httpx.Response:
    """Make an authenticated multipart POST request (for file uploads)."""
    client = _require_client()

    response = await client.request("POST", path, files=files, data=data)
    response.raise_for_status()
    return response


# ---------------------------------------------------------------------------
# Content handling
# ---------------------------------------------------------------------------

# text/* is covered by the prefix check in _is_text_content; only the
# non-text/* media types that are safe to return verbatim belong here.
_TEXT_CONTENT_TYPES = frozenset({
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


def _decode_text(data: bytes, encoding: str | None) -> str | None:
    """Strictly decode *data*, returning ``None`` if it is not valid text.

    ``httpx.Response.text`` decodes with ``errors="replace"`` and therefore
    never fails, which would turn a mislabelled binary file into mojibake
    instead of falling back to base64. This decodes strictly instead.
    """
    try:
        return data.decode(encoding or "utf-8", errors="strict")
    except (UnicodeDecodeError, LookupError):
        return None


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


def _truncate_text(text: str) -> str:
    """Truncate *text* to the configured content limit, with an explicit marker."""
    encoded = text.encode("utf-8")
    if len(encoded) <= _max_content_bytes:
        return text

    kept = encoded[:_max_content_bytes].decode("utf-8", errors="ignore")
    omitted = len(encoded) - len(kept.encode("utf-8"))
    return (
        f"{kept}\n\n[... truncated: {omitted} of {len(encoded)} bytes omitted. "
        "Raise PAPRA_MAX_CONTENT_BYTES to return more. ...]"
    )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


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


P = ParamSpec("P")


def tool_errors(func: Callable[P, Awaitable[str]]) -> Callable[P, Awaitable[str]]:
    """Turn exceptions into ``ToolError`` so MCP flags the result with ``isError``.

    Returning an error as a normal string leaves ``isError`` false, which makes
    a failure indistinguishable from a successful response for the caller.
    """

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> str:
        try:
            return await func(*args, **kwargs)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(format_error(exc)) from exc

    return wrapper


def _pretty_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _body_from(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop unset (``None``) fields, raising if nothing is left to send."""
    body = {k: v for k, v in fields.items() if v is not None}
    if not body:
        raise ToolError("No fields to update — provide at least one field to change.")
    return body


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("papra_mcp", lifespan=lifespan)

READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
WRITE = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
IDEMPOTENT_WRITE = {**WRITE, "idempotentHint": True}
DESTRUCTIVE = {**IDEMPOTENT_WRITE, "destructiveHint": True}

# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


def _papra_id_type(prefix: str) -> Any:
    return Annotated[str, StringConstraints(pattern=rf"^{prefix}_[a-z0-9]{{24}}$")]


OrganizationId = _papra_id_type("org")
DocumentId = _papra_id_type("doc")
TagId = _papra_id_type("tag")
PropertyDefinitionId = _papra_id_type("cpd")
PropertyOptionId = _papra_id_type("cpso")
UserId = _papra_id_type("usr")

_ID_PATTERNS = {
    prefix: re.compile(rf"^{prefix}_[a-z0-9]{{24}}$")
    for prefix in ("org", "doc", "tag", "cpd", "cpso", "usr")
}


def _path_segment(resource_id: str) -> str:
    return quote(resource_id, safe="")


# -- Base models --


class OrgBase(BaseModel):
    organization_id: OrganizationId = Field(..., description="The organization ID")


class PaginatedOrgBase(OrgBase):
    page_index: int = Field(default=0, description="Page index (0-based)", ge=0)
    page_size: int = Field(default=100, description="Items per page (1-100)", ge=1, le=100)


class DocBase(OrgBase):
    document_id: DocumentId = Field(..., description="The document ID")


# -- Organization models --


class CreateOrgInput(BaseModel):
    name: str = Field(..., description="Organization name (3-50 characters)", min_length=3, max_length=50)


class OrgName(OrgBase):
    name: str = Field(..., description="Organization name (3-50 characters)", min_length=3, max_length=50)


# -- Document models --

SortField = Literal["createdAt", "updatedAt", "name", "documentDate"]
SortOrder = Literal["asc", "desc"]


class ListDocsInput(PaginatedOrgBase):
    search_query: str | None = Field(
        default=None,
        description="Optional search query. Supports filters: name:, content:, tag:, created: and operators AND, OR, NOT",
    )
    sort_field: SortField = Field(default="createdAt", description="Field to sort by")
    sort_order: SortOrder = Field(default="desc", description="Sort direction")


class SearchDocsInput(PaginatedOrgBase):
    search_query: str = Field(..., description="Search query string", min_length=1)
    sort_field: SortField = Field(default="createdAt", description="Field to sort by")
    sort_order: SortOrder = Field(default="desc", description="Sort direction")


class UpdateDocInput(DocBase):
    name: str | None = Field(default=None, description="New document name")
    content: str | None = Field(default=None, description="New document content (for search)")


class CreateDocInput(OrgBase):
    file_name: str = Field(
        ...,
        description=(
            "File name including the extension (e.g. 'invoice-2026-01.pdf'). Papra uses this "
            "as the document name and to detect the file type, so always include the extension."
        ),
        min_length=1,
    )
    file_content: str = Field(
        ...,
        description=(
            "Strictly base64-encoded file content. Plain text is NOT accepted here — encode it "
            "as base64 first, or use the text_content field instead."
        ),
    )
    content_type: str | None = Field(
        default=None,
        description="Optional MIME type (e.g. 'application/pdf'). Guessed from file_name if omitted.",
    )
    ocr_languages: str | None = Field(default=None, description="OCR languages to use (e.g., 'eng', 'fra', 'deu')")


class CreateTextDocInput(OrgBase):
    file_name: str = Field(
        ...,
        description="File name including the extension (e.g. 'notes.md', 'report.txt').",
        min_length=1,
    )
    text_content: str = Field(..., description="Plain text content of the document (UTF-8, not encoded).")
    content_type: str | None = Field(
        default=None,
        description="Optional MIME type (e.g. 'text/markdown'). Guessed from file_name if omitted.",
    )


class DocActivityInput(DocBase):
    page_index: int = Field(default=0, ge=0)
    page_size: int = Field(default=100, ge=1, le=100)


# -- Tag models --


class CreateTagInput(OrgBase):
    name: str = Field(..., description="Tag name", min_length=1)
    color: str = Field(..., description="Hex color (e.g. #FF0000)", pattern=r"^#[0-9a-fA-F]{6}$")
    description: str | None = Field(default=None, description="Optional tag description")


class UpdateTagInput(OrgBase):
    tag_id: TagId = Field(..., description="The tag ID")
    name: str | None = Field(default=None, description="New tag name")
    color: str | None = Field(default=None, description="New hex color", pattern=r"^#[0-9a-fA-F]{6}$")
    description: str | None = Field(default=None, description="New description")


class TagIdInput(OrgBase):
    tag_id: TagId = Field(..., description="The tag ID")


class DocTagInput(DocBase):
    tag_id: TagId = Field(..., description="The tag ID")


# -- Custom property models --

PropertyType = Literal[
    "text",
    "number",
    "date",
    "boolean",
    "select",
    "multi_select",
    "user_relation",
    "document_relation",
]
PropertyValue = bool | int | float | str | list[str]
PropertyOptionName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]


class PropertyDefBase(OrgBase):
    property_definition_id: PropertyDefinitionId = Field(
        ..., description="The custom property definition ID"
    )


class PropertyOptionInput(BaseModel):
    id: PropertyOptionId = Field(default=None, description="ID of an existing option to keep")
    name: PropertyOptionName = Field(..., description="Option name (required for both kept and new options)")


class CreatePropertyDefInput(OrgBase):
    name: str = Field(..., description="Custom property name", min_length=1, max_length=255)
    type: PropertyType = Field(..., description="Custom property type")
    description: str | None = Field(default=None, description="Optional description")
    options: list[PropertyOptionName] | None = Field(
        default=None,
        min_length=1,
        description="Non-empty option names, required for 'select' and 'multi_select' types",
    )

    @model_validator(mode="after")
    def validate_options_for_type(self):
        supports_options = self.type in {"select", "multi_select"}
        if supports_options and self.options is None:
            raise ValueError(f"{self.type} requires a non-empty options list")
        if not supports_options and self.options is not None:
            raise ValueError(f"{self.type} does not support options")
        return self


class UpdatePropertyDefInput(PropertyDefBase):
    name: str | None = Field(
        default=None,
        description="Current or new custom property name; required when replacing options on Papra 0.3.0",
        min_length=1,
        max_length=255,
    )
    description: str | None = Field(default=None, description="New description")
    options: list[PropertyOptionInput] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Full replacement option list for 'select'/'multi_select' properties. Every entry "
            "requires a name; kept options also include their id. Omitted options are removed. "
            "An empty option list is invalid."
        ),
    )

    @model_validator(mode="after")
    def validate_option_replacement(self):
        if self.options is not None and self.name is None:
            raise ValueError(
                "name is required when replacing options on Papra 0.3.0; "
                "provide the current or a new definition name"
            )
        return self


class DocPropertyBase(DocBase):
    property_definition_id: PropertyDefinitionId = Field(
        ..., description="The custom property definition ID"
    )


def _has_id_prefix(value: Any, prefix: str) -> bool:
    return isinstance(value, str) and _ID_PATTERNS[prefix].fullmatch(value) is not None


class SetDocPropertyInput(DocPropertyBase):
    property_type: PropertyType = Field(
        ...,
        description="Type of the existing property definition; used to validate the replacement value",
    )
    value: PropertyValue = Field(
        ...,
        description=(
            "Full replacement value: text/date as a string, number as a finite number, boolean "
            "as true/false, select as one option ID, and multi_select/user_relation/"
            "document_relation as arrays of option/user/document IDs. [] clears all values "
            "for multi-valued properties; omitted IDs are removed."
        ),
    )

    @model_validator(mode="after")
    def validate_value_for_type(self):
        property_type = self.property_type
        value = self.value

        if property_type == "text" and (not isinstance(value, str) or len(value) > 10_000):
            raise ValueError("text requires a string of at most 10,000 characters")
        if property_type == "number" and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        ):
            raise ValueError("number requires a finite number, not a boolean or string")
        if property_type == "boolean" and not isinstance(value, bool):
            raise ValueError("boolean requires true or false")
        if property_type == "date" and (
            isinstance(value, bool) or not isinstance(value, (str, int, float))
        ):
            raise ValueError("date requires an ISO 8601 string or a timestamp")
        if property_type == "select" and not _has_id_prefix(value, "cpso"):
            raise ValueError("select requires one valid cpso_ option ID")
        if property_type == "multi_select" and (
            not isinstance(value, list) or any(not _has_id_prefix(item, "cpso") for item in value)
        ):
            raise ValueError("multi_select requires an array of valid cpso_ option IDs; [] clears values")
        if property_type == "user_relation" and (
            not isinstance(value, list) or any(not _has_id_prefix(item, "usr") for item in value)
        ):
            raise ValueError("user_relation requires an array of valid usr_ IDs; [] clears values")
        if property_type == "document_relation" and (
            not isinstance(value, list) or any(not _has_id_prefix(item, "doc") for item in value)
        ):
            raise ValueError("document_relation requires an array of valid doc_ IDs; [] clears values")

        return self


# ---------------------------------------------------------------------------
# API Key
# ---------------------------------------------------------------------------


@mcp.tool(name="papra_check_api_key", annotations={"title": "Check API Key", **READ_ONLY})
@tool_errors
async def papra_check_api_key() -> str:
    """Check the currently used API key. Returns the key's ID, name, and permissions."""
    return _pretty_json(await papra_request("GET", "/api/api-keys/current"))


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


@mcp.tool(name="papra_list_organizations", annotations={"title": "List Organizations", **READ_ONLY})
@tool_errors
async def papra_list_organizations() -> str:
    """List all organizations accessible to the authenticated user."""
    return _pretty_json(await papra_request("GET", "/api/organizations"))


@mcp.tool(name="papra_get_organization", annotations={"title": "Get Organization", **READ_ONLY})
@tool_errors
async def papra_get_organization(params: OrgBase) -> str:
    """Get details of a specific organization by its ID."""
    return _pretty_json(
        await papra_request("GET", f"/api/organizations/{_path_segment(params.organization_id)}")
    )


@mcp.tool(name="papra_create_organization", annotations={"title": "Create Organization", **WRITE})
@tool_errors
async def papra_create_organization(params: CreateOrgInput) -> str:
    """Create a new organization. The name must be 3-50 characters."""
    return _pretty_json(
        await papra_request("POST", "/api/organizations", body={"name": params.name})
    )


@mcp.tool(
    name="papra_update_organization",
    annotations={"title": "Update Organization", **DESTRUCTIVE},
)
@tool_errors
async def papra_update_organization(params: OrgName) -> str:
    """Update an organization's name."""
    return _pretty_json(
        await papra_request(
            "PUT", f"/api/organizations/{_path_segment(params.organization_id)}", body={"name": params.name}
        )
    )


@mcp.tool(name="papra_delete_organization", annotations={"title": "Delete Organization", **DESTRUCTIVE})
@tool_errors
async def papra_delete_organization(params: OrgBase) -> str:
    """Delete an organization by its ID. This is a destructive operation."""
    await papra_request("DELETE", f"/api/organizations/{_path_segment(params.organization_id)}")
    return f"Organization {params.organization_id} deleted successfully."


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


async def _list_documents(
    *,
    organization_id: str,
    page_index: int,
    page_size: int,
    search_query: str | None,
    sort_field: str,
    sort_order: str,
) -> str:
    data = await papra_request(
        "GET",
        f"/api/organizations/{_path_segment(organization_id)}/documents",
        params={
            "pageIndex": page_index,
            "pageSize": page_size,
            "searchQuery": search_query,
            "sortField": sort_field,
            "sortOrder": sort_order,
        },
    )
    return _pretty_json(data)


@mcp.tool(name="papra_list_documents", annotations={"title": "List Documents", **READ_ONLY})
@tool_errors
async def papra_list_documents(params: ListDocsInput) -> str:
    """List documents in an organization with optional pagination, search, and sorting.

    The searchQuery supports advanced filters like name:, content:, tag:, created:
    and logical operators AND, OR, NOT.
    """
    return await _list_documents(
        organization_id=params.organization_id,
        page_index=params.page_index,
        page_size=params.page_size,
        search_query=params.search_query,
        sort_field=params.sort_field,
        sort_order=params.sort_order,
    )


@mcp.tool(name="papra_search_documents", annotations={"title": "Search Documents", **READ_ONLY})
@tool_errors
async def papra_search_documents(params: SearchDocsInput) -> str:
    """Search documents by name or content. Supports advanced search syntax with
    filters (name:, content:, tag:, created:), logical operators (AND, OR, NOT),
    and grouping with parentheses.
    """
    return await _list_documents(
        organization_id=params.organization_id,
        page_index=params.page_index,
        page_size=params.page_size,
        search_query=params.search_query,
        sort_field=params.sort_field,
        sort_order=params.sort_order,
    )


@mcp.tool(
    name="papra_list_deleted_documents",
    annotations={"title": "List Deleted Documents (Trash)", **READ_ONLY},
)
@tool_errors
async def papra_list_deleted_documents(params: PaginatedOrgBase) -> str:
    """List deleted documents (trash) in an organization."""
    data = await papra_request(
        "GET",
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/deleted",
        params={"pageIndex": params.page_index, "pageSize": params.page_size},
    )
    return _pretty_json(data)


async def _create_document(
    *,
    organization_id: str,
    file_name: str,
    file_bytes: bytes,
    content_type: str | None,
    ocr_languages: str | None = None,
) -> str:
    # A 3-tuple with content_type=None would send a literal "None" header, so
    # fall back to the 2-tuple form and let httpx guess from the file name.
    file_field: tuple[str, bytes] | tuple[str, bytes, str] = (
        (file_name, file_bytes, content_type) if content_type else (file_name, file_bytes)
    )
    data = {"ocrLanguages": ocr_languages} if ocr_languages else None

    response = await papra_upload_request(
        f"/api/organizations/{_path_segment(organization_id)}/documents",
        files={"file": file_field},
        data=data,
    )

    if response.status_code == 204 or not response.content:
        return f"Document {file_name!r} created successfully."
    return _pretty_json(response.json())


@mcp.tool(name="papra_create_document", annotations={"title": "Create Document", **WRITE})
@tool_errors
async def papra_create_document(params: CreateDocInput) -> str:
    """Create a document by uploading base64-encoded file content.

    file_content must be strictly valid base64 — plain text is rejected rather
    than silently uploaded as corrupt bytes. Use papra_create_text_document to
    upload plain text. file_name should include the extension, since Papra uses
    it as the document name and for file type detection.
    """
    try:
        file_bytes = base64.b64decode(params.file_content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError(
            f"file_content is not valid base64 ({exc}). Encode the file as base64 first, "
            "or use papra_create_text_document for plain text."
        ) from exc

    return await _create_document(
        organization_id=params.organization_id,
        file_name=params.file_name,
        file_bytes=file_bytes,
        content_type=params.content_type,
        ocr_languages=params.ocr_languages,
    )


@mcp.tool(name="papra_create_text_document", annotations={"title": "Create Text Document", **WRITE})
@tool_errors
async def papra_create_text_document(params: CreateTextDocInput) -> str:
    """Create a document from plain text, without requiring base64 encoding.

    The text is uploaded as a UTF-8 file named file_name.
    """
    return await _create_document(
        organization_id=params.organization_id,
        file_name=params.file_name,
        file_bytes=params.text_content.encode("utf-8"),
        content_type=params.content_type,
    )


@mcp.tool(name="papra_get_document", annotations={"title": "Get Document", **READ_ONLY})
@tool_errors
async def papra_get_document(params: DocBase) -> str:
    """Get a document's metadata by its ID."""
    data = await papra_request(
        "GET",
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/{_path_segment(params.document_id)}",
    )
    return _pretty_json(data)


@mcp.tool(
    name="papra_get_document_content",
    annotations={"title": "Get Document Content", **READ_ONLY},
)
@tool_errors
async def papra_get_document_content(params: DocBase) -> str:
    """Get the file content of a document by its ID.

    For text-based documents (plain text, HTML, CSV, JSON, XML, Markdown, etc.)
    the content is returned directly as text. For PDF documents the text is
    extracted and returned as plain text so that LLMs can process it directly.
    For other binary documents (images, archives, etc.) the content is returned
    as a base64-encoded string together with the content type so the caller can
    decode it. Oversized text is truncated; oversized binary content is refused.
    """
    response = await papra_file_request(
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/{_path_segment(params.document_id)}/file",
    )
    raw = response.content
    content_type = response.headers.get("content-type", "application/octet-stream")

    if _is_text_content(content_type):
        text = _decode_text(raw, response.encoding)
        if text is not None:
            return _truncate_text(text)
        # Not decodable despite the header — fall through to the binary path.

    media_type = content_type.split(";")[0].strip().lower()
    if media_type == "application/pdf" or _looks_like_pdf(raw):
        text = _extract_pdf_text(raw)
        if text is not None:
            return _truncate_text(text)

    if len(raw) > _max_content_bytes:
        raise ToolError(
            f"Document is {len(raw)} bytes of binary content ({content_type}), which exceeds "
            f"the {_max_content_bytes} byte limit. Truncating base64 would corrupt it, so "
            "nothing was returned. Download the file directly from Papra, or raise "
            "PAPRA_MAX_CONTENT_BYTES."
        )

    return _pretty_json({
        "content_type": content_type,
        "encoding": "base64",
        "data": base64.b64encode(raw).decode("ascii"),
    })


@mcp.tool(
    name="papra_get_document_statistics",
    annotations={"title": "Get Document Statistics", **READ_ONLY},
)
@tool_errors
async def papra_get_document_statistics(params: OrgBase) -> str:
    """Get statistics (document count and total size) for an organization."""
    data = await papra_request(
        "GET",
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/statistics",
    )
    return _pretty_json(data)


@mcp.tool(name="papra_update_document", annotations={"title": "Update Document", **DESTRUCTIVE})
@tool_errors
async def papra_update_document(params: UpdateDocInput) -> str:
    """Update a document's name or content (for search indexing). Both fields are optional."""
    body = _body_from({"name": params.name, "content": params.content})
    data = await papra_request(
        "PATCH",
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/{_path_segment(params.document_id)}",
        body=body,
    )
    return _pretty_json(data)


@mcp.tool(name="papra_reprocess_document", annotations={"title": "Reprocess Document", **WRITE})
@tool_errors
async def papra_reprocess_document(params: DocBase) -> str:
    """Enqueue a background task to reprocess a document with the current instance configuration.

    Reprocessing re-runs content extraction and OCR using the document's configured OCR
    languages. Existing content and tags are not cleared while the task is pending.
    Returns immediately with HTTP 202 — it confirms the task was queued, not that
    processing has completed.

    Rate limited to 100 requests per hour per organization by default. Requires the
    documents:update API key permission.
    """
    await papra_request(
        "POST",
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/{_path_segment(params.document_id)}/reprocess",
    )
    return (
        f"Reprocessing task for document {params.document_id} queued successfully. "
        "Processing happens asynchronously in the background."
    )


@mcp.tool(name="papra_delete_document", annotations={"title": "Delete Document", **DESTRUCTIVE})
@tool_errors
async def papra_delete_document(params: DocBase) -> str:
    """Soft-delete a document (moves to trash). Permanently deleted after retention period."""
    await papra_request(
        "DELETE",
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/{_path_segment(params.document_id)}",
    )
    return f"Document {params.document_id} moved to trash successfully."


@mcp.tool(
    name="papra_get_document_activity",
    annotations={"title": "Get Document Activity", **READ_ONLY},
)
@tool_errors
async def papra_get_document_activity(params: DocActivityInput) -> str:
    """Get the activity log of a document."""
    data = await papra_request(
        "GET",
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/{_path_segment(params.document_id)}/activity",
        params={"pageIndex": params.page_index, "pageSize": params.page_size},
    )
    return _pretty_json(data)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


@mcp.tool(name="papra_list_tags", annotations={"title": "List Tags", **READ_ONLY})
@tool_errors
async def papra_list_tags(params: OrgBase) -> str:
    """List all tags in an organization."""
    return _pretty_json(
        await papra_request("GET", f"/api/organizations/{_path_segment(params.organization_id)}/tags")
    )


@mcp.tool(name="papra_create_tag", annotations={"title": "Create Tag", **WRITE})
@tool_errors
async def papra_create_tag(params: CreateTagInput) -> str:
    """Create a new tag in an organization with a name, color, and optional description."""
    body: dict[str, Any] = {"name": params.name, "color": params.color}
    if params.description is not None:
        body["description"] = params.description

    data = await papra_request(
        "POST", f"/api/organizations/{_path_segment(params.organization_id)}/tags", body=body
    )
    return _pretty_json(data)


@mcp.tool(name="papra_update_tag", annotations={"title": "Update Tag", **DESTRUCTIVE})
@tool_errors
async def papra_update_tag(params: UpdateTagInput) -> str:
    """Update a tag's name, color, or description. All fields are optional."""
    body = _body_from(
        {"name": params.name, "color": params.color, "description": params.description}
    )
    data = await papra_request(
        "PUT",
        f"/api/organizations/{_path_segment(params.organization_id)}/tags/{_path_segment(params.tag_id)}",
        body=body,
    )
    return _pretty_json(data)


@mcp.tool(name="papra_delete_tag", annotations={"title": "Delete Tag", **DESTRUCTIVE})
@tool_errors
async def papra_delete_tag(params: TagIdInput) -> str:
    """Delete a tag by its ID."""
    await papra_request(
        "DELETE",
        f"/api/organizations/{_path_segment(params.organization_id)}/tags/{_path_segment(params.tag_id)}",
    )
    return f"Tag {params.tag_id} deleted successfully."


@mcp.tool(
    name="papra_add_tag_to_document",
    annotations={"title": "Add Tag to Document", **IDEMPOTENT_WRITE},
)
@tool_errors
async def papra_add_tag_to_document(params: DocTagInput) -> str:
    """Associate a tag with a document."""
    await papra_request(
        "POST",
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/{_path_segment(params.document_id)}/tags",
        body={"tagId": params.tag_id},
    )
    return f"Tag {params.tag_id} added to document {params.document_id} successfully."


@mcp.tool(
    name="papra_remove_tag_from_document",
    annotations={"title": "Remove Tag from Document", **DESTRUCTIVE},
)
@tool_errors
async def papra_remove_tag_from_document(params: DocTagInput) -> str:
    """Remove a tag association from a document."""
    await papra_request(
        "DELETE",
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/{_path_segment(params.document_id)}/tags/{_path_segment(params.tag_id)}",
    )
    return f"Tag {params.tag_id} removed from document {params.document_id} successfully."


# ---------------------------------------------------------------------------
# Custom properties
# ---------------------------------------------------------------------------


@mcp.tool(
    name="papra_list_custom_properties",
    annotations={"title": "List Custom Property Definitions", **READ_ONLY},
)
@tool_errors
async def papra_list_custom_properties(params: OrgBase) -> str:
    """List all custom property definitions of an organization."""
    data = await papra_request(
        "GET", f"/api/organizations/{_path_segment(params.organization_id)}/custom-properties"
    )
    return _pretty_json(data)


@mcp.tool(
    name="papra_get_custom_property",
    annotations={"title": "Get Custom Property Definition", **READ_ONLY},
)
@tool_errors
async def papra_get_custom_property(params: PropertyDefBase) -> str:
    """Get a custom property definition by its ID."""
    data = await papra_request(
        "GET",
        f"/api/organizations/{_path_segment(params.organization_id)}/custom-properties/{_path_segment(params.property_definition_id)}",
    )
    return _pretty_json(data)


@mcp.tool(
    name="papra_create_custom_property",
    annotations={"title": "Create Custom Property Definition", **WRITE},
)
@tool_errors
async def papra_create_custom_property(params: CreatePropertyDefInput) -> str:
    """Create a custom property definition in an organization.

    Supported types: text, number, date, boolean, select, multi_select,
    user_relation, document_relation. The options field only applies to the
    select and multi_select types.
    """
    body: dict[str, Any] = {"name": params.name, "type": params.type}
    if params.description is not None:
        body["description"] = params.description
    if params.options is not None:
        body["options"] = [{"name": name} for name in params.options]

    data = await papra_request(
        "POST", f"/api/organizations/{_path_segment(params.organization_id)}/custom-properties", body=body
    )
    return _pretty_json(data)


@mcp.tool(
    name="papra_update_custom_property",
    annotations={"title": "Update Custom Property Definition", **WRITE, "destructiveHint": True},
)
@tool_errors
async def papra_update_custom_property(params: UpdatePropertyDefInput) -> str:
    """Update a custom property definition's name, description, or options.

    The non-empty options list replaces the existing one. Every option requires
    a name; kept options also include their id. Options left out are removed.
    When replacing options, also provide the current or new definition name;
    Papra 0.3.0 cannot process an options-only update. New options receive fresh
    IDs, so repeating a request with id-less options is not idempotent.
    """
    options = (
        [option.model_dump(exclude_none=True) for option in params.options]
        if params.options is not None
        else None
    )
    body = _body_from(
        {"name": params.name, "description": params.description, "options": options}
    )
    data = await papra_request(
        "PUT",
        f"/api/organizations/{_path_segment(params.organization_id)}/custom-properties/{_path_segment(params.property_definition_id)}",
        body=body,
    )
    return _pretty_json(data)


@mcp.tool(
    name="papra_delete_custom_property",
    annotations={"title": "Delete Custom Property Definition", **DESTRUCTIVE},
)
@tool_errors
async def papra_delete_custom_property(params: PropertyDefBase) -> str:
    """Delete a custom property definition and its values on all documents."""
    await papra_request(
        "DELETE",
        f"/api/organizations/{_path_segment(params.organization_id)}/custom-properties/{_path_segment(params.property_definition_id)}",
    )
    return f"Custom property {params.property_definition_id} deleted successfully."


@mcp.tool(
    name="papra_list_document_custom_properties",
    annotations={"title": "List Document Custom Properties", **READ_ONLY},
)
@tool_errors
async def papra_list_document_custom_properties(params: DocBase) -> str:
    """List all custom properties currently set on a document."""
    data = await papra_request(
        "GET",
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/{_path_segment(params.document_id)}/custom-properties",
    )
    return _pretty_json(data)


@mcp.tool(
    name="papra_set_document_custom_property",
    annotations={"title": "Set Document Custom Property", **DESTRUCTIVE},
)
@tool_errors
async def papra_set_document_custom_property(params: SetDocPropertyInput) -> str:
    """Replace the value of a custom property on a document.

    property_type must match the existing definition. Text/date use strings,
    number uses a finite number, boolean uses true/false, and select uses one
    option ID. multi_select, user_relation, and document_relation require arrays
    of their respective IDs. Setting an array replaces all previous values:
    omitted IDs are removed and [] clears the property's values.
    """
    await papra_request(
        "PUT",
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/{_path_segment(params.document_id)}"
        f"/custom-properties/{_path_segment(params.property_definition_id)}",
        body={"value": params.value},
    )
    return (
        f"Custom property {params.property_definition_id} set on "
        f"document {params.document_id} successfully."
    )


@mcp.tool(
    name="papra_clear_document_custom_property",
    annotations={"title": "Clear Document Custom Property", **DESTRUCTIVE},
)
@tool_errors
async def papra_clear_document_custom_property(params: DocPropertyBase) -> str:
    """Clear a custom property value from a document."""
    await papra_request(
        "DELETE",
        f"/api/organizations/{_path_segment(params.organization_id)}/documents/{_path_segment(params.document_id)}"
        f"/custom-properties/{_path_segment(params.property_definition_id)}",
    )
    return (
        f"Custom property {params.property_definition_id} cleared from "
        f"document {params.document_id} successfully."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the Papra MCP server."""
    # stdout carries the MCP protocol on the stdio transport — logs must go to stderr.
    logging.basicConfig(
        level=os.environ.get("PAPRA_LOG_LEVEL", "INFO").upper(),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp.run()


if __name__ == "__main__":
    main()
