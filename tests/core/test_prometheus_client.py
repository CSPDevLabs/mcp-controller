"""Tests for Prometheus async HTTP client."""

import pytest
import respx
from httpx import Response

from mcp_controller.core.prometheus_client import (
    PrometheusClient,
    PrometheusClientError,
    PrometheusHTTPError,
)

# Verbatim shape of a Prometheus PromQL rejection — an unquoted label matcher
# value, which is what makes the real server answer 400 rather than 200.
BAD_DATA_BODY = {
    "status": "error",
    "errorType": "bad_data",
    "error": (
        'invalid parameter "query": 1:73: parse error: unexpected number "60" '
        "in label matching, expected string"
    ),
}


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


@pytest.mark.asyncio
async def test_label_values_returns_sorted_metric_names(
    prometheus, raw_metric_names_response
):
    """Should return the `__name__` values sorted, from the fixture response."""
    with respx.mock:
        respx.get("http://localhost:9090/api/v1/label/__name__/values").mock(
            return_value=Response(200, json=raw_metric_names_response)
        )
        result = await prometheus.label_values("__name__")

    assert result == sorted(raw_metric_names_response["data"])
    assert "state_system_cpu_summary_usage_cpu_usage" in result
    assert "ALERTS" in result
    assert "up" in result

    await prometheus.close()


@pytest.mark.asyncio
async def test_label_values_uses_default_label_name(prometheus):
    """Default `label_name` argument should hit the `__name__` endpoint."""
    with respx.mock:
        route = respx.get("http://localhost:9090/api/v1/label/__name__/values").mock(
            return_value=Response(200, json={"status": "success", "data": ["up"]})
        )
        result = await prometheus.label_values()

    assert route.called
    assert result == ["up"]

    await prometheus.close()


@pytest.mark.asyncio
async def test_label_values_custom_label_name(prometheus):
    """A non-default `label_name` should hit the matching endpoint."""
    with respx.mock:
        respx.get("http://localhost:9090/api/v1/label/source/values").mock(
            return_value=Response(200, json={"status": "success", "data": ["nok-bng/bng1"]})
        )
        result = await prometheus.label_values("source")

    assert result == ["nok-bng/bng1"]

    await prometheus.close()


@pytest.mark.asyncio
async def test_label_values_error_response(prometheus):
    """Should raise `PrometheusClientError` on a Prometheus error body."""
    error_response = {"status": "error", "error": "bad label name"}
    with respx.mock:
        respx.get("http://localhost:9090/api/v1/label/__name__/values").mock(
            return_value=Response(200, json=error_response)
        )
        with pytest.raises(PrometheusClientError, match="bad label name"):
            await prometheus.label_values()

    await prometheus.close()


@pytest.mark.asyncio
async def test_range_query_400_surfaces_promql_parse_error(prometheus):
    """A 400 should carry Prometheus' own parse error, not the httpx status line."""
    with respx.mock:
        respx.post("http://localhost:9090/api/v1/query_range").mock(
            return_value=Response(400, json=BAD_DATA_BODY)
        )
        with pytest.raises(PrometheusHTTPError) as exc_info:
            await prometheus.query_range("cpu{a=60}", start="now-5m", end="now")

    exc = exc_info.value
    assert exc.status_code == 400
    assert exc.error_type == "bad_data"
    assert "parse error" in str(exc)
    assert "expected string" in str(exc)
    assert "range query" in str(exc)
    # The old message leaked httpx's generic text and an MDN link instead.
    assert "developer.mozilla.org" not in str(exc)

    await prometheus.close()


@pytest.mark.asyncio
async def test_instant_query_400_surfaces_promql_parse_error(prometheus):
    """The instant-query path should extract the error body the same way."""
    with respx.mock:
        respx.post("http://localhost:9090/api/v1/query").mock(
            return_value=Response(400, json=BAD_DATA_BODY)
        )
        with pytest.raises(PrometheusHTTPError) as exc_info:
            await prometheus.query("cpu{a=60}")

    assert exc_info.value.error_type == "bad_data"
    assert "instant query" in str(exc_info.value)

    await prometheus.close()


@pytest.mark.asyncio
async def test_http_error_without_prometheus_envelope(prometheus):
    """A non-JSON error body (proxy/ingress page) should still be reported."""
    with respx.mock:
        respx.post("http://localhost:9090/api/v1/query_range").mock(
            return_value=Response(502, text="<html>bad gateway</html>")
        )
        with pytest.raises(PrometheusHTTPError) as exc_info:
            await prometheus.query_range("up", start="now-5m", end="now")

    exc = exc_info.value
    assert exc.status_code == 502
    assert exc.error_type == ""
    assert "bad gateway" in str(exc)

    await prometheus.close()


@pytest.mark.asyncio
async def test_http_error_body_is_truncated(prometheus):
    """An oversized non-Prometheus error body must not flood the message."""
    with respx.mock:
        respx.post("http://localhost:9090/api/v1/query_range").mock(
            return_value=Response(500, text="x" * 5000)
        )
        with pytest.raises(PrometheusHTTPError) as exc_info:
            await prometheus.query_range("up", start="now-5m", end="now")

    assert len(str(exc_info.value)) < 700

    await prometheus.close()
