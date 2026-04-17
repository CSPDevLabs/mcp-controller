# GENERATED FILE — do not edit by hand.
# Re-generate with:  python scripts/gen_k8s_types.py
#
# Source CRDs  (group nok.dev / version v1alpha1):
#   NetworkDeviceTarget   networkdevicetargets.nok.dev
#   NetworkHostTarget     networkhosttargets.nok.dev
#
# Core K8s types (injected as seed $defs, not from CRD YAML):
#   Namespace             core/v1

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from mcp_controller.core._k8s_base import _K8sModel
from pydantic import AwareDatetime, Field


class ObjectMeta(_K8sModel):
    name: str
    namespace: str | None = ''
    labels: dict[str, str] | None = {}
    annotations: dict[str, str] | None = {}
    uid: str | None = ''
    resource_version: Annotated[str | None, Field(alias='resourceVersion')] = ''
    creation_timestamp: Annotated[str | None, Field(alias='creationTimestamp')] = ''


class Reachability(StrEnum):
    reachable = 'Reachable'
    unreachable = 'Unreachable'
    unknown = 'Unknown'


class NamespacePhase(StrEnum):
    active = 'Active'
    terminating = 'Terminating'


class NamespaceStatus(_K8sModel):
    phase: NamespacePhase | None = None


class Namespace(_K8sModel):
    api_version: Annotated[str | None, Field(alias='apiVersion')] = 'v1'
    kind: str | None = 'Namespace'
    metadata: ObjectMeta | None = None
    status: NamespaceStatus | None = None


class SdcioSchema(_K8sModel):
    provider: Annotated[str, Field(description='The schema provider name.')]
    version: Annotated[str, Field(description='The schema version.')]


class Sdcio(_K8sModel):
    enabled: Annotated[
        bool | None,
        Field(description='Set to false if this device should not be managed by SDCIO.'),
    ] = True
    credentials_secret_ref: Annotated[
        str | None,
        Field(
            alias='credentialsSecretRef',
            description='Name of the Kubernetes Secret for SDCIO credentials.',
        ),
    ] = None
    connection_profile_ref: Annotated[
        str | None,
        Field(
            alias='connectionProfileRef', description='Name of the SDCIO TargetConnectionProfile.'
        ),
    ] = None
    sync_profile_ref: Annotated[
        str | None,
        Field(alias='syncProfileRef', description='Name of the SDCIO TargetSyncProfile.'),
    ] = None
    schema_: Annotated[SdcioSchema | None, Field(alias='schema')] = None
    labels: Annotated[
        dict[str, str] | None,
        Field(description='Labels specific to SDCIO targets generated for this device.'),
    ] = None


class Gnmic(_K8sModel):
    enabled: Annotated[
        bool | None,
        Field(description='Set to false if this device should not be managed by gnmic.'),
    ] = True
    credentials_secret_ref: Annotated[
        str | None,
        Field(
            alias='credentialsSecretRef',
            description='Name of the Kubernetes Secret for gnmic credentials.',
        ),
    ] = None
    target_profile_ref: Annotated[
        str | None, Field(alias='targetProfileRef', description='Name of the gnmic TargetProfile.')
    ] = None
    port: Annotated[
        str | None,
        Field(description='The specific port that gnmic should use to connect to this device.'),
    ] = None
    labels: Annotated[
        dict[str, str] | None,
        Field(description='Labels specific to gnmic targets generated for this device.'),
    ] = None


class NetworkDeviceTargetSpec(_K8sModel):
    address: Annotated[
        str, Field(description='The primary IP address or resolvable hostname for the device.')
    ]
    hostname: Annotated[str, Field(description='A user-friendly hostname for the device.')]
    common_labels: Annotated[
        dict[str, str] | None,
        Field(
            alias='commonLabels',
            description='Labels to be applied to generated SDCIO/gnmic targets.',
        ),
    ] = None
    sdcio: Sdcio | None = None
    gnmic: Gnmic | None = None


class NetworkDeviceTargetStatus(_K8sModel):
    reachability: Reachability | None = None
    last_probe_time: Annotated[
        AwareDatetime | None,
        Field(alias='lastProbeTime', description='Timestamp of the last gnmic reachability check.'),
    ] = None


class NetworkDeviceTarget(_K8sModel):
    api_version: Annotated[str | None, Field(alias='apiVersion')] = None
    kind: str | None = None
    metadata: ObjectMeta | None = None
    spec: NetworkDeviceTargetSpec | None = None
    status: NetworkDeviceTargetStatus | None = None


class SshJumpHost(_K8sModel):
    address: Annotated[str, Field(description='IP address or hostname of the jump host.')]
    port: Annotated[int | None, Field(description='SSH port of the jump host.', ge=1, le=65535)] = (
        22
    )
    credentials_secret_ref: Annotated[
        str | None,
        Field(
            alias='credentialsSecretRef',
            description='Name of the Kubernetes Secret containing jump host SSH credentials.',
        ),
    ] = None


class Ssh(_K8sModel):
    port: Annotated[int | None, Field(description='SSH port number.', ge=1, le=65535)] = 22
    credentials_secret_ref: Annotated[
        str | None,
        Field(
            alias='credentialsSecretRef',
            description='Name of the Kubernetes Secret containing SSH username and password.',
        ),
    ] = None
    key_secret_ref: Annotated[
        str | None,
        Field(
            alias='keySecretRef',
            description='Name of the Kubernetes Secret containing the SSH private key.',
        ),
    ] = None
    host_key_validation: Annotated[
        bool | None,
        Field(
            alias='hostKeyValidation',
            description='Whether to verify the remote host key against known hosts.',
        ),
    ] = False
    connection_timeout: Annotated[
        int | None,
        Field(alias='connectionTimeout', description='SSH connection timeout in seconds.', ge=1),
    ] = 30
    jump_host: Annotated[SshJumpHost | None, Field(alias='jumpHost')] = None


class NetworkHostTargetSpec(_K8sModel):
    address: Annotated[
        str, Field(description='The primary IP address or resolvable hostname for the host.')
    ]
    hostname: Annotated[str, Field(description='A user-friendly hostname for the host.')]
    common_labels: Annotated[
        dict[str, str] | None,
        Field(
            alias='commonLabels',
            description='Labels to be applied to generated resources for this host.',
        ),
    ] = None
    ssh: Ssh | None = None


class NetworkHostTargetStatus(_K8sModel):
    reachability: Reachability | None = None
    last_probe_time: Annotated[
        AwareDatetime | None,
        Field(alias='lastProbeTime', description='Timestamp of the last SSH reachability check.'),
    ] = None


class NetworkHostTarget(_K8sModel):
    api_version: Annotated[str | None, Field(alias='apiVersion')] = None
    kind: str | None = None
    metadata: ObjectMeta | None = None
    spec: NetworkHostTargetSpec | None = None
    status: NetworkHostTargetStatus | None = None
