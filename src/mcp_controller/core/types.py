"""Shared types for MCP controller framework."""

from enum import Enum
import json

from pydantic import BaseModel, Field


class HealthStatus(str, Enum):
    """Health status levels for metric evaluation."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    UNKNOWN = "unknown"


class TimeRange(BaseModel):
    """Time range specification for queries."""

    start: str = Field(description="Start time (ISO 8601 or Prometheus duration like '1h')")
    end: str = Field(default="now", description="End time (ISO 8601 or 'now')")
    step: str = Field(default="15s", description="Query resolution step")


class MetricSample(BaseModel):
    """A single metric sample with timestamp and value."""

    timestamp: float
    value: float


class MetricScalar(BaseModel):
    """A scalar with timestamp and value."""

    timestamp: float
    value: float


class MetricString(BaseModel):
    """A string with timestamp."""

    timestamp: float
    value: str


class MetricSeries(BaseModel):
    """A time series with labels and samples."""

    labels: dict[str, str] = Field(default_factory=dict)
    samples: list[MetricSample] = Field(default_factory=list)


class MetricResult(BaseModel):
    """Result from a Prometheus query."""

    query: str
    result_type: str = Field(default="vector", description="'vector', 'matrix', or 'scalar'")
    series: list[MetricSeries] = Field(default_factory=list)


class LogEntry(BaseModel):
    """A single log entry from Loki."""

    timestamp: str
    labels: dict[str, str] = Field(default_factory=dict)
    line: str


class LogResult(BaseModel):
    """Result from a Loki query."""

    query: str
    entries: list[LogEntry] = Field(default_factory=list)


class MCPErrorResponse(Exception):
    """Base structured error response for all MCP resource and tool handlers.

    Inherits from `Exception` so it can be **raised** from tool
    handlers.  FastMCP catches the exception and produces a
    ``CallToolResult(isError=True)`` with the JSON payload in a
    ``TextContent`` block — setting the MCP protocol-level error flag
    automatically.

    Every NF controller should subclass this and add domain-specific
    optional fields (e.g. `uri`, `device`).

    The ``__str__`` override ensures the exception message is the full
    JSON payload (with ``exclude_none=True``), so FastMCP's
    ``ToolError`` wrapper preserves the structured information.
    """

    def __init__(self, error: str, detail: str, **kwargs):  # noqa: B042
        self.error = error
        self.detail = detail
        for k, v in kwargs.items():
            setattr(self, k, v)
        super().__init__(self.model_dump_json(exclude_none=True, indent=2))

    def model_dump(self, mode: str = "json", exclude_none: bool = False, **kwargs) -> dict:
        """Return a dictionary representation of the error."""
        d = {"error": self.error, "detail": self.detail}
        for k, v in self.__dict__.items():
            if k not in ["error", "detail"] and not k.startswith("_"):
                d[k] = v
        if exclude_none:
            d = {k: v for k, v in d.items() if v is not None}
        return d

    def model_dump_json(
        self, exclude_none: bool = False, indent: int | None = None, **kwargs
    ) -> str:
        """Return a JSON string representation of the error."""
        return json.dumps(self.model_dump(exclude_none=exclude_none), indent=indent)

    def __str__(self) -> str:
        """Return the structured JSON payload for use in exception messages."""
        return self.model_dump_json(exclude_none=True, indent=2)


class ErrorResponse(MCPErrorResponse):
    """Structured error response for MCP resource and tool handlers.

    Extends the base `MCPErrorResponse` with optional context fields
    commonly needed by network-function controllers.  Serialize with
    ``model_dump_json(exclude_none=True, indent=2)``.
    """

    def __init__(
        self,
        error: str,
        detail: str,
        uri: str | None = None,
        device: str | None = None,
        interval: str | None = None,
    ):
        super().__init__(
            error=error,
            detail=detail,
            uri=uri,
            device=device,
            interval=interval,
        )
