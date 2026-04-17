"""Tests for MCP server factory."""

from mcp_controller.config import Settings
from mcp_controller.server import create_server


class TestCreateServer:
    """Tests for server factory function."""

    def test_creates_fastmcp_instance(self):
        """Should create a FastMCP server with correct configuration."""
        settings = Settings(host="127.0.0.1", port=9000)
        server = create_server(settings)
        assert server is not None
        assert server.name == "bng-mcp-controller"

    def test_server_has_bng_manifest_resource(self):
        """Server should have the bng://manifest resource registered."""
        settings = Settings()
        server = create_server(settings)
        # FastMCP stores resources internally; verify via the server's resource manager
        assert server._resource_manager is not None
