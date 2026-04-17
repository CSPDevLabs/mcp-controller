"""WIP!!!! Loki HTTP API client."""

import logging
from datetime import datetime

import httpx

from mcp_controller.core.types import LogEntry, LogResult

logger = logging.getLogger(__name__)


class LokiClient:
    """Async client for Loki HTTP API."""

    def __init__(self, base_url: str, timeout: float = 30.0, verify: bool = True):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify = verify
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                verify=self.verify,
            )
        return self._client

    async def close(self):
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def query(self, logql: str, limit: int = 100) -> LogResult:
        """Execute an instant query against Loki.

        Args:
            logql: LogQL expression.
            limit: Maximum number of entries to return.
        """
        client = await self._get_client()
        params = {"query": logql, "limit": str(limit)}

        response = await client.get("/loki/api/v1/query", params=params)
        response.raise_for_status()
        body = response.json()

        if body.get("status") != "success":
            error = body.get("error", "unknown error")
            raise RuntimeError(f"Loki query failed: {error}")

        return self._parse_result(logql, body["data"])

    async def query_range(
        self,
        logql: str,
        start: str | datetime,
        end: str | datetime,
        limit: int = 100,
    ) -> LogResult:
        """Execute a range query against Loki.

        Args:
            logql: LogQL expression.
            start: Start time (RFC3339 or Unix nanoseconds).
            end: End time (RFC3339 or Unix nanoseconds).
            limit: Maximum number of entries to return.
        """
        client = await self._get_client()
        params = {
            "query": logql,
            "start": self._format_time(start),
            "end": self._format_time(end),
            "limit": str(limit),
        }

        response = await client.get("/loki/api/v1/query_range", params=params)
        response.raise_for_status()
        body = response.json()

        if body.get("status") != "success":
            error = body.get("error", "unknown error")
            raise RuntimeError(f"Loki range query failed: {error}")

        return self._parse_result(logql, body["data"])

    @staticmethod
    def _format_time(t: str | datetime) -> str:
        if isinstance(t, datetime):
            return t.isoformat()
        return str(t)

    @staticmethod
    def _parse_result(query: str, data: dict) -> LogResult:
        entries: list[LogEntry] = []
        for stream in data.get("result", []):
            labels = stream.get("stream", {})
            for ts, line in stream.get("values", []):
                entries.append(LogEntry(timestamp=ts, labels=labels, line=line))
        return LogResult(query=query, entries=entries)
