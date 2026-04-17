"""WIP!!!! Prometheus HTTP API client."""

import logging
from datetime import datetime
import httpx

from mcp_controller.core.types import (
    MetricResult,
    MetricSample,
    MetricSeries,
    MetricScalar,
    MetricString,
)

logger = logging.getLogger(__name__)

# Exceptions
# -------------------------------------------------------------------------------------------------
# PrometheusClientError          (base — also raised for body-level failures and catch-all)
# ├── PrometheusConnectionError  (NetworkError: DNS, refused, unreachable, read/write/close)
# ├── PrometheusTimeoutError     (connect, read, write, pool timeout)
# └── PrometheusHTTPError        (4xx/5xx — carries .status_code)


class PrometheusClientError(Exception):
    """Raised when a Prometheus API request fails."""


class PrometheusConnectionError(PrometheusClientError):
    """Raised on network-level failures (DNS, refused, read/write errors, proxy)."""


class PrometheusTimeoutError(PrometheusClientError):
    """Raised when a Prometheus request exceeds the configured timeout."""


class PrometheusHTTPError(PrometheusClientError):
    """Raised when Prometheus returns a non-2xx HTTP status."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message, status_code)
        self.status_code = status_code


class PrometheusClient:
    """Async client for Prometheus HTTP API."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        limits: httpx.Limits | None = None,
        verify: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.limits = limits or httpx.Limits(
            max_connections=10,
            max_keepalive_connections=5,
            keepalive_expiry=30,
        )
        self.verify = verify
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Returns the underlying HTTP client. No connection.

        Args:
            None

        Returns:
            httpx.AsyncClient: An asynchronous HTTP client.
        """
        # debug logging
        logging.debug(
            "[%s] GETTING CLIENT",
            self._get_client.__name__,
        )

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                limits=self.limits,
                verify=self.verify,
            )
        return self._client

    async def close(self):
        """Close the underlying HTTP client.

        Args:
            None

        Returns:
            None
        """
        # debug logging
        logging.debug(
            "[%s] CLOSING CLIENT",
            self.close.__name__,
        )

        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def query(
        self, promql: str, time: str | None = None
    ) -> MetricResult | MetricScalar | MetricString | None:
        """Execute an instant query against Prometheus.

        Args:
            promql: PromQL expression.
            time: Evaluation timestamp (RFC3339 or Unix). Defaults to server time.

        Raises:
            PrometheusConnectionError: Cannot reach the Prometheus server.
            PrometheusTimeoutError: Request exceeded the configured timeout.
            PrometheusHTTPError: Server returned a non-2xx HTTP status.
            PrometheusClientError: Response body indicates query failure.
        """
        # debug logging
        logger.debug("[%s] EXECUTING INSTANT QUERY", self.query.__name__)
        logger.debug("[%s] QUERY: %s", self.query.__name__, promql)
        logger.debug("[%s] TIME: %s", self.query.__name__, time)
        logger.debug("[%s] VERIFY: %s", self.query.__name__, self.verify)

        # Getting client
        client = await self._get_client()
        # Making request to put in POST data
        params: dict[str, str] = {"query": promql}
        # time is optional
        if time:
            params["time"] = time

        try:
            response = await client.post("/api/v1/query", data=params)
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise PrometheusTimeoutError(f"Prometheus query timed out: {exc}") from exc
        except httpx.NetworkError as exc:
            raise PrometheusConnectionError(
                f"Cannot reach Prometheus at {self.base_url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise PrometheusHTTPError(
                f"Prometheus returned HTTP {exc.response.status_code}: {exc}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise PrometheusClientError(f"Prometheus request failed: {exc}") from exc
        except Exception as exc:
            raise PrometheusClientError(f"Prometheus returned non-JSON response: {exc}") from exc

        if body.get("status") != "success":
            error = body.get("error", "unknown error")
            raise PrometheusClientError(f"Prometheus query failed: {error}")

        return self._parse_result(promql, body["data"])

    async def query_range(
        self,
        promql: str,
        start: str | datetime,
        end: str | datetime,
        step: str = "15s",
    ) -> MetricResult | MetricScalar | MetricString | None:
        """Execute a range query against Prometheus.

        Args:
            promql: PromQL expression.
            start: Start time (RFC3339, Unix timestamp, or datetime).
            end: End time (RFC3339, Unix timestamp, or datetime).
            step: Query resolution step (e.g., '15s', '1m').

        Returns:
            MetricResult: Result of the prom query.

        Raises:
            PrometheusConnectionError: Cannot reach the Prometheus server.
            PrometheusTimeoutError: Request exceeded the configured timeout.
            PrometheusHTTPError: Server returned a non-2xx HTTP status.
            PrometheusClientError: Response body indicates query failure.
        """
        # debug logging
        logger.debug("[%s] EXECUTING RANGE QUERY", self.query_range.__name__)
        logger.debug("[%s] QUERY: %s", self.query_range.__name__, promql)
        logger.debug("[%s] START: %s", self.query_range.__name__, start)
        logger.debug("[%s] END: %s", self.query_range.__name__, end)
        logger.debug("[%s] STEP: %s", self.query_range.__name__, step)

        # Getting client
        client = await self._get_client()
        # Making request data for HTTP POST
        params = {
            "query": promql,
            "start": self._format_time(start),
            "end": self._format_time(end),
            "step": step,
        }

        try:
            response = await client.post("/api/v1/query_range", data=params)
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise PrometheusTimeoutError(f"Prometheus range query timed out: {exc}") from exc
        except httpx.NetworkError as exc:
            raise PrometheusConnectionError(
                f"Cannot reach Prometheus at {self.base_url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise PrometheusHTTPError(
                f"Prometheus returned HTTP {exc.response.status_code}: {exc}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise PrometheusClientError(f"Prometheus range request failed: {exc}") from exc
        except Exception as exc:
            raise PrometheusClientError(
                f"Prometheus returned non-JSON response: {exc}"
            ) from exc

        if body.get("status") != "success":
            error = body.get("error", "unknown error")
            raise PrometheusClientError(f"Prometheus range query failed: {error}")

        return self._parse_result(promql, body["data"])

    @staticmethod
    def _format_time(t: str | datetime) -> str:
        if isinstance(t, datetime):
            return t.isoformat()
        return str(t)

    @staticmethod
    def _parse_result(query: str, data: dict) -> MetricResult | MetricScalar | MetricString | None:
        result_type = data.get("resultType", "unknown")
        if result_type not in ["matrix", "vector", "scalar", "string"]:
            raise PrometheusClientError(f"Unexpected result type: {result_type}")

        raw_results = data.get("result", [])
        if raw_results is None:
            return None

        logger.debug("[_parse_result] QUERY: %s", query)
        logger.debug("[_parse_result] result_type: %s", result_type)

        match result_type:
            case "matrix":
                return MetricResult(
                    query=query,
                    result_type=result_type,
                    series=PrometheusClient._parse_matrix(raw_results),
                )
            case "vector":
                return MetricResult(
                    query=query,
                    result_type=result_type,
                    series=PrometheusClient._parse_vector(raw_results),
                )
            case "scalar":
                return PrometheusClient._parse_scalar(raw_results)
            case "string":
                return PrometheusClient._parse_string(raw_results)
            case _:
                raise PrometheusClientError(f"Unexpected result type: {result_type}")

    @staticmethod
    def _parse_matrix(raw_results: list[dict]) -> list[MetricSeries]:
        """Parse a Prometheus matrix result into a list of `MetricSeries`.

        Args:
            raw_results: List of result items from the Prometheus response.

        Returns:
            List of `MetricSeries`, one per time series.
        """
        series_list: list[MetricSeries] = []
        for item in raw_results:
            labels = {k: v for k, v in item.get("metric", {}).items()}
            samples = [
                MetricSample(timestamp=float(v[0]), value=float(v[1]))
                for v in item.get("values", [])
            ]
            series_list.append(MetricSeries(labels=labels, samples=samples))
        return series_list

    @staticmethod
    def _parse_vector(raw_results: list[dict]) -> list[MetricSeries]:
        """Parse a Prometheus vector result into a list of `MetricSeries`.

        Args:
            raw_results: List of result items from the Prometheus response.

        Returns:
            List of `MetricSeries`, one per instant vector.
        """
        series_list: list[MetricSeries] = []
        for item in raw_results:
            labels = {k: v for k, v in item.get("metric", {}).items()}
            val = item.get("value", [])
            samples = [MetricSample(timestamp=float(val[0]), value=float(val[1]))] if val else []
            series_list.append(MetricSeries(labels=labels, samples=samples))
        return series_list

    @staticmethod
    def _parse_scalar(result: list) -> MetricScalar | None:
        """Parse a Prometheus scalar result.

        Args:
            result: Flat array `[timestamp, "value"]` from the Prometheus response.

        Returns:
            Parsed `MetricScalar`, or `None` if the result is empty.
        """
        if not result:
            return None
        return MetricScalar(timestamp=result[0], value=float(result[1]))

    @staticmethod
    def _parse_string(result: list) -> MetricString | None:
        """Parse a Prometheus string result.

        Args:
            result: Flat array `[timestamp, "value"]` from the Prometheus response.

        Returns:
            Parsed `MetricString`, or `None` if the result is empty.
        """
        if not result:
            return None
        return MetricString(timestamp=result[0], value=str(result[1]))
