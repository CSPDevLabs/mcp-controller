"""Kubernetes CRD base model.

This module is intentionally minimal — it exists solely so that
scripts/gen_k8s_types.py can pass it to datamodel-code-generator via
--base-class, causing every generated model to inherit _K8sModel instead
of plain BaseModel.

Do not add domain types here; those belong in k8s_types.py (generated).
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class _K8sModel(BaseModel):
    """Base for all nok.dev CRD Pydantic models.

    Configures three behaviours shared by every Kubernetes CR model:

    - alias_generator=to_camel   : maps snake_case Python attrs to the
                                   camelCase keys used in Kubernetes JSON
                                   (e.g. last_probe_time ↔ lastProbeTime).
    - populate_by_name=True      : allows construction using either the
                                   Python name or the camelCase alias.
    - extra="ignore"             : silently drops fields not present in
                                   the schema (ownerReferences, finalizers,
                                   managedFields, etc.) so partial API
                                   responses never raise validation errors.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )
