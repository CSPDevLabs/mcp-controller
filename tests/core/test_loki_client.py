"""Tests for Loki async HTTP client."""

import pytest
import respx
from httpx import Response

from mcp_controller.core.loki_client import LokiClient


@pytest.fixture
def loki():
    return LokiClient("http://localhost:3100")


@pytest.fixture
def mock_streams_response():
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"job": "syslog", "source": "bng1"},
                    "values": [
                        ["1711234567000000000", "Mar 23 CPU threshold exceeded"],
                        ["1711234568000000000", "Mar 23 SRRP state change to master"],
                    ],
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_instant_query(loki, mock_streams_response):
    """Should parse a streams instant query response."""
    with respx.mock:
        respx.get("http://localhost:3100/loki/api/v1/query").mock(
            return_value=Response(200, json=mock_streams_response)
        )
        result = await loki.query('{job="syslog"}')

    assert result.query == '{job="syslog"}'
    assert len(result.entries) == 2
    assert result.entries[0].labels["source"] == "bng1"
    assert "CPU threshold" in result.entries[0].line

    await loki.close()


@pytest.mark.asyncio
async def test_range_query(loki, mock_streams_response):
    """Should parse a streams range query response."""
    with respx.mock:
        respx.get("http://localhost:3100/loki/api/v1/query_range").mock(
            return_value=Response(200, json=mock_streams_response)
        )
        result = await loki.query_range(
            '{job="syslog"}',
            start="2024-03-23T00:00:00Z",
            end="2024-03-23T23:59:59Z",
        )

    assert len(result.entries) == 2

    await loki.close()


@pytest.mark.asyncio
async def test_query_error(loki):
    """Should raise RuntimeError on Loki error response."""
    error_response = {"status": "error", "error": "parse error"}
    with respx.mock:
        respx.get("http://localhost:3100/loki/api/v1/query").mock(
            return_value=Response(200, json=error_response)
        )
        with pytest.raises(RuntimeError, match="parse error"):
            await loki.query("invalid")

    await loki.close()


@pytest.mark.asyncio
async def test_empty_result(loki):
    """Should handle empty log result."""
    empty_response = {
        "status": "success",
        "data": {"resultType": "streams", "result": []},
    }
    with respx.mock:
        respx.get("http://localhost:3100/loki/api/v1/query").mock(
            return_value=Response(200, json=empty_response)
        )
        result = await loki.query('{job="nonexistent"}')

    assert result.entries == []

    await loki.close()
