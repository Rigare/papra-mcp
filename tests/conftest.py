"""Shared fixtures for the papra MCP server tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import papra_mcp


@pytest.fixture
def mock_client():
    """Install a mock HTTP client for the duration of a test.

    Yields the mock so tests can assert on the outgoing request; the previous
    client is restored afterwards.
    """
    original = papra_mcp._client
    client = AsyncMock()
    papra_mcp._client = client
    try:
        yield client
    finally:
        papra_mcp._client = original


@pytest.fixture
def api_response():
    """Factory for mock responses used by papra_request-style JSON calls."""

    def _make(payload=None, status_code: int = 200) -> MagicMock:
        response = MagicMock()
        response.status_code = status_code
        response.content = b"{}" if payload is None else b'{"mocked": true}'
        response.json.return_value = {} if payload is None else payload
        return response

    return _make


@pytest.fixture
def max_content_bytes():
    """Temporarily override the content size limit."""
    original = papra_mcp._max_content_bytes

    def _set(value: int) -> None:
        papra_mcp._max_content_bytes = value

    try:
        yield _set
    finally:
        papra_mcp._max_content_bytes = original
