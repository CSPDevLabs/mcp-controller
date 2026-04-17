"""Tests for Prometheus async HTTP client."""

import pytest
import respx
from httpx import Response

from mcp_controller.core.prometheus_client import PrometheusClient, PrometheusClientError


@pytest.fixture
def prometheus():
    return PrometheusClient("http://localhost:9090")


@pytest.fixture
def mock_vector_response():
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"source": "bng1", "__name__": "cpu_usage"},
                    "value": [1711234567.123, "42.5"],
                }
            ],
        },
    }


@pytest.fixture
def mock_matrix_response():
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"source": "bng1"},
                    "values": [
                        [1711234500.0, "40.0"],
                        [1711234515.0, "42.5"],
                        [1711234530.0, "41.0"],
                    ],
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_instant_query(prometheus, mock_vector_response):
    """Should parse a vector instant query response."""
    with respx.mock:
        respx.post("http://localhost:9090/api/v1/query").mock(
            return_value=Response(200, json=mock_vector_response)
        )
        result = await prometheus.query("cpu_usage")

    assert result.query == "cpu_usage"
    assert result.result_type == "vector"
    assert len(result.series) == 1
    assert result.series[0].labels["source"] == "bng1"
    assert result.series[0].samples[0].value == 42.5

    await prometheus.close()


@pytest.mark.asyncio
async def test_range_query(prometheus, mock_matrix_response):
    """Should parse a matrix range query response."""
    with respx.mock:
        respx.post("http://localhost:9090/api/v1/query_range").mock(
            return_value=Response(200, json=mock_matrix_response)
        )
        result = await prometheus.query_range("cpu_usage", start="2024-03-23T00:00:00Z", end="now")

    assert result.result_type == "matrix"
    assert len(result.series[0].samples) == 3
    assert result.series[0].samples[1].value == 42.5

    await prometheus.close()


@pytest.mark.asyncio
async def test_query_error(prometheus):
    """Should raise PrometheusClientError on Prometheus error response."""
    error_response = {"status": "error", "error": "bad query"}
    with respx.mock:
        respx.post("http://localhost:9090/api/v1/query").mock(
            return_value=Response(200, json=error_response)
        )
        with pytest.raises(PrometheusClientError, match="bad query"):
            await prometheus.query("invalid{}")

    await prometheus.close()


@pytest.mark.asyncio
async def test_empty_result(prometheus):
    """Should handle empty result set."""
    empty_response = {
        "status": "success",
        "data": {"resultType": "vector", "result": []},
    }
    with respx.mock:
        respx.post("http://localhost:9090/api/v1/query").mock(
            return_value=Response(200, json=empty_response)
        )
        result = await prometheus.query("nonexistent_metric")

    assert result.series == []

    await prometheus.close()
