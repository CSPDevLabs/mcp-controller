"""BNG MCP Prompt definitions."""

import json
import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import PromptMessage, TextContent

from mcp_controller.bng.resources import BNG_MANIFEST
from mcp_controller.config import Settings

logger = logging.getLogger(__name__)


def register_bng_prompts(mcp: FastMCP, settings: Settings) -> None:
    """Register BNG prompts on the MCP server.

    Args:
        mcp:      The FastMCP server instance.
        settings: Application settings.

    Returns:
        None
    """

    @mcp.prompt(
        name="bng-overview",
        description=(
            "Return the BNG controller manifest and capability summary. "
            "Use this prompt at session start to discover what the BNG "
            "controller provides before calling any tools or resources."
        ),
    )
    async def bng_overview() -> list[PromptMessage]:
        """Provide the BNG manifest and grouped capabilities for session bootstrap."""
        logger.info("Generating bng-overview prompt")

        manifest_json = BNG_MANIFEST.model_dump_json(indent=2)

        grouped: dict[str, list[dict]] = {}
        for cap in BNG_MANIFEST.capabilities:
            grouped.setdefault(cap.kind, []).append(
                {
                    "name": cap.name,
                    "description": cap.description,
                    "tags": [str(t) for t in cap.tags],
                }
            )
        capabilities_json = json.dumps(grouped, indent=2)

        text = (
            "# BNG Controller — Session Context\n\n"
            "## Manifest\n"
            f"```json\n{manifest_json}\n```\n\n"
            "## Available Capabilities\n"
            f"```json\n{capabilities_json}\n```\n\n"
            "Use the capabilities above to decide which resources to read "
            "or tools to call. Metric tools (`bng_device_cpu_usage`, "
            "`bng_device_memory_usage`, `bng_device_unavailability_map`) "
            "accept an optional `start_time` (ISO 8601) for historical queries "
            "and return quantile statistics (p80, p95, p98) where applicable."
        )

        return [PromptMessage(role="user", content=TextContent(type="text", text=text))]
