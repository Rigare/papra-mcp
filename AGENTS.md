# AGENTS.md

## Required Environment
`PAPRA_BASE_URL` (valid URL with scheme + host) and `PAPRA_API_KEY` must be set. The server validates both at startup and exits immediately if either is missing or `PAPRA_BASE_URL` is malformed.

## Commands
- Start server: `python papra_mcp.py` (or `papra-mcp` after `pip install -e .`)
- Run tests: `pytest tests/` (no live Papra instance required, uses mocked HTTP requests)

## Architecture
- Single-file application: all logic in `papra_mcp.py`, built with FastMCP.
- `httpx.AsyncClient` is initialized once at startup via the lifespan context manager, using env vars for base URL and auth header.
- PDF content is extracted to plain text via `pymupdf`; falls back to base64-encoded JSON for non-text PDFs or other binary content.

## Tool-Specific Notes
- `papra_get_document_content` returns: plain text for text/* MIME types, extracted PDF text, or base64 JSON for binary content.
- Tag color values must match the regex `^#[0-9a-fA-F]{6}$` (enforced by Pydantic input models).
- All resource IDs (organization, document, tag) are non-empty strings (minimum length 1).
