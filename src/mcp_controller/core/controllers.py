"""Registry of known MCP controller identifiers.

Convention: <nf>.mcp.controllers.nok.dev  (reverse-DNS, Kubernetes-style)
"""

from enum import StrEnum


class ControllerId(StrEnum):
    """Canonical identifiers for MCP controllers in the Nokia ecosystem."""

    BNG = "bng.mcp.controllers.nok.dev"

    @classmethod
    def _missing_(cls, value: object) -> "ControllerId | None":
        """Accept short aliases (e.g. `"bng"`) in addition to the canonical value."""
        if isinstance(value, str) and value == "bng":
            return cls.BNG
        return None
