"""Tag enumerations for MCP controller capabilities and identities."""

from enum import StrEnum


class CapabilityTag(StrEnum):
    """Tags for classifying MCP capabilities."""

    MANIFEST = "manifest"
    DISCOVERY = "discovery"
    TARGETS = "targets"
    OBSERVABILITY = "observability"
    STATUS = "status"
    METRICS = "metrics"
    LOGS = "logs"
    RESOURCES = "resources"
    SUBSCRIBER_MGMT = "subscriber-mgmt"


class ControllerTag(StrEnum):
    """Tags for classifying MCP controllers by NF domain."""

    BROADBAND = "broadband"
    SUBSCRIBER_MGMT = "subscriber-mgmt"
    SROS = "sros"
    NOKIA = "nokia"
    BNG = "bng"
    KUBERNETES = "kubernetes"
