"""Tests for controller manifest and registry models."""

import json

from mcp_controller.core.controllers import ControllerId
from mcp_controller.core.registry import (
    CapabilityRef,
    ControllerCapability,
    ControllerDependency,
    ControllerManifest,
)


class TestControllerManifest:
    """Tests for Pydantic manifest models."""

    def test_minimal_manifest(self):
        """Should create a manifest with minimal required fields."""
        manifest = ControllerManifest(
            controller_id="bng",
            version="0.1.0",
            display_name="Nokia BNG",
            network_function="bng",
            description="BNG controller",
        )
        assert manifest.controller_id == ControllerId.BNG
        assert manifest.capabilities == []
        assert manifest.dependencies == []

    def test_manifest_with_capabilities(self):
        """Should create a manifest with capabilities."""
        cap = ControllerCapability(
            name="bng://manifest",
            kind="resource",
            description="Controller manifest",
            tags=["discovery"],
        )
        manifest = ControllerManifest(
            controller_id="bng",
            version="0.1.0",
            display_name="Nokia BNG",
            network_function="bng",
            description="BNG controller",
            capabilities=[cap],
        )
        assert len(manifest.capabilities) == 1
        assert manifest.capabilities[0].kind == "resource"

    def test_manifest_with_dependencies(self):
        """Should create a manifest with fine-grained dependencies."""
        dep = ControllerDependency(
            controller_id="bng",
            min_version="0.1.0",
            required=False,
            reason="example dependency for model-shape test",
            capability_refs=[
                CapabilityRef(kind="resource_template", name="bng://metrics/target/{source}"),
                CapabilityRef(kind="tool", name="get_bng_status"),
            ],
        )
        manifest = ControllerManifest(
            controller_id="bng",
            version="0.1.0",
            display_name="Nokia BNG",
            network_function="bng",
            description="BNG controller",
            dependencies=[dep],
        )
        assert len(manifest.dependencies[0].capability_refs) == 2
        assert manifest.dependencies[0].capability_refs[0].kind == "resource_template"

    def test_manifest_json_serialization(self):
        """Manifest should serialize to valid JSON."""
        manifest = ControllerManifest(
            controller_id="bng",
            version="0.1.0",
            display_name="Nokia BNG",
            network_function="bng",
            description="BNG controller",
            data_sources=["prometheus", "loki"],
            tags=["broadband", "sros"],
        )
        data = json.loads(manifest.model_dump_json())
        assert data["controller_id"] == "bng.mcp.controllers.nok.dev"
        assert data["data_sources"] == ["prometheus", "loki"]

    def test_manifest_json_schema(self):
        """Manifest should produce a valid JSON schema."""
        schema = ControllerManifest.model_json_schema()
        assert schema["type"] == "object"
        assert "controller_id" in schema["properties"]

    def test_capability_kind_validation(self):
        """Capability kind should be restricted to valid MCP primitives."""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="kind"):
            ControllerCapability(
                name="test",
                kind="invalid",
                description="test",
            )

    def test_capability_ref_kinds(self):
        """CapabilityRef should support all MCP primitive types."""
        for kind in ("resource", "resource_template", "tool", "prompt"):
            ref = CapabilityRef(kind=kind, name=f"test://{kind}")
            assert ref.kind == kind
