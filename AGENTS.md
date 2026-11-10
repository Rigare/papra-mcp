# AGENTS.md

## Required Environment
`PAPRA_BASE_URL` (valid URL with scheme + host) and `PAPRA_API_KEY` must be set. Optional: `PAPRA_TIMEOUT` (seconds, default 60), `PAPRA_MAX_CONTENT_BYTES` (default 5 MB), `PAPRA_LOG_LEVEL` (default INFO). All are validated at startup; the server exits immediately on a missing or malformed value.

## Commands
- Install: `pip install -e ".[dev]"`
- Start server: `python papra_mcp.py` (or `papra-mcp` after install)
- Run tests: `pytest` (no live Papra instance required, uses mocked HTTP requests)
- Lint: `ruff check .`

## Architecture
- Single-file application: all logic in `papra_mcp.py`, built with FastMCP.
- `httpx.AsyncClient` is initialized once at startup via the lifespan context manager, using env vars for base URL, auth header, timeout, and redirect handling.
- Every tool is wrapped in `@tool_errors`, which converts exceptions into `ToolError` so MCP flags the result with `isError`. Do not return error strings from a tool.
- Tools take a single Pydantic model named `params`; validation lives in the model, not the tool body.

## Dependency constraints
- `mcp[cli]` is pinned to `<2`. MCP SDK 2.0 renamed `mcp.server.fastmcp` to `mcp.server.mcpserver` and `FastMCP` to `MCPServer`, which breaks this server on import. Migrating means updating the import, the `mcp = FastMCP(...)` construction, and the `ToolError` import path.

## Papra API notes
- The supported target is Papra 0.3.0 at commit `648c223b807500f0258c822e0321d6ef4964f064`; do not claim compatibility with other versions without a new live verification.
- There is **no** `/documents/search` endpoint. Search runs through `GET /documents` with the `searchQuery` parameter; both `papra_list_documents` and `papra_search_documents` go through `_list_documents`.
- `GET /documents` also accepts `sortField` (`createdAt`, `updatedAt`, `name`, `documentDate`) and `sortOrder` (`asc`, `desc`).
- Upload is `multipart/form-data` with a `file` field and optional `ocrLanguages`. Papra takes the document name and file type from the multipart file name, so it must be a real name with an extension.
- Restore, permanent deletion, trash emptying, and tagging-rule apply are session-only routes at the supported version. Do not register API-key tools for them or add session-cookie fallback. Listing deleted documents supports API keys.

## Tool-Specific Notes
- `papra_get_document_content` returns: plain text for text MIME types, extracted PDF text, or base64 JSON for binary. Text decoding is strict so mislabelled binaries fall back to base64. Text over the size limit is truncated; oversized binary is refused.
- `papra_create_document` requires strictly valid base64 (`validate=True`); plain text belongs in `papra_create_text_document`.
- Tag color values must match the regex `^#[0-9a-fA-F]{6}$` (enforced by Pydantic input models).
- Resource IDs use `<prefix>_<24 lowercase letters or digits>` with resource-specific prefixes (`org`, `doc`, `tag`, `cpd`, `cpso`, `usr`). Reject navigation and encoded variants before HTTP and encode valid IDs as one path segment.
- Multi-select and relation values are full replacement arrays; omitted IDs are removed and `[]` clears values. Option replacement requires every option's `name`, retained option IDs, and the current or new definition `name` on Papra 0.3.0.
- Update tools raise `ToolError` when no field is provided, instead of silently doing nothing.
