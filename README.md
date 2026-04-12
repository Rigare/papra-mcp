# Papra MCP Server (Python)

MCP server for the [Papra](https://papra.app) document management API.

## Setup

```bash
pip install -r requirements.txt
```

## Configure for Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "papra": {
      "command": "python",
      "args": ["C:/Users/marco/mcp/papra_mcp/papra_mcp.py"],
      "env": {
        "PAPRA_BASE_URL": "https://papra.partiri.net",
        "PAPRA_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PAPRA_BASE_URL` | Yes | Your Papra instance URL (e.g. `https://papra.example.com`) |
| `PAPRA_API_KEY` | Yes | API key from Papra account settings |

Both variables are validated at server startup. The server will fail with a clear error message if either is missing.

## Tools (21)

### API Key

| Tool | Description |
|------|-------------|
| `papra_check_api_key` | Check the current API key's ID, name, and permissions |

### Organizations

| Tool | Description |
|------|-------------|
| `papra_list_organizations` | List all accessible organizations |
| `papra_get_organization` | Get details of a specific organization |
| `papra_create_organization` | Create a new organization (name: 3–50 chars) |
| `papra_update_organization` | Update an organization's name |
| `papra_delete_organization` | Delete an organization (**destructive**) |

### Documents

| Tool | Description |
|------|-------------|
| `papra_list_documents` | List documents with pagination and optional search |
| `papra_list_deleted_documents` | List deleted documents (trash) |
| `papra_get_document` | Get a document's metadata |
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
