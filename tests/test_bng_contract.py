"""Contract test: `BNG_MANIFEST` must exactly match what is registered on the server.

Guards against the manifest lying to the LLM — every capability declared in
`BNG_MANIFEST` must be backed by a real registration, and nothing may be
registered on the MCP server that isn't declared in the manifest.
"""

from mcp.server.fastmcp import FastMCP

from mcp_controller.bng.prompts import register_bng_prompts
from mcp_controller.bng.resources import BNG_MANIFEST, register_bng_resources
from mcp_controller.bng.tools import register_bng_tools
from mcp_controller.config import Settings


def _build_registered_server() -> FastMCP:
    """Register all BNG resources, tools, and prompts on a fresh server."""
    settings = Settings(k8s_namespace="test-ns")
    mcp = FastMCP(name="contract-test")
    register_bng_resources(mcp, settings)
    register_bng_tools(mcp, settings)
    register_bng_prompts(mcp, settings)
    return mcp


class TestBngManifestContract:
    """Every `BNG_MANIFEST` capability must be registered, and vice versa."""

    def test_manifest_capabilities_are_all_registered(self) -> None:
        mcp = _build_registered_server()

        registered = {
            "resource": set(mcp._resource_manager._resources.keys()),
            "resource_template": set(mcp._resource_manager._templates.keys()),
            "tool": set(mcp._tool_manager._tools.keys()),
            "prompt": set(mcp._prompt_manager._prompts.keys()),
        }

        for cap in BNG_MANIFEST.capabilities:
            assert cap.name in registered[cap.kind], (
                f"Manifest declares {cap.kind} '{cap.name}' but it is not registered "
                "on the MCP server"
            )

    def test_no_undeclared_capabilities_are_registered(self) -> None:
        mcp = _build_registered_server()

        declared: dict[str, set[str]] = {
            "resource": set(),
            "resource_template": set(),
            "tool": set(),
            "prompt": set(),
        }
        for cap in BNG_MANIFEST.capabilities:
            declared[cap.kind].add(cap.name)

        registered = {
            "resource": set(mcp._resource_manager._resources.keys()),
            "resource_template": set(mcp._resource_manager._templates.keys()),
            "tool": set(mcp._tool_manager._tools.keys()),
            "prompt": set(mcp._prompt_manager._prompts.keys()),
        }

        for kind, names in registered.items():
            undeclared = names - declared[kind]
            assert not undeclared, (
                f"{kind}(s) registered but not declared in BNG_MANIFEST: {undeclared}"
            )

    def test_manifest_capability_count_matches_registered_count(self) -> None:
        mcp = _build_registered_server()

        manifest_counts = {"resource": 0, "resource_template": 0, "tool": 0, "prompt": 0}
        for cap in BNG_MANIFEST.capabilities:
            manifest_counts[cap.kind] += 1

        assert manifest_counts["resource"] == len(mcp._resource_manager._resources)
        assert manifest_counts["resource_template"] == len(mcp._resource_manager._templates)
        assert manifest_counts["tool"] == len(mcp._tool_manager._tools)
        assert manifest_counts["prompt"] == len(mcp._prompt_manager._prompts)
