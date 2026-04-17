#!/usr/bin/env python3
"""Dry-run script for manual investigation of KubernetesClient outcomes.

Usage::

    # List all NetworkDeviceTargets in namespace "nok"
    uv run python scripts/kubernetes_dry-run.py --namespace nok list-devices

    # Get a single NetworkDeviceTarget by name
    uv run python scripts/kubernetes_dry-run.py --namespace nok get-device bng1

    # List with label selector
    uv run python scripts/kubernetes_dry-run.py --namespace nok list-devices -l "role=bng"

    # List all NetworkHostTargets
    uv run python scripts/kubernetes_dry-run.py --namespace nok list-hosts

    # Get a single NetworkHostTarget by name
    uv run python scripts/kubernetes_dry-run.py --namespace nok get-host iperf-server-1

    # Raw: list any CRD (group/version/plural)
    uv run python scripts/kubernetes_dry-run.py --namespace nok raw nok.dev v1alpha1 networkdevicetargets
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys

from mcp_controller.core.kubernetes_client import (
    KubernetesClient,
    KubernetesClientError,
    CRDDescriptor,
)


logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger("kubernetes_dry-run")


def _dump(obj: object) -> None:
    """Pretty-print a Pydantic model or raw dict to stdout."""
    if hasattr(obj, "model_dump"):
        print(json.dumps(obj.model_dump(by_alias=True), indent=2, default=str))
    else:
        print(json.dumps(obj, indent=2, default=str))


async def cmd_list_devices(k8s: KubernetesClient, args: argparse.Namespace) -> None:
    targets = await k8s.list_network_device_targets(
        namespace=args.namespace,
        label_selector=args.label,
    )
    logger.info("Got %d NetworkDeviceTarget(s)", len(targets))
    for t in targets:
        _dump(t)


async def cmd_list_devices_raw(k8s: KubernetesClient, args: argparse.Namespace) -> None:
    # api = await k8s._ensure_connected()
    descriptor = CRDDescriptor(
        group="nok.dev",
        version="v1alpha1",
        plural="networkdevicetargets",
        kind="NetworkDeviceTarget",
    )

    response: list | None = None
    try:
        response = await k8s.list_custom_objects(descriptor, namespace="nok-bng")
    except Exception as e:
        print(f"Error: {e}")

    if response is None:
        print("No response")
        return

    if type(response) is list:
        logger.info("Got %d NetworkDeviceTarget(s)", len(response))
        for t in response:
            _dump(t)
    else:
        _dump(response)


async def cmd_get_device(k8s: KubernetesClient, args: argparse.Namespace) -> None:
    target = await k8s.get_network_device_target(args.namespace)
    _dump(target)


async def cmd_list_namespaces(k8s: KubernetesClient, args: argparse.Namespace) -> None:
    _ = args  # unused
    namespaces = await k8s.list_namespaces()
    logger.info("Got %d namespaces", len(namespaces))
    for ns in namespaces:
        _dump(ns)


async def cmd_list_hosts(k8s: KubernetesClient, args: argparse.Namespace) -> None:
    targets = await k8s.list_network_host_targets(
        namespace=args.namespace,
        label_selector=args.label,
    )
    logger.info("Got %d NetworkHostTarget(s)", len(targets))
    for t in targets:
        _dump(t)


async def cmd_get_host(k8s: KubernetesClient, args: argparse.Namespace) -> None:
    target = await k8s.get_network_host_target(args.namespace)
    _dump(target)


async def cmd_raw(k8s: KubernetesClient, args: argparse.Namespace) -> None:
    descriptor = CRDDescriptor(
        group=args.group,
        version=args.version,
        plural=args.plural,
        kind=args.plural,
    )
    items = await k8s.list_custom_objects(
        descriptor,
        label_selector=args.label,
        field_selector=args.field,
    )
    logger.info("Got %d raw item(s)", len(items))
    for item in items:
        _dump(item)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run helper for KubernetesClient — poke CRDs interactively.",
    )
    parser.add_argument(
        "--namespace",
        "-n",
        default="",
        help="Kubernetes namespace (default: 'default').",
    )
    parser.add_argument(
        "--kubeconfig",
        "-k",
        default=None,
        help="Path to kubeconfig file (default: standard lookup).",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # list-devices
    p = sub.add_parser("list-devices", help="List NetworkDeviceTargets.")
    p.add_argument("-l", "--label", default=None, help="Label selector.")

    # list-devices-raw
    p = sub.add_parser("list-devices-raw", help="List NetworkDeviceTargets using raw API.")
    p.add_argument("-l", "--label", default=None, help="Label selector.")

    # get-device
    p = sub.add_parser("get-device", help="Get a single NetworkDeviceTarget.")
    p.add_argument("name", help="Resource name.")

    # list-hosts
    p = sub.add_parser("list-hosts", help="List NetworkHostTargets.")
    p.add_argument("-l", "--label", default=None, help="Label selector.")

    # get-host
    p = sub.add_parser("get-host", help="Get a single NetworkHostTarget.")
    p.add_argument("name", help="Resource name.")

    # list-namespaces
    p = sub.add_parser("list-namespaces", help="List all namespaces in the cluster.")

    # raw
    p = sub.add_parser("raw", help="List any CRD by group/version/plural.")
    p.add_argument("group", help="API group, e.g. 'nok.dev'.")
    p.add_argument("version", help="API version, e.g. 'v1alpha1'.")
    p.add_argument("plural", help="Plural resource name, e.g. 'networkdevicetargets'.")
    p.add_argument("-l", "--label", default=None, help="Label selector.")
    p.add_argument("-f", "--field", default=None, help="Field selector.")

    return parser


DISPATCH = {
    "list-devices": cmd_list_devices,
    "list-devices-raw": cmd_list_devices_raw,
    "get-device": cmd_get_device,
    "list-hosts": cmd_list_hosts,
    "get-host": cmd_get_host,
    "raw": cmd_raw,
    "list-namespaces": cmd_list_namespaces,
}


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    async with KubernetesClient(
        namespace=args.namespace,
        kubeconfig=args.kubeconfig,
    ) as k8s:
        try:
            await DISPATCH[args.command](k8s, args)
        except KubernetesClientError as exc:
            logger.error("%s: %s", type(exc).__name__, exc)
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
