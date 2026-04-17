#!/usr/bin/env python3
"""Generate Pydantic v2 models from nok.dev CRD YAML manifests.

Reads CRD YAML files, extracts the openAPIV3Schema for each resource kind,
preprocesses it (injects ObjectMeta, hoists nested objects to named $defs,
promotes reachability to a shared enum, strips x-kubernetes-* extensions),
then delegates model generation to datamodel-code-generator.

All generated models inherit from _K8sModel (via --base-class), which
configures camelCase ↔ snake_case aliasing, populate_by_name, and
extra="ignore" automatically.

Class naming convention
-----------------------
datamodel-codegen uses the JSON Schema ``$defs`` key as the class name, so
every object schema is hoisted to a named ``$def`` during preprocessing:

    Level 0 — direct properties of a CR (spec, status, …):
        ``$defs["{Kind}{PropTitleCase}"]``  →  NetworkDeviceTargetSpec

    Level 1 — objects nested within spec/status:
        ``$defs["{PropTitleCase}"]``         →  Sdcio, Gnmic, Ssh

    Level ≥ 2 — deeper nested objects:
        ``$defs["{Parent}{PropTitleCase}"]`` →  SdcioSchema, SshJumpHost

    Shared across CRDs:
        ``$defs["Reachability"]``            →  Reachability (StrEnum)

Usage
-----
    # default CRD paths (requires NetOpsKube repo checked out alongside):
    python scripts/gen_k8s_types.py

    # explicit paths:
    python scripts/gen_k8s_types.py path/to/crd1.yaml path/to/crd2.yaml

    # preview the intermediate JSON Schema without running codegen:
    python scripts/gen_k8s_types.py --dry-run

Output: src/mcp_controller/core/k8s_types.py

Re-run this script whenever a CRD schema changes to keep models in sync.
Install dev dependencies first:  pip install -e ".[dev]"
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "src" / "mcp_controller" / "core" / "k8s_types.py"

# Default CRD source paths — assumes NetOpsKube repo lives next to this one.
# Override at the command line if your layout differs.
_NOK_BASE = ROOT.parent / "NetOpsKube" / "nok-kpt" / "nok-base" / "nok-controller"
DEFAULT_CRD_PATHS: list[Path] = [
    _NOK_BASE / "network-device-target-crd.yaml",
    _NOK_BASE / "network-host-target-crd.yaml",
]

# ---------------------------------------------------------------------------
# Schema building blocks injected during preprocessing
# ---------------------------------------------------------------------------

# Concrete ObjectMeta definition to replace the bare {type: object} that
# Kubernetes CRDs intentionally leave unspecified (ObjectMeta is a well-known
# server-side type and therefore not inlined in CRD schemas).
OBJECT_META_SCHEMA: dict = {
    "title": "ObjectMeta",
    "description": "Minimal Kubernetes object metadata.",
    "type": "object",
    "required": ["name"],
    # No additionalProperties constraint — inherits extra="ignore" from _K8sModel,
    # so system fields (managedFields, generation, etc.) are silently dropped.
    "properties": {
        "name": {"type": "string"},
        "namespace": {"type": "string", "default": ""},
        "labels": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "default": {},
        },
        "annotations": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "default": {},
        },
        "uid": {"type": "string", "default": ""},
        "resourceVersion": {"type": "string", "default": ""},
        "creationTimestamp": {"type": "string", "default": ""},
    },
}

# Shared Reachability enum across all nok.dev target CRs.
# Defined once in $defs so datamodel-codegen emits a single StrEnum class.
REACHABILITY_SCHEMA: dict = {
    "title": "Reachability",
    "description": "Aggregated reachability status for a nok.dev target CR.",
    "type": "string",
    "enum": ["Reachable", "Unreachable", "Unknown"],
}

# Core K8s Namespace (core/v1) — injected as a $def so we get a typed
# Pydantic model alongside the CRD models. Only surfaces fields useful for
# discovery flows: apiVersion, kind, metadata, and status.phase.
NAMESPACE_PHASE_SCHEMA: dict = {
    "title": "NamespacePhase",
    "description": "Kubernetes Namespace lifecycle phase.",
    "type": "string",
    "enum": ["Active", "Terminating"],
}

NAMESPACE_STATUS_SCHEMA: dict = {
    "title": "NamespaceStatus",
    "description": "Status of a Kubernetes core/v1 Namespace.",
    "type": "object",
    "properties": {
        "phase": {"$ref": "#/$defs/NamespacePhase"},
    },
}

NAMESPACE_SCHEMA: dict = {
    "title": "Namespace",
    "description": "Kubernetes core/v1 Namespace (minimal projection).",
    "type": "object",
    "properties": {
        "apiVersion": {"type": "string", "default": "v1"},
        "kind": {"type": "string", "default": "Namespace"},
        "metadata": {"$ref": "#/$defs/ObjectMeta"},
        "status": {"$ref": "#/$defs/NamespaceStatus"},
    },
}

# Seed $defs that are not generated from CRDs and should be excluded from
# the root oneOf unless they are top-level kinds. NamespaceStatus and
# NamespacePhase are sub-definitions; Namespace is a top-level kind and is
# included below via CORE_KINDS.
_NON_CR_DEFS: frozenset[str] = frozenset(
    {"ObjectMeta", "Reachability", "NamespaceStatus", "NamespacePhase"}
)
CORE_KINDS: list[str] = ["Namespace"]

# ---------------------------------------------------------------------------
# File header written into the generated output
# ---------------------------------------------------------------------------

FILE_HEADER = """\
# GENERATED FILE — do not edit by hand.
# Re-generate with:  python scripts/gen_k8s_types.py
#
# Source CRDs  (group nok.dev / version v1alpha1):
#   NetworkDeviceTarget   networkdevicetargets.nok.dev
#   NetworkHostTarget     networkhosttargets.nok.dev
#
# Core K8s types (injected as seed $defs, not from CRD YAML):
#   Namespace             core/v1
"""

# ---------------------------------------------------------------------------
# Schema extraction
# ---------------------------------------------------------------------------


def extract_schema(crd_path: Path) -> tuple[str, dict]:
    """Return (Kind, openAPIV3Schema) for the storage version of a CRD."""
    raw: dict = yaml.safe_load(crd_path.read_text())
    kind: str = raw["spec"]["names"]["kind"]
    versions: list[dict] = raw["spec"]["versions"]
    version = next(
        (v for v in versions if v.get("storage", False)),
        next(v for v in versions if v.get("served", True)),
    )
    schema: dict = version["schema"]["openAPIV3Schema"]
    return kind, schema


def _strip_k8s_extensions(node: object) -> None:
    """Recursively remove x-kubernetes-* keys (invalid in JSON Schema draft-07)."""
    if isinstance(node, dict):
        for key in list(node.keys()):
            if key.startswith("x-kubernetes-"):
                del node[key]
            else:
                _strip_k8s_extensions(node[key])
    elif isinstance(node, list):
        for item in node:
            _strip_k8s_extensions(item)


# ---------------------------------------------------------------------------
# Object hoisting — core of the naming strategy
# ---------------------------------------------------------------------------


def _prop_title(name: str) -> str:
    """TitleCase a camelCase or lowercase property name."""
    return name[0].upper() + name[1:]


def _hoist_objects(
    props: dict,
    parent_def_name: str,
    level: int,
    defs: dict,
) -> dict:
    """Replace inline object schemas with ``$ref``s, collecting hoisted defs.

    Modifies *defs* in-place.  Returns a new props dict with objects replaced
    by ``$ref`` pointers.

    Args:
        props:           Property map from the current schema object.
        parent_def_name: ``$defs`` key of the enclosing schema (used for naming).
        level:           Nesting depth (0 = direct CR properties).
        defs:            Accumulator for hoisted ``$defs`` entries.
    """
    result: dict = {}
    for prop_name, prop_schema in props.items():
        if not isinstance(prop_schema, dict) or "$ref" in prop_schema:
            result[prop_name] = prop_schema
            continue

        if prop_schema.get("type") == "object" and "properties" in prop_schema:
            prop_t = _prop_title(prop_name)
            if level == 0:
                # Direct CR property: prefix with Kind name.
                def_name = parent_def_name + prop_t  # NetworkDeviceTargetSpec
            elif level == 1:
                # Config block within spec/status: bare prop title.
                def_name = prop_t  # Sdcio, Gnmic, Ssh
            else:
                # Deeper: parent prefix + prop title.
                def_name = parent_def_name + prop_t  # SdcioSchema, SshJumpHost

            if def_name not in defs:
                # Recursively hoist objects nested inside this one first.
                inner_props = _hoist_objects(
                    prop_schema.get("properties", {}),
                    def_name,
                    level + 1,
                    defs,
                )
                defs[def_name] = {
                    **prop_schema,
                    "title": def_name,
                    "properties": inner_props,
                }

            result[prop_name] = {"$ref": f"#/$defs/{def_name}"}

        elif prop_name == "reachability" and prop_schema.get("type") == "string":
            # Promote reachability to the shared Reachability $def.
            result[prop_name] = {"$ref": "#/$defs/Reachability"}

        else:
            result[prop_name] = prop_schema

    return result


# ---------------------------------------------------------------------------
# Schema enrichment
# ---------------------------------------------------------------------------


def enrich_schema(kind: str, schema: dict, defs: dict) -> dict:
    """Preprocess a raw openAPIV3Schema for safe consumption by datamodel-codegen.

    Transformations applied (in order):
    1. Replace bare ``metadata: {type: object}`` with ``$ref: ObjectMeta``.
    2. Strip all ``x-kubernetes-*`` extensions (invalid JSON Schema).
    3. Hoist all nested object schemas to named ``$defs`` entries.
    """
    props: dict = schema.get("properties", {})

    # 1. Concrete metadata
    props["metadata"] = {"$ref": "#/$defs/ObjectMeta"}

    # 2. Strip extensions before hoisting (avoids polluting hoisted defs)
    _strip_k8s_extensions(schema)

    # 3. Hoist nested objects; level=0 means props are direct CR properties
    hoisted_props = _hoist_objects(props, kind, level=0, defs=defs)

    return {
        "title": kind,
        "type": "object",
        "required": schema.get("required", []),
        "properties": hoisted_props,
        # No additionalProperties constraint — inherits extra="ignore" from _K8sModel,
        # which is safer than "forbid" for real K8s API responses that may include
        # undocumented server-side fields.
    }


def build_json_schema(crd_paths: list[Path]) -> dict:
    """Combine CRD schemas into a single JSON Schema document.

    Nested objects are hoisted to named ``$defs`` so that datamodel-codegen
    uses meaningful class names instead of ``Spec``, ``Spec1``, ``Schema``.

    The root schema uses ``oneOf`` to reference all top-level CRs — this
    ensures every ``$def`` is reachable (and therefore generated) while
    avoiding the spurious ``Model`` wrapper class that a ``properties``-based
    root would produce.
    """
    # Seed with shared definitions available to all CRDs plus injected
    # core K8s types (Namespace and friends) that aren't sourced from CRD YAML.
    defs: dict = {
        "ObjectMeta": OBJECT_META_SCHEMA,
        "Reachability": REACHABILITY_SCHEMA,
        "NamespacePhase": NAMESPACE_PHASE_SCHEMA,
        "NamespaceStatus": NAMESPACE_STATUS_SCHEMA,
        "Namespace": NAMESPACE_SCHEMA,
    }

    for path in crd_paths:
        kind, raw_schema = extract_schema(path)
        defs[kind] = enrich_schema(kind, raw_schema, defs)

    # All top-level kinds included in the root oneOf: injected core K8s
    # kinds come first, followed by every def that isn't a shared helper.
    cr_kinds = CORE_KINDS + [k for k in defs if k not in _NON_CR_DEFS and k not in CORE_KINDS]
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        # oneOf ensures all $defs are generated without creating a
        # properties-based root that would produce a spurious Model class.
        "oneOf": [{"$ref": f"#/$defs/{kind}"} for kind in cr_kinds],
        "$defs": defs,
    }


# ---------------------------------------------------------------------------
# Code generation + post-processing
# ---------------------------------------------------------------------------


def run_codegen(schema: dict, output: Path) -> None:
    """Write *schema* to a temp file and invoke datamodel-code-generator."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        json.dump(schema, tmp, indent=2)
        tmp_path = Path(tmp.name)

    try:
        cmd = [
            sys.executable,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(tmp_path),
            "--input-file-type",
            "jsonschema",
            "--output",
            str(output),
            # Target runtime
            "--target-python-version",
            "3.12",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            # Inherit _K8sModel so aliasing / extra="ignore" apply everywhere.
            "--base-class",
            "mcp_controller.core._k8s_base._K8sModel",
            # Style: match project conventions
            "--snake-case-field",  # lastProbeTime → last_probe_time
            "--use-standard-collections",  # list[...] not List[...]
            "--use-union-operator",  # X | Y not Union[X, Y]
            "--collapse-root-models",  # collapse the oneOf root union
            "--field-constraints",  # emit Field() for min/max constraints
            "--use-annotated",  # Annotated[int, Ge(1)] not conint(ge=1)
            # Prepend the generated-file banner
            "--custom-file-header",
            FILE_HEADER,
        ]
        print("Running:", " ".join(cmd))
        subprocess.run(cmd, check=True)
    finally:
        tmp_path.unlink(missing_ok=True)

    _strip_root_model(output)


def _strip_root_model(output: Path) -> None:
    """Remove the spurious RootModel class that datamodel-codegen emits for
    the top-level schema.

    When all nested objects are hoisted to ``$defs``, datamodel-codegen
    synthesises a ``class Model(RootModel[A | B | ...])`` that unions every
    definition.  This carries no domain meaning and would confuse callers.
    Strip it — and clean up the now-unused ``RootModel`` import.
    """
    content = output.read_text()
    # Locate the Model class regardless of how many lines it spans.
    idx = content.rfind("\nclass Model(")
    if idx != -1:
        content = content[:idx].rstrip() + "\n"
    # Remove RootModel from the pydantic import line if it's now unused.
    if "RootModel[" not in content:
        content = re.sub(r",\s*RootModel\b", "", content)
        content = re.sub(r"\bRootModel,\s*", "", content)
    output.write_text(content)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "crd_paths",
        nargs="*",
        type=Path,
        default=DEFAULT_CRD_PATHS,
        metavar="CRD_YAML",
        help="Paths to CRD YAML files (default: nok.dev CRDs from NetOpsKube repo)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=OUTPUT,
        metavar="FILE",
        help=f"Destination file (default: {OUTPUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the intermediate JSON Schema without running codegen",
    )
    args = parser.parse_args()

    for p in args.crd_paths:
        if not p.exists():
            sys.exit(f"error: CRD file not found: {p}")

    schema = build_json_schema(args.crd_paths)

    if args.dry_run:
        print(json.dumps(schema, indent=2))
        return

    run_codegen(schema, args.output)
    print(f"Generated: {args.output}")


if __name__ == "__main__":
    main()
