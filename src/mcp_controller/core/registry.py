"""Controller manifest and dependency registry models."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from mcp_controller.core.controllers import ControllerId
from mcp_controller.core.tags import CapabilityTag, ControllerTag


class DataSource(StrEnum):
    """Supported backend data sources."""

    PROMETHEUS = "prometheus"
    LOKI = "loki"
    GNMI = "gnmi"
    NETCONF = "netconf"
    KUBERNETES = "kubernetes"


class ControllerCapability(BaseModel):
    """A capability this controller exposes."""

    name: str = Field(description="Capability name: tool name or resource URI")
    kind: Literal["resource", "resource_template", "tool", "prompt"] = Field(
        description="MCP primitive type"
    )
    description: str = Field(description="Human-readable description")
    tags: list[CapabilityTag] = Field(default_factory=list, description="Searchable tags")


class CapabilityRef(BaseModel):
    """Reference to a specific capability in another MCP controller."""

    kind: Literal["resource", "resource_template", "tool", "prompt"] = Field(
        description="MCP primitive type being referenced"
    )
    name: str = Field(description="URI for resources, name for tools/prompts")


class ControllerDependency(BaseModel):
    """Dependency on another MCP controller, with fine-grained capability refs."""

    controller_id: ControllerId = Field(description="Target controller identifier")
    min_version: str = Field(description="Minimum compatible version (semver)")
    required: bool = Field(
        default=False, description="Hard dependency (fail) vs soft (degrade gracefully)"
    )
    reason: str = Field(description="Why this dependency exists")
    capability_refs: list[CapabilityRef] = Field(
        default_factory=list, description="Specific capabilities needed from the target controller"
    )


class ControllerManifest(BaseModel):
    """Manifest declaring a controller's identity, capabilities, and dependencies."""

    controller_id: ControllerId = Field(description="Unique controller identifier")
    version: str = Field(description="Controller version (semver)")
    display_name: str = Field(description="Human-readable name")
    network_function: str = Field(description="Canonical network function name")
    description: str = Field(description="What this controller provides")
    capabilities: list[ControllerCapability] = Field(
        default_factory=list, description="Exposed MCP capabilities"
    )
    dependencies: list[ControllerDependency] = Field(
        default_factory=list, description="Dependencies on other MCP controllers"
    )
    data_sources: list[DataSource] = Field(
        default_factory=list, description="Backend data sources used"
    )
    tags: list[ControllerTag] = Field(default_factory=list, description="Searchable tags")
