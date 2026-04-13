# Papra MCP Server

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

An [MCP](https://modelcontextprotocol.io) (Model Context Protocol) server for the [Papra](https://papra.app) document management API. Gives AI assistants the ability to manage organizations, documents, and tags in your Papra instance.

## Prerequisites

- Python 3.10+
- A running [Papra](https://github.com/papra-hq/papra) instance
- An API key from your Papra account settings

## Installation

```bash
git clone https://github.com/Rigare/papra-mcp.git
cd papra-mcp
pip install -r requirements.txt
```

## Configuration

The server requires two environment variables:

| Variable | Description |
|----------|-------------|
| `PAPRA_BASE_URL` | Your Papra instance URL (e.g. `https://papra.example.com`) |
| `PAPRA_API_KEY` | API key from your Papra account settings |

Both are validated at startup. The server will exit with a clear error if either is missing.

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

Use the MCP inspector to test tools interactively:

```bash
mcp dev papra_mcp.py
```

## Tools (22)

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
| `papra_list_documents` | List documents with pagination and optional search |
| `papra_list_deleted_documents` | List deleted documents (trash) |
| `papra_get_document` | Get a document's metadata |
| `papra_get_document_content` | Get the file content of a document (text returned directly, PDFs extracted as plain text, other binary as base64) |
| `papra_search_documents` | Search documents (supports `name:`, `content:`, `tag:`, `created:`, `AND`, `OR`, `NOT`) |
| `papra_get_document_statistics` | Get document count and total size for an organization |
| `papra_update_document` | Update a document's name or content |
| `papra_delete_document` | Soft-delete a document (moves to trash) |
| `papra_get_document_activity` | Get the activity log of a document |

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

## PDF text extraction

When a document has the content type `application/pdf`, the server automatically extracts the text from the PDF using [PyMuPDF](https://pymupdf.readthedocs.io/) and returns it as plain text. This allows LLMs to read, search, and summarize PDF content directly without needing to decode binary data.

If text extraction fails (e.g. scanned documents without OCR, corrupted files, or image-only PDFs), the server falls back to returning the raw content as base64-encoded JSON — the same format used for other binary types like images or archives.

## License

[Apache-2.0](LICENSE)
