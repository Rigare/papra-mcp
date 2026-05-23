# AGENTS.md

> **Read this file at the start of every session.** It is the canonical source of project conventions for AI agents working in this repo. Re-check it before non-trivial work; update it whenever a new convention is established.

## Workflow Rules

- **Never work directly on `main`.** Before making any code changes, create a new branch (`git checkout -b feature/<short-slug>` or `fix/<short-slug>`). If you discover you are on `main` with uncommitted changes, create the branch first — the uncommitted changes will move with you.
- Commit only when the user explicitly requests it. Update relevant docs (README, AGENTS.md) in the same change set as the code.
- When adding or removing tools, update the **Tools (N)** count in [README.md](README.md) and the per-section tables.
- When the Papra API surface changes, sync this server: cross-check against <https://docs.papra.app/resources/api-endpoints/> and the upstream routes under [papra-hq/papra](https://github.com/papra-hq/papra/tree/main/apps/papra-server/src/modules).

## Required Environment
`PAPRA_BASE_URL` (valid URL with scheme + host) and `PAPRA_API_KEY` must be set. The server validates both at startup and exits immediately if either is missing or `PAPRA_BASE_URL` is malformed.

## Commands
- Start server: `python papra_mcp.py` (or `papra-mcp` after `pip install -e .`)
- Run tests: `pytest tests/` (no live Papra instance required, uses mocked HTTP requests)

## Architecture
- Single-file application: all logic in [papra_mcp.py](papra_mcp.py), built with FastMCP.
- `httpx.AsyncClient` is initialized once at startup via the lifespan context manager, using env vars for base URL and auth header.
- All API calls go through `papra_request` (JSON) or `papra_file_request` (raw response); errors are formatted via `format_error`.
- Input validation uses Pydantic models. Shared bases: `OrgBase`, `PaginatedOrgBase`, `DocBase`. Enumerated values (roles, sort fields, API-key permissions) are modeled as `typing.Literal`.
- PDF content is extracted to plain text via `pymupdf`; falls back to base64-encoded JSON for non-text PDFs or other binary content.

## Tool Conventions
- Tool names are snake_case with the `papra_` prefix.
- Each `@mcp.tool` decorator sets `annotations` with `title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` — keep these accurate (destructive endpoints must set `destructiveHint: True`).
- Tools return either `_pretty_json(data)` or a short status string; wrap the API call in `try/except` and return `format_error(exc)` on failure.

## Tool-Specific Notes
- `papra_get_document_content` returns: plain text for text/* MIME types, extracted PDF text, or base64 JSON for binary content.
- Tag color values must match the regex `^#[0-9a-fA-F]{6}$` (enforced by Pydantic input models).
- All resource IDs (organization, document, tag, member, api_key) are non-empty strings (minimum length 1).
- API-key permissions are typed (`organizations|documents|tags`:`create|read|update|delete`) — keep the `ApiKeyPermission` Literal in sync with upstream `API_KEY_PERMISSIONS_VALUES`.
- Organization roles are typed (`member`, `admin`, `owner`) — keep `OrgRole` in sync with upstream `ORGANIZATION_ROLES`.
