#!/usr/bin/env python3
"""Dry-run script to explore the PrometheusClient module.

Usage::

    # Default — hits localhost:9090
    uv run python scripts/prom_dry_run.py

    # Custom URL
    uv run python scripts/prom_dry_run.py --url http://prometheus.example.com:9090

    # Skip TLS verification
    uv run python scripts/prom_dry_run.py --url https://prometheus.example.com:9090 --skip-verify

    # Range query with custom window
    uv run python scripts/prom_dry_run.py --range --start 2024-01-01T00:00:00Z --end 2024-01-01T01:00:00Z
"""

import argparse
import asyncio
import logging
import sys
import json
from datetime import datetime, timedelta, timezone

from rich import print_json

from mcp_controller.core.prometheus_client import (
    PrometheusClient,
    PrometheusClientError,
    PrometheusConnectionError,
    PrometheusHTTPError,
    PrometheusTimeoutError,
)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger("prom_dry_run")

SAMPLE_DEVICE = "nok-bng/clab-sros-bngt-bng2"
SAMPLE_INTERVAL = "5m"

SAMPLE_QUERIES = [
    ("up", "Target health (up/down)"),
    # ("prometheus_build_info", "Prometheus build metadata"),
    # ("process_resident_memory_bytes", "Prometheus process RSS"),
    # ("state_router_interface_statistics_mpls_in_octets", "MPLS in"),
    # ("state_router_interface_statistics_mpls_out_octets", "MPLS out"),
    (
        f'(absent_over_time(state_system_cpu_summary_usage_cpu_time{{source="{SAMPLE_DEVICE}"}}'
        f"[{SAMPLE_INTERVAL}]) == 1) OR "
        f'(count by (source) (state_system_cpu_summary_usage_cpu_time{{source="{SAMPLE_DEVICE}"}}'
        f") * 0)",
        f"Device unavailability map ({SAMPLE_DEVICE}, {SAMPLE_INTERVAL})",
    ),
]


async def run_instant(client: PrometheusClient, promql: str, label: str) -> None:
    """Run a single instant query and print the result."""
    print("INSTANT QUERY START", "=" * 80)
    logger.info("Instant query: %s  (%s)", promql, label)
    result = await client.query(promql)
    if not result:
        return None
    logger.info("  result_type=%s  series_count=%d", result.result_type, len(result.series))
    print_json(json.dumps(result.model_dump()))
    print("VECTORS START", "=" * 80)
    for series in result.series:
        latest = series.samples[-1] if series.samples else None
        logger.info(
            "  labels=%s  value=%s",
            series.labels,
            latest.value if latest else "N/A",
        )
    print("VECTORS END", "=" * 80)
    print("INSTANT QUERY END", "=" * 80)


async def run_range(
    client: PrometheusClient,
    promql: str,
    label: str,
    start: str,
    end: str,
    step: str,
) -> None:
    """Run a single range query and print the result."""
    print("RANGE QUERY START", "=" * 80)
    logger.info("Range query: %s  (%s)  [%s → %s @ %s]", promql, label, start, end, step)
    result = await client.query_range(promql, start=start, end=end, step=step)
    logger.info("  result_type=%s  series_count=%d", result.result_type, len(result.series))
    print_json(json.dumps(result.model_dump()))
    print("Series START", "=" * 80)
    for series in result.series:
        logger.info("  labels=%s  samples=%d", series.labels, len(series.samples))
        if series.samples:
            first, last = series.samples[0], series.samples[-1]
            logger.info(
                "    first=(%s, %s)  last=(%s, %s)",
                datetime.fromtimestamp(first.timestamp, tz=timezone.utc).isoformat(),
                first.value,
                datetime.fromtimestamp(last.timestamp, tz=timezone.utc).isoformat(),
                last.value,
            )
    print("Series END", "=" * 80)
    print("RANGE QUERY END", "=" * 80)


async def run_device_unavailability_map(
    client: PrometheusClient,
    device: str,
    interval: str,
    start: str,
    end: str,
    step: str,
) -> None:
    """Run the device_unavailability_map range query and print the result."""
    print("DEVICE UNAVAILABILITY MAP START", "=" * 80)
    logger.info(
        "device_unavailability_map: device=%s interval=%s step=%s",
        device,
        interval,
        step,
    )
    promql = (
        f"(absent_over_time("
        f'state_system_cpu_summary_usage_cpu_time{{source="{device}"}}'
        f"[{interval}]) == 1) OR "
        f"(count by (source) ("
        f'state_system_cpu_summary_usage_cpu_time{{source="{device}"}}'
        f") * 0)"
    )
    logger.info("PromQL: %s", promql)

    result = await client.query_range(promql, start=start, end=end, step=step)

    if not result:
        logger.warning("No result returned")
        return

    # Count steps where the device was absent (value == 1).
    unavailable_steps = 0
    total_steps = 0
    if hasattr(result, "series"):
        for series in result.series:
            for sample in series.samples:
                total_steps += 1
                if sample.value == 1.0:
                    unavailable_steps += 1

    logger.info(
        "  unavailable_steps=%d / total_steps=%d",
        unavailable_steps,
        total_steps,
    )
    print_json(
        json.dumps(
            {
                "device": device,
                "interval": interval,
                "step": step,
                "unavailable_steps": unavailable_steps,
                "total_steps": total_steps,
                "result": result.model_dump() if result else None,
            }
        )
    )
    print("DEVICE UNAVAILABILITY MAP END", "=" * 80)


async def main(args: argparse.Namespace) -> None:
    client = PrometheusClient(
        base_url=args.url,
        timeout=args.timeout,
        verify=not args.skip_verify,
    )
    logger.info("PrometheusClient created — base_url=%s  verify=%s", client.base_url, client.verify)

    now = datetime.now(tz=timezone.utc)
    start_iso = (now - timedelta(minutes=args.start)).isoformat()
    end_iso = args.end if args.end else now.isoformat()

    try:
        for promql, label in SAMPLE_QUERIES:
            try:
                if args.range:
                    await run_range(client, promql, label, start_iso, end_iso, args.step)
                else:
                    await run_instant(client, promql, label)
            except PrometheusConnectionError as exc:
                logger.error("Connection failed: %s", exc)
                logger.error("Is Prometheus running at %s?", args.url)
                break
            except PrometheusTimeoutError as exc:
                logger.error("Timeout: %s", exc)
            except PrometheusHTTPError as exc:
                logger.error("HTTP %d: %s", exc.status_code, exc)
            except PrometheusClientError as exc:
                logger.error("Client error: %s", exc)

        # Run device_unavailability_map after sample queries
        try:
            await run_device_unavailability_map(
                client,
                device=SAMPLE_DEVICE,
                interval=SAMPLE_INTERVAL,
                start=start_iso,
                end=end_iso,
                step=args.step,
            )
        except PrometheusConnectionError as exc:
            logger.error("Connection failed: %s", exc)
        except PrometheusClientError as exc:
            logger.error("device_unavailability_map error: %s", exc)

        logger.info("Done.")
    finally:
        await client.close()
        logger.info("Client closed.")


def parse_args() -> argparse.Namespace:
    now = datetime.now(tz=timezone.utc)
    one_hour_ago = now - timedelta(hours=1)

    parser = argparse.ArgumentParser(description="Dry-run the PrometheusClient module.")
    parser.add_argument(
        "--url",
        default="http://localhost:9090",
        help="Prometheus base URL (default: http://localhost:9090)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Request timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Disable TLS certificate verification",
    )
    parser.add_argument(
        "--range",
        action="store_true",
        help="Run range queries instead of instant queries",
    )
    parser.add_argument(
        "--start1h",
        default=one_hour_ago.isoformat(),
        help="Range query start time (default: 1h ago)",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=5,
        help="Range query start: minutes ago (default: 5)",
    )

    parser.add_argument(
        "--end",
        default=None,
        help="Range query end time as ISO 8601 (default: now)",
    )
    parser.add_argument(
        "--step",
        default="60s",
        help="Range query step (default: 60s)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        logger.info("Interrupted.")
        sys.exit(130)
