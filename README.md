# Papra MCP Server

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

An [MCP](https://modelcontextprotocol.io) (Model Context Protocol) server for the [Papra](https://papra.app) document management API. Gives AI assistants the ability to manage organizations, documents, tags, and custom properties in your Papra instance.

Covers all 30 endpoints from the [Papra API documentation](https://docs.papra.app/resources/api-endpoints/), plus trash restore and permanent deletion.

## Prerequisites

- Python 3.10+
- A running [Papra](https://github.com/papra-hq/papra) instance
- An API key from your Papra account settings

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

## Tools (35)

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
| `papra_delete_document` | Soft-delete a document (moves to trash) |
| `papra_get_document_activity` | Get the activity log of a document |

### Trash

| Tool | Description |
|------|-------------|
| `papra_list_deleted_documents` | List deleted documents (trash) |
| `papra_restore_document` | Restore a document from the trash |
| `papra_delete_document_permanently` | Permanently delete one document (**destructive**) |
| `papra_empty_trash` | Permanently delete the whole trash (**destructive**) |

### Tags

| Tool | Description |
|------|-------------|
| `papra_list_tags` | List all tags in an organization |
| `papra_create_tag` | Create a tag with name, hex color, and optional description |
| `papra_update_tag` | Update a tag's name, color, or description |
| `papra_delete_tag` | Delete a tag (**destructive**) |
| `papra_add_tag_to_document` | Associate a tag with a document |
| `papra_remove_tag_from_document` | Remove a tag from a document |
| `papra_apply_tagging_rule` | Apply a tagging rule to all existing documents (background task) |

### Custom Properties

| Tool | Description |
|------|-------------|
| `papra_list_custom_properties` | List all custom property definitions |
| `papra_get_custom_property` | Get a custom property definition |
| `papra_create_custom_property` | Create a custom property definition |
| `papra_update_custom_property` | Update name, description, or options |
| `papra_delete_custom_property` | Delete a definition and its values (**destructive**) |
| `papra_list_document_custom_properties` | List the properties set on a document |
| `papra_set_document_custom_property` | Set a property value on a document |
| `papra_clear_document_custom_property` | Clear a property value from a document |

Custom properties support the types `text`, `number`, `date`, `boolean`, `select`, `multi_select`, `user_relation`, and `document_relation`. They need the matching `custom-properties:*` API key permissions.

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
