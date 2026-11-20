"""The server must report its own package version, not the mcp library's."""

from importlib.metadata import version

import papra_mcp


def test_server_info_reports_package_version():
    options = papra_mcp.mcp._mcp_server.create_initialization_options()
    assert options.server_version == version("papra-mcp")
    assert options.server_version != version("mcp")
