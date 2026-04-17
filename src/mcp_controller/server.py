"""MCP Server factory for BNG controller."""

import logging

from mcp.server.fastmcp import FastMCP

from mcp_controller.bng.prompts import register_bng_prompts
from mcp_controller.bng.resources import register_bng_resources
from mcp_controller.bng.tools import register_bng_tools
from mcp_controller.config import Settings
from mcp_controller.bng.completions import register_bng_completions

logger = logging.getLogger(__name__)


def create_server(settings: Settings) -> FastMCP:
    """Create and configure the MCP server with BNG resources."""
    mcp = FastMCP(
        name="bng-mcp-controller",
        instructions=(
            "Nokia BNG Observability MCP Controller. "
            "Provides metrics, logs, health assessment, and engineering SME context "
            "for Nokia SROS Broadband Network Gateway operations. "
            "Start by calling the `read_bng_manifest` tool or the `bng-overview` "
            "prompt to discover available capabilities."
        ),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )

    if settings.mock:
        logger.warning("MOCK MODE: serving pre-recorded responses from tests/mocks/data/")
    elif settings.mock_data_record:
        logger.warning(
            "MOCK DATA RECORDING: recording handler responses to tests/mocks/data/"
        )

    register_bng_resources(mcp, settings)
    register_bng_tools(mcp, settings)
    register_bng_prompts(mcp, settings)
    register_bng_completions(mcp, settings)

    logger.info(
        "BNG MCP controller configured on %s:%d",
        settings.host,
        settings.port,
    )
    return mcp
