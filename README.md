# Papra MCP Server

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

An [MCP](https://modelcontextprotocol.io) (Model Context Protocol) server for the [Papra](https://papra.app) document management API. Gives AI assistants the ability to manage organizations, documents, tags, and custom properties in your Papra instance.

Exposes 32 tools whose routes support Bearer API-key authentication. Session-only routes are deliberately not registered, and the server never falls back to session cookies.

## Prerequisites

- Python 3.10+
- A running [Papra](https://github.com/papra-hq/papra) instance
- An API key from your Papra account settings
- Papra monorepo version 0.3.0 at commit `648c223b807500f0258c822e0321d6ef4964f064`; compatibility with other server versions is not claimed

## Installation

```bash
git clone https://github.com/Rigare/papra-mcp.git
cd papra-mcp
pip install -e .
```

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PAPRA_BASE_URL` | yes | — | Your Papra instance URL (e.g. `https://papra.example.com`) |
| `PAPRA_API_KEY` | yes | — | API key from your Papra account settings |
| `PAPRA_TIMEOUT` | no | `60` | HTTP timeout in seconds |
| `PAPRA_MAX_CONTENT_BYTES` | no | `5242880` | Size limit for document content returned to the model (5 MB) |
| `PAPRA_LOG_LEVEL` | no | `INFO` | Log level; logs always go to stderr |

All values are validated at startup. The server exits with a clear error if a required variable is missing, if `PAPRA_BASE_URL` is not a well-formed URL (missing scheme or host), or if a numeric variable is not a positive number.

## Usage

### Claude Desktop

Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "papra": {
      "command": "python",
      "args": ["/path/to/papra-mcp/papra_mcp.py"],
      "env": {
        "PAPRA_BASE_URL": "https://papra.example.com",
        "PAPRA_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

### Claude Code

Add via the CLI:

```bash
claude mcp add papra -- python /path/to/papra-mcp/papra_mcp.py
```

Then set the environment variables in your shell or `.env` file before launching Claude Code.

### Development

```bash
pip install -e ".[dev]"
pytest              # no live Papra instance required, HTTP is mocked
ruff check .
mcp dev papra_mcp.py    # interactive tool inspector
```

### Live contract verification

`tests/test_live_papra.py` is an opt-in integration suite for a **separate disposable Papra instance**, never a production instance. It creates a random account, API keys, organizations, documents, and custom properties, and replaces or clears test values. The target must use SQLite, listen on loopback, and report both version 0.3.0 and commit `648c223b807500f0258c822e0321d6ef4964f064` from `/api/config`; the test stops before creating data if either differs.

Run Papra from its own checkout at that commit. Its Dockerfile specifies Node 26.12.1. Install dependencies with `pnpm install --frozen-lockfile`, build with `pnpm --filter "@papra/app-server..." run build`, then run `pnpm migrate:up:prod` and `pnpm start` in `apps/papra-server`. Set `PAPRA_VERSION=0.3.0` and `GIT_COMMIT` to the pinned commit, use matching local base URLs and a random `AUTH_SECRET`, and keep `PROCESS_MODE=web`. A checkout nested in another Git repository must retain its own `.git` metadata so Papra does not report the outer project's commit.

With the disposable server available at `http://127.0.0.1:37065`, run:

```bash
PAPRA_RUN_LIVE_TESTS=1 \
PAPRA_LIVE_BASE_URL=http://127.0.0.1:37065 \
pytest tests/test_live_papra.py
```

Without `PAPRA_RUN_LIVE_TESTS=1`, these tests are skipped. Browser-session authentication is used only to provision the throwaway account and API keys; all adapter calls under test use Bearer API keys.

## Compatibility and API-key capabilities

The compatibility target is **Papra monorepo 0.3.0**, commit `648c223b807500f0258c822e0321d6ef4964f064`. Route registration and input validation follow that version's authentication middleware and validators. Other versions may work, but are not advertised as supported without a new verification run.

Authentication uses only the `Authorization: Bearer …` API-key header. The adapter does not create, accept, or fall back to session cookies. Create keys with only the permissions required for the tools you use:

| Tools | Minimum API-key scopes |
|-------|------------------------|
| `papra_check_api_key` | Any valid API key; no additional scope |
| `papra_list_organizations`, `papra_get_organization` | `organizations:read` |
| `papra_create_organization` | `organizations:create` |
| `papra_update_organization` | `organizations:update` |
| `papra_delete_organization` | `organizations:delete` |
| `papra_list_documents`, `papra_search_documents`, `papra_list_deleted_documents`, `papra_get_document`, `papra_get_document_content`, `papra_get_document_statistics`, `papra_get_document_activity` | `documents:read` |
| `papra_create_document`, `papra_create_text_document` | `documents:create` |
| `papra_update_document`, `papra_reprocess_document` | `documents:update` |
| `papra_delete_document` | `documents:delete` |
| `papra_list_tags` | `tags:read` |
| `papra_create_tag` | `tags:create` |
| `papra_update_tag` | `tags:update` |
| `papra_delete_tag` | `tags:delete` |
| `papra_add_tag_to_document`, `papra_remove_tag_from_document` | `documents:update` **and** `tags:read` |
| `papra_list_custom_properties`, `papra_get_custom_property` | `custom-properties:read` |
| `papra_create_custom_property` | `custom-properties:create` |
| `papra_update_custom_property` | `custom-properties:update` |
| `papra_delete_custom_property` | `custom-properties:delete` |
| `papra_list_document_custom_properties` | `documents:read` **and** `custom-properties:read` |
| `papra_set_document_custom_property`, `papra_clear_document_custom_property` | `documents:update` **and** `custom-properties:read` |

The following upstream routes require a browser session at the target version, regardless of API-key scopes, and therefore have **no MCP tool**: document restore, permanent document deletion, emptying trash, and applying a tagging rule. Listing deleted documents remains available with `documents:read`.

### Resource IDs

Resource IDs use Papra's actual `<prefix>_<24 lowercase letters or digits>` format. The prefixes are `org_`, `doc_`, `tag_`, `cpd_` (property definitions), `cpso_` (property options), and `usr_` (users). IDs are bound to their resource type and encoded as one URL path segment. Empty values, wrong prefixes, separators, query/fragment characters, dot segments, and percent-encoded or repeatedly encoded navigation variants are rejected before any HTTP request.

## Tools (32)

### API Key

| Tool | Description |
|------|-------------|
| `papra_check_api_key` | Check the current API key's ID, name, and permissions |

### Organizations

| Tool | Description |
|------|-------------|
| `papra_list_organizations` | List all accessible organizations |
| `papra_get_organization` | Get details of a specific organization |
| `papra_create_organization` | Create a new organization (name: 3-50 chars) |
| `papra_update_organization` | Update an organization's name |
| `papra_delete_organization` | Delete an organization (**destructive**) |

### Documents

| Tool | Description |
|------|-------------|
| `papra_list_documents` | List documents with pagination, search, and sorting |
| `papra_search_documents` | Search documents (supports `name:`, `content:`, `tag:`, `created:`, `AND`, `OR`, `NOT`) |
| `papra_create_document` | Upload a document from base64-encoded content |
| `papra_create_text_document` | Upload a document from plain text, no base64 needed |
| `papra_get_document` | Get a document's metadata |
| `papra_get_document_content` | Get file content (text directly, PDFs as extracted text, other binary as base64) |
| `papra_get_document_statistics` | Get document count and total size for an organization |
| `papra_update_document` | Update a document's name or content |
| `papra_reprocess_document` | Queue background reprocessing (re-extract text, re-run OCR) |
| `papra_delete_document` | Soft-delete a document (moves to trash) |
| `papra_get_document_activity` | Get the activity log of a document |

### Trash

| Tool | Description |
|------|-------------|
| `papra_list_deleted_documents` | List deleted documents (trash); restoring or permanently deleting is session-only in Papra |

### Tags

| Tool | Description |
|------|-------------|
| `papra_list_tags` | List all tags in an organization |
| `papra_create_tag` | Create a tag with name, hex color, and optional description |
| `papra_update_tag` | Update a tag's name, color, or description |
| `papra_delete_tag` | Delete a tag (**destructive**) |
| `papra_add_tag_to_document` | Associate a tag with a document |
| `papra_remove_tag_from_document` | Remove a tag from a document |

Applying a tagging rule is session-only in Papra and is not exposed as an MCP tool.

### Custom Properties

| Tool | Description |
|------|-------------|
| `papra_list_custom_properties` | List all custom property definitions |
| `papra_get_custom_property` | Get a custom property definition |
| `papra_create_custom_property` | Create a custom property definition |
| `papra_update_custom_property` | Update name, description, or replace options |
| `papra_delete_custom_property` | Delete a definition and its values (**destructive**) |
| `papra_list_document_custom_properties` | List the properties set on a document |
| `papra_set_document_custom_property` | Replace a property value on a document |
| `papra_clear_document_custom_property` | Clear a property value from a document |

Custom properties support the types `text`, `number`, `date`, `boolean`, `select`, `multi_select`, `user_relation`, and `document_relation`. They need the API-key permissions listed above. Creating a select or multi-select definition requires a non-empty list of option names. Updating options requires the current or new definition `name` plus full `{id, name}` objects for retained options and `{name}` objects for new options; omitted existing options are removed. Papra 0.3.0 otherwise fails internally while applying an options-only update, so the adapter rejects that incomplete payload before HTTP. An option list cannot be empty because select definitions require at least one option. Id-less entries always create new options with fresh IDs, so updates containing them are not idempotent; fetch the definition and use the returned IDs in subsequent replacement payloads.

Select takes one `cpso_` option ID. Multi-select, user relation, and document relation take arrays of `cpso_`, `usr_`, and `doc_` IDs respectively, never a scalar. Setting a value replaces all previous values: omitted IDs are removed, and `[]` clears a multi-valued property. Use `papra_clear_document_custom_property` to remove a property value of any type.

## Uploading documents

`papra_create_document` takes strictly valid base64 in `file_content`. Plain text is **rejected** rather than silently decoded into corrupt bytes — use `papra_create_text_document` for text.

`file_name` is required and should include the extension. Papra derives the document name and the file type from it, so `invoice-2026-01.pdf` produces a usable document where `upload` does not. `content_type` is optional and is guessed from the file name when omitted.

## Reading documents

`papra_get_document_content` returns:

- **Text** (`text/*`, JSON, XML) decoded directly. Decoding is strict, so a binary file mislabelled as `text/plain` falls back to base64 instead of returning replacement characters.
- **PDFs** as extracted plain text via [PyMuPDF](https://pymupdf.readthedocs.io/), so models can read them without decoding binary data. Detection works both via the `application/pdf` content type and via the `%PDF` magic bytes, which covers servers that return `application/octet-stream`. If no text can be extracted (scanned documents without OCR, image-only or corrupt PDFs), it falls back to base64.
- **Other binary** as base64-encoded JSON together with the content type.

Text above `PAPRA_MAX_CONTENT_BYTES` is truncated with an explicit marker. Binary content above the limit is refused with an error, since a truncated base64 blob would be corrupt.

## Error handling

Failed calls raise a `ToolError`, so the MCP result is flagged with `isError` and a model cannot mistake an API error for a successful response. Messages include the HTTP status and Papra's own error text.

## License

[Apache-2.0](LICENSE)
