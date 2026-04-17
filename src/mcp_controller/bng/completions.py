"""BNG MCP Prompt and Resource completion handlers."""

import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    PromptReference,
    ResourceTemplateReference,
)
from mcp_controller.core.kubernetes_client import KubernetesClient, KubernetesClientError
from mcp_controller.core.mock import mock_intercept

from mcp_controller.config import Settings

logger = logging.getLogger(__name__)

NAMESPACE_TEMPLATE_URIS = {
    "bng://targets/devices/{namespace}",
    "bng://targets/hosts/{namespace}",
}


def register_bng_completions(mcp: FastMCP, settings: Settings) -> None:
    """Register BNG completions on the MCP server.

    Args:
        mcp:      The FastMCP server instance.
        settings: Application settings (provides `k8s_namespace`).

    Returns:
        None
    """
    k8s = KubernetesClient(namespace=settings.k8s_namespace)

    # Completions for prompt arguments and resource template variables

    @mcp.completion()
    @mock_intercept(settings)
    async def handle_completion(
        ref: PromptReference | ResourceTemplateReference,
        argument: CompletionArgument,
        context: CompletionContext | None,
    ) -> Completion | None:
        """Provide completions for prompts and resources.

        Args:
            ref: The reference for which completion is requested (prompt or resource template).
            argument: The specific argument for which to provide completions.
            context: Additional context about the completion request.

        Returns:
            A Completion object with possible values, or None if no completions are available.

        Raises:
            KubernetesClientError: If there is an error communicating with the Kubernetes API.

        """

        # Complete programming languages for the prompt
        if isinstance(ref, PromptReference):
            return None

        # Complete repository names for GitHub resources
        if isinstance(ref, ResourceTemplateReference):
            if ref.uri in NAMESPACE_TEMPLATE_URIS and argument.name == "namespace":
                prefix = argument.value or ""
                try:
                    namespaces = await k8s.list_namespaces()
                except KubernetesClientError as exc:
                    logger.warning("namespace completion failed: %s", exc)
                    return Completion(values=[])  # return empty completions on error
                return Completion(
                    values=[
                        ns.metadata.name
                        for ns in namespaces
                        if ns.metadata and ns.metadata.name and ns.metadata.name.startswith(prefix)
                    ][
                        0:100
                    ],  # limit to 100 completions
                    total=len(namespaces),
                    hasMore=len(namespaces) > 100,
                )
            else:
                return Completion(
                    values=[]
                )  # no completions for other resource templates or arguments

        return Completion(values=[])  # default to no completions for other cases
