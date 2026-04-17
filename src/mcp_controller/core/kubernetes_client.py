"""Async Kubernetes client for nok.dev Custom Resource Definitions.

Architecture
------------
`KubernetesClient` is a thin async wrapper around `kubernetes_asyncio`
that exposes typed accessors for nok.dev and not only CRs.
It implements lazy-init / explicit-close lifecycle.

Extensibility (not fully tested!!!!)
-------------
Adding support for a new CRD requires three steps:

1. Run `python scripts/gen_k8s_types.py` (or add the CRD path as an arg)
   to update `k8s_types.py` with the new Pydantic models.

2. Add a `CRDDescriptor` constant below (group, version, plural, kind).

3. Add a typed `list_*` / `get_*` or whatever necessary method that calls the generic
   `list_custom_objects` / `get_custom_object` and parses into the
   new model via `Model.model_validate(raw)`.

The generic methods stay unchanged — all CRD-specific knowledge lives in
the descriptor constant and the two typed methods.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from kubernetes_asyncio import config
from kubernetes_asyncio.client import ApiClient
from kubernetes_asyncio.client.api.core_v1_api import CoreV1Api
from kubernetes_asyncio.client.api.custom_objects_api import CustomObjectsApi
from kubernetes_asyncio.client.rest import ApiException


from mcp_controller.core.k8s_types import (
    Namespace,
    NetworkDeviceTarget,
    NetworkHostTarget,
)


logger = logging.getLogger(__name__)

# Generic and interested CRDs
# -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CRDDescriptor:
    """Describes a Kubernetes Custom Resource Definition.

    All fields map directly to the arguments expected by
    `CustomObjectsApi.list_namespaced_custom_object` and friends.
    Adding a new CRD is a matter of declaring a new constant here.
    """

    group: str
    version: str
    plural: str
    kind: str  # used only for log messages and error strings


NETWORK_DEVICE_TARGET = CRDDescriptor(
    group="nok.dev",
    version="v1alpha1",
    plural="networkdevicetargets",
    kind="NetworkDeviceTarget",
)

NETWORK_HOST_TARGET = CRDDescriptor(
    group="nok.dev",
    version="v1alpha1",
    plural="networkhosttargets",
    kind="NetworkHostTarget",
)


# Exceptions TODO: to review
# -------------------------------------------------------------------------------------------------


class KubernetesClientError(Exception):
    """Raised when the Kubernetes API returns an unexpected error."""


class KubernetesNotFoundError(KubernetesClientError):
    """Raised when the requested resource does not exist (HTTP 404)."""


class KubernetesSchemaError(KubernetesClientError):
    """Raised when an API response fails Pydantic validation (e.g. CRD version skew)."""


class KubernetesPermissionError(KubernetesClientError):
    """Raised when the request is denied by RBAC (HTTP 403)."""


class KubernetesBadRequestError(KubernetesClientError):
    """Raised when the API server rejects the request as malformed (HTTP 400)."""


class KubernetesNamespaceNotFoundError(KubernetesClientError):
    """Raised when the requested namespace does not exist in the cluster."""


# Kubernetes client
# -------------------------------------------------------------------------------------------------


class KubernetesClient:
    """Async client for Kubernetes Custom Resources.

    Supports both in-cluster (ServiceAccount token) and out-of-cluster
    (kubeconfig) authentication — detected automatically at first use.

    Usage::

        async with KubernetesClient(namespace="nok") as k8s:
            targets = await k8s.list_network_device_targets()

    Or with explicit lifecycle management (matches the existing client
    pattern used by `PrometheusClient` and `LokiClient`)::

        k8s = KubernetesClient(namespace="nok")
        targets = await k8s.list_network_device_targets()
        await k8s.close()
    """

    def __init__(
        self,
        namespace: str = "",
        kubeconfig: str | None = None,
    ) -> None:
        """
        Args:
            namespace:  Default namespace for all CRD queries.  Can be
                        overridden per-call via the `namespace` argument.
            kubeconfig: Path to a kubeconfig file.  `None` (default) uses
                        the standard lookup order: `KUBECONFIG` env var,
                        then `~/.kube/config`.  Ignored when running
                        in-cluster (ServiceAccount token is used instead).
        Returns:
            None
        """
        self.namespace = namespace
        self._kubeconfig = kubeconfig
        self._api_client: ApiClient | None = None
        self._custom_objects: CustomObjectsApi | None = None
        self._core_v1: CoreV1Api | None = None

    # Connection lifecycle methods
    async def _ensure_connected(self) -> None:
        """Lazily initialise the API client on first use.

        Tries in-cluster config first (ServiceAccount token injected by the
        kubelet); if that fails, falls back to kubeconfig for out-of-cluster
        development. This mirrors the standard pattern used by K8s controllers.

        Raises:
            KubernetesClientError: if neither in-cluster nor kubeconfig
                authentication succeeds, or if the API client objects
                cannot be created.
        """
        if self._custom_objects is not None:
            return

        # In cluster configuration
        try:
            config.load_incluster_config()
            logger.debug("kubernetes: loaded in-cluster config")
        except config.ConfigException:
            # Out of cluster configuration
            try:
                await config.load_kube_config(config_file=self._kubeconfig)
                logger.debug(
                    "kubernetes: loaded kubeconfig (path=%s)",
                    self._kubeconfig or "default",
                )
            except config.ConfigException as exc:
                raise KubernetesClientError(
                    "Unable to configure Kubernetes client — neither in-cluster "
                    "config nor kubeconfig is available"
                ) from exc

        try:
            api_client = ApiClient()
            custom_objects = CustomObjectsApi(api_client)
            core_v1 = CoreV1Api(api_client)
        except Exception as exc:
            raise KubernetesClientError(
                f"Failed to initialise Kubernetes API client: {exc}"
            ) from exc

        self._api_client = api_client
        self._custom_objects = custom_objects
        self._core_v1 = core_v1

    async def _custom_objects_api(self) -> CustomObjectsApi:
        """Return the initialised `CustomObjectsApi`, connecting if needed.

        Returns:
            `CustomObjectsApi` instance guaranteed to be non-`None`.

        Raises:
            KubernetesClientError: if the client failed to initialise.
        """
        await self._ensure_connected()
        if self._custom_objects is None:
            raise KubernetesClientError("Kubernetes client not initialised")
        return self._custom_objects

    async def _core_v1_api(self) -> CoreV1Api:
        """Return the initialised `CoreV1Api`, connecting if needed.

        Returns:
            `CoreV1Api` instance guaranteed to be non-`None`.

        Raises:
            KubernetesClientError: if the client failed to initialise.
        """
        await self._ensure_connected()
        if self._core_v1 is None:
            raise KubernetesClientError("Kubernetes client not initialised")
        return self._core_v1

    async def _get_api_client(self) -> ApiClient:
        """Return the initialised underlying `ApiClient`, connecting if needed.

        Exposed so callers can reach `ApiClient` helpers like
        `sanitize_for_serialization` without going through
        `CoreV1Api.api_client` (which `kubernetes_asyncio` does not
        publish in its type stubs).

        Returns:
            `ApiClient` instance guaranteed to be non-`None`.

        Raises:
            KubernetesClientError: if the client failed to initialise.
        """
        await self._ensure_connected()
        if self._api_client is None:
            raise KubernetesClientError("Kubernetes client not initialised")
        return self._api_client

    async def close(self) -> None:
        """Release the underlying API client and connection pool.

        Args:
            None

        Returns:
            None

        Raises:
            None

        """
        if self._api_client is not None:
            await self._api_client.close()
            self._api_client = None
            self._custom_objects = None
            self._core_v1 = None
            logger.debug("kubernetes: client closed")

    async def __aenter__(self) -> KubernetesClient:
        """Does no connection work — it just returns `self`.

        Returns:
            KubernetesClient: KubernetesClient instance.

        Raises:
            None
        """
        return self

    async def __aexit__(self, *_: object) -> None:
        """We are doing cleanup no exception handling here.

        Args:
            object: collects `(exc_type, exc_value, traceback)` into a tuple
                and discards them, since no exception handling.

        Returns:
            None

        Raises:
            None, because exception re-raises normally.
        """
        await self.close()

    # Namespace validation
    # -------------------------------------------------------------------------------------------------

    async def list_namespaces(self) -> list[Namespace]:
        """Return all namespaces in the cluster as validated Pydantic models.

        Uses `CoreV1Api.list_namespace()`, which returns typed
        `V1Namespace` objects. Each item is converted to the
        Kubernetes JSON shape (camelCase keys, ISO-formatted
        timestamps) via `ApiClient.sanitize_for_serialization` and
        validated into the `Namespace` Pydantic model.

        Returns:
            List of `Namespace` models in the order returned by the API server.

        Raises:
            KubernetesPermissionError: on HTTP 403 (RBAC issue — the
                ServiceAccount lacks `list` on `namespaces`).
            KubernetesClientError: on any other API error.
            KubernetesSchemaError: if the response fails Pydantic validation.
        """
        core_v1 = await self._core_v1_api()
        api_client = await self._get_api_client()

        try:
            response = await core_v1.list_namespace()
        except ApiException as exc:
            if exc.status == 403:
                raise KubernetesPermissionError(
                    "Permission denied listing namespaces — "
                    "check ServiceAccount RBAC rules"
                ) from exc
            raise KubernetesClientError(
                f"Failed to list namespaces: HTTP {exc.status} {exc.reason}"
            ) from exc

        try:
            return [
                Namespace.model_validate(api_client.sanitize_for_serialization(item))
                for item in response.items
            ]
        except ValidationError as exc:
            raise KubernetesSchemaError(f"Failed to validate Namespace: {exc}") from exc

    async def check_namespace_exists(self, namespace: str) -> None:
        """Verify that `namespace` exists in the cluster.

        Args:
            namespace: Kubernetes namespace name to check.

        Raises:
            KubernetesNamespaceNotFoundError: if the namespace does not exist.
            KubernetesPermissionError: on HTTP 403 (RBAC issue).
            KubernetesClientError: on any other API error.
        """
        core_v1 = await self._core_v1_api()

        try:
            await core_v1.read_namespace(name=namespace)
        except ApiException as exc:
            if exc.status == 404:
                raise KubernetesNamespaceNotFoundError(
                    f"Namespace '{namespace}' does not exist in the cluster"
                ) from exc
            if exc.status == 403:
                raise KubernetesPermissionError(
                    f"Permission denied reading namespace '{namespace}' — "
                    f"check ServiceAccount RBAC rules"
                ) from exc
            raise KubernetesClientError(
                f"Failed to read namespace '{namespace}': " f"HTTP {exc.status} {exc.reason}"
            ) from exc

    # Label verifier
    # -------------------------------------------------------------------------------------------------

    async def _validate_label_selector_via_apiserver(
        self, descriptor: CRDDescriptor, label_selector: str
    ) -> None:
        """Validate a label selector against the API server.

        Sends a cheap `limit=1` list call to let the API server parse
        the selector.  Raises on empty or syntactically invalid input.

        Args:
            descriptor: CRD to probe — the selector is validated against
                a real resource type so the API server does the parsing.
            label_selector: K8s label selector expression, e.g. `"role=bng"`.

        Returns:
            None

        Raises:
            KubernetesBadRequestError: if the selector is empty or the API
                server rejects it as malformed (HTTP 400).
        """
        if not label_selector or not label_selector.strip():
            raise KubernetesBadRequestError("label selector must not be empty")
        api = await self._custom_objects_api()
        try:
            # use a cheap list call against the real target resource
            await api.list_cluster_custom_object(
                group=descriptor.group,
                version=descriptor.version,
                plural=descriptor.plural,
                label_selector=label_selector,
                limit=1,
            )
        except ApiException as e:
            if e.status == 400:
                detail = e.reason
                if e.body:
                    try:
                        detail = json.loads(e.body).get("message", e.reason)
                    except (json.JSONDecodeError, TypeError):
                        detail = e.reason
                raise KubernetesBadRequestError(f"invalid label selector: {detail}") from e
            raise

    # Generic CRD lister
    # -------------------------------------------------------------------------------------------------

    async def list_custom_objects(
        self,
        descriptor: CRDDescriptor,
        *,
        namespace: str | None = None,
        label_selector: str | None = None,
        field_selector: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all objects of the given CRD type.

        Args:
            descriptor:     Which CRD to query.
            namespace:      Override the client's default namespace.
                            Pass `""` to list across all namespaces
                            (requires appropriate RBAC!!!!).
            label_selector: Standard K8s label selector expression,
                            e.g. `"role=bng,env=prod"`.
            field_selector: Standard K8s field selector expression,
                            e.g. `"metadata.name=bng1"`.

        Returns:
            Raw item dicts from the `items` list of the API response.
            Callers are responsible for validating into Pydantic models.

        Raises:
            KubernetesBadRequestError: if `label_selector` is empty or
                syntactically invalid (rejected by the API server).
            KubernetesNamespaceNotFoundError: if the resolved namespace does
                not exist in the cluster.
            KubernetesPermissionError: on HTTP 403 (RBAC issue).
            KubernetesClientError: on any other API error, including HTTP 404
                (in case the CRD not installed at all).
        """
        api = await self._custom_objects_api()
        ns = namespace if namespace is not None else self.namespace

        if ns:
            await self.check_namespace_exists(ns)

        kwargs: dict[str, str] = {}
        if label_selector:
            await self._validate_label_selector_via_apiserver(descriptor, label_selector)
            kwargs["label_selector"] = label_selector
        if field_selector:
            kwargs["field_selector"] = field_selector

        try:
            if ns:
                response = await api.list_namespaced_custom_object(
                    group=descriptor.group,
                    version=descriptor.version,
                    namespace=ns,
                    plural=descriptor.plural,
                    **kwargs,  # type: ignore [arg-type] # pyright: ignore [reportArgumentType]
                )
            else:
                response = await api.list_cluster_custom_object(
                    group=descriptor.group,
                    version=descriptor.version,
                    plural=descriptor.plural,
                    **kwargs,  # type: ignore [arg-type] # pyright: ignore [reportArgumentType]
                )
        except ApiException as exc:
            if exc.status == 403:
                raise KubernetesPermissionError(
                    f"Permission denied listing {descriptor.kind} in namespace '{ns}' — "
                    f"check ServiceAccount RBAC rules"
                ) from exc
            raise KubernetesClientError(
                f"Failed to list {descriptor.kind} in namespace '{ns}': "
                f"HTTP {exc.status} {exc.reason}"
            ) from exc

        return response.get("items", [])

    # Generic Custom Object retrieval
    # -------------------------------------------------------------------------------------------------

    async def get_custom_object(
        self,
        descriptor: CRDDescriptor,
        name: str,
        *,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a single custom object by name.

        Args:
            descriptor: Which CRD to query.
            name:       Object name.
            namespace:  Override the client's default namespace.

        Returns:
            Raw item dict from the API response.
            Callers are responsible for validating into Pydantic models.

        Raises:
            KubernetesNotFoundError: if the object does not exist of CRDs aren't installed.
            KubernetesClientError:   on any other API error.
        """
        api = await self._custom_objects_api()
        ns = namespace if namespace is not None else self.namespace

        try:
            return await api.get_namespaced_custom_object(
                group=descriptor.group,
                version=descriptor.version,
                namespace=ns,
                plural=descriptor.plural,
                name=name,
            )
        except ApiException as exc:
            if exc.status == 404:
                raise KubernetesNotFoundError(
                    f"{descriptor.kind} '{name}' not found in namespace '{ns}'"
                ) from exc
            raise KubernetesClientError(
                f"Failed to get {descriptor.kind} '{name}' in namespace '{ns}': "
                f"HTTP {exc.status} {exc.reason}"
            ) from exc

    # List and get NetworkDeviceTarget CRDs
    # -------------------------------------------------------------------------------------------------

    async def list_network_device_targets(
        self,
        *,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> list[NetworkDeviceTarget]:
        """Return all `NetworkDeviceTarget` CRs, optionally filtered by labels.

        Args:
            namespace:      Override default namespace.
            label_selector: K8s label selector, e.g. `"role=bng"`.

        Raises:
            KubernetesBadRequestError: if `label_selector` is empty or
                syntactically invalid.
            KubernetesSchemaError: if the response fails Pydantic validation.
        """
        items = await self.list_custom_objects(
            NETWORK_DEVICE_TARGET,
            namespace=namespace,
            label_selector=label_selector,
        )
        try:
            return [NetworkDeviceTarget.model_validate(item) for item in items]
        except ValidationError as exc:
            raise KubernetesSchemaError(f"Failed to validate NetworkDeviceTarget: {exc}") from exc

    async def get_network_device_target(
        self,
        name: str,
        *,
        namespace: str | None = None,
    ) -> NetworkDeviceTarget:
        """Return a single `NetworkDeviceTarget` CR by name.

        Raises:
            KubernetesNotFoundError: if the object does not exist.
            KubernetesSchemaError: if the response fails Pydantic validation.
        """
        raw = await self.get_custom_object(NETWORK_DEVICE_TARGET, name, namespace=namespace)
        try:
            return NetworkDeviceTarget.model_validate(raw)
        except ValidationError as exc:
            raise KubernetesSchemaError(f"Failed to validate NetworkDeviceTarget: {exc}") from exc

    # List and get NetworkHostTarget CRDs
    # -------------------------------------------------------------------------------------------------

    async def list_network_host_targets(
        self,
        *,
        namespace: str | None = None,
        label_selector: str | None = None,
    ) -> list[NetworkHostTarget]:
        """Return all `NetworkHostTarget` CRs, optionally filtered by labels.

        Args:
            namespace:      Override default namespace.
            label_selector: K8s label selector.

        Returns:
            List of `NetworkHostTarget` resources.

        Raises:
            KubernetesBadRequestError: if `label_selector` is empty or
                syntactically invalid.
            KubernetesSchemaError: if the response fails Pydantic validation.
        """
        items = await self.list_custom_objects(
            NETWORK_HOST_TARGET,
            namespace=namespace,
            label_selector=label_selector,
        )
        try:
            return [NetworkHostTarget.model_validate(item) for item in items]
        except ValidationError as exc:
            raise KubernetesSchemaError(f"Failed to validate NetworkHostTarget: {exc}") from exc

    async def get_network_host_target(
        self,
        name: str,
        *,
        namespace: str | None = None,
    ) -> NetworkHostTarget:
        """Return a single `NetworkHostTarget` CR by name.

        Raises:
            KubernetesNotFoundError: if the object does not exist.
            KubernetesSchemaError: if the response fails Pydantic validation.

        Returns:
            `NetworkHostTarget` resource.
        """
        raw = await self.get_custom_object(NETWORK_HOST_TARGET, name, namespace=namespace)
        try:
            return NetworkHostTarget.model_validate(raw)
        except ValidationError as exc:
            raise KubernetesSchemaError(f"Failed to validate NetworkHostTarget: {exc}") from exc
