# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Any, Optional

from fastapi import status

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.aws_bedrock.bedrock_agent_service import BedrockAgentService
from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes, MCP_SERVERS_ASSISTANT_METRIC


def _get_assistant_by_slug_or_raise(assistant_slug: str, project: Optional[str] = None) -> Assistant:
    """
    Retrieves an assistant by slug or raises a standardized exception if not found.

    When ``project`` is provided the lookup is scoped to that project, which makes
    the result deterministic (slugs are unique per-project via ``uq_assistants_project_slug``).
    Without ``project``, raises HTTP 422 if the same slug exists in more than one project —
    callers must supply ``project`` to disambiguate.
    """
    fields: dict[str, Any] = {"slug.keyword": assistant_slug}
    if project is not None:
        fields["project.keyword"] = project

    matches = Assistant.get_all_by_fields(fields)

    if not matches:
        scope_hint = f" in project '{project}'" if project else ""
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Assistant not found",
            details=f"No assistant found with the slug '{assistant_slug}'{scope_hint}.",
            help="Please check the assistant slug and ensure it is correct. ",
        )

    if len(matches) > 1 and not project:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Ambiguous slug",
            details=(
                f"Slug '{assistant_slug}' matches {len(matches)} assistants across different projects. "
                "Add the 'project' query parameter to scope the lookup."
            ),
            help="Use GET /v1/assistants/slug/{slug}?project={project} to resolve a specific assistant.",
        )

    return matches[0]


def _validate_remote_entities_and_raise(assistant: Assistant):
    # Validate Bedrock Agents
    deleted_assistant_names = BedrockAgentService.validate_remote_entity_exists_and_cleanup_with_subassistants(
        assistant
    )

    # Validate Bedrock AgentCore Runtime endpoints
    if not deleted_assistant_names:  # Only check if not already deleted
        deleted_assistant_names = (
            BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup_with_subassistants(assistant)
        )

    if deleted_assistant_names:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Requested assistant or subassistants were not found on vendor, deleting from AI/Run.",
            details=f"We haven't found the assistant/s '{', '.join(deleted_assistant_names)}' on the vendor.",
            help="Make sure that the assistant exists on the vendor side and reimport.",
        )


def _track_assistant_management_metric(
    metric_name: str, assistant: Assistant, user: User, success: bool, additional_attributes: dict = None
):
    """
    Tracks metrics for assistant management operations.
    Safely extracts attributes from assistant object to avoid DetachedInstanceError in thread pool contexts.
    """
    # Extract attributes before passing to service to avoid DetachedInstanceError
    # This is necessary because FastAPI runs sync endpoints in thread pools via anyio,
    # and SQLAlchemy objects may be detached after the session closes.
    try:
        assistant_name = assistant.name
        assistant_description = assistant.description
        assistant_project = assistant.project
        assistant_slug = assistant.slug if assistant.slug is not None else assistant.id
        assistant_llm_model = assistant.llm_model_type
        assistant_ids_count = len(assistant.assistant_ids) if assistant.assistant_ids else 0
        mcp_servers = assistant.mcp_servers.copy() if assistant.mcp_servers else []
    except Exception as e:
        # If we can't access attributes (detached instance), skip metrics tracking
        logger.warning(
            f"Failed to track assistant management metric '{metric_name}': unable to access assistant attributes. "
            "This typically occurs when the SQLAlchemy instance is detached from the session.",
            extra={
                "metric_name": metric_name,
                "error_class": e.__class__.__name__,
                "user_id": user.id if user else None,
            },
        )
        return

    # Build attributes dict
    attributes = {
        MetricsAttributes.ASSISTANT_NAME: assistant_name,
        MetricsAttributes.ASSISTANT_DESCRIPTION: assistant_description,
        MetricsAttributes.PROJECT: assistant_project,
        MetricsAttributes.SLUG: assistant_slug,
        MetricsAttributes.LLM_MODEL: assistant_llm_model,
        MetricsAttributes.USER_ID: user.id,
        MetricsAttributes.USER_NAME: user.name,
        MetricsAttributes.USER_EMAIL: user.username,
        MetricsAttributes.NESTED_ASSISTANTS_COUNT: assistant_ids_count,
    }

    # Track MCP server metrics
    for mcp in mcp_servers:
        config = f"{mcp.config}_{mcp.arguments}"
        attributes[MetricsAttributes.MCP_SERVER_NAME] = mcp.name
        attributes[MetricsAttributes.MCP_SERVER_CONFIG] = config
        BaseMonitoringService.send_count_metric(
            name=f"{MCP_SERVERS_ASSISTANT_METRIC}_{metric_name}",
            attributes=attributes,
        )

    if additional_attributes:
        attributes.update(additional_attributes)

    if success:
        BaseMonitoringService.send_count_metric(
            name=metric_name,
            attributes=attributes,
        )
    else:
        BaseMonitoringService.send_count_metric(
            name=metric_name + "_error",
            attributes=attributes,
        )


def _raise_assistant_operation_error(operation: str, exception: Exception):
    """
    Raises a standardized error for assistant operations
    """
    # Get the exception message - handle both ExtendedHTTPException and regular exceptions
    error_message = exception.message if isinstance(exception, ExtendedHTTPException) else str(exception)

    raise ExtendedHTTPException(
        code=status.HTTP_400_BAD_REQUEST,
        message=f"Failed to {operation} assistant",
        details=f"An error occurred while trying to {operation} the assistant: {error_message}",
        help="Please check the assistant data for any invalid fields or constraints. "
        "If you continue to experience issues, please contact support with the error details.",
    ) from exception


def _index_marketplace_assistant(assistant_id: str, user: User, is_update: bool = False):
    """
    Background task to index or reindex a marketplace assistant in Elasticsearch.

    This function runs asynchronously after publish/update operations complete,
    ensuring the marketplace index stays in sync with PostgreSQL data without blocking API responses.

    Args:
        assistant_id: ID of the assistant to index
        user: User who triggered the operation (for cost tracking)
        is_update: If True, this is an update of existing document (won't increment progress counters).
                   If False, this is a new document (will increment progress counters).
    """
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    from codemie.service.llm_service.utils import set_llm_context

    set_llm_context(None, user.current_project, user)

    operation = "reindexing" if is_update else "indexing"
    try:
        PlatformIndexingService.index_single_assistant(assistant_id, user=user, is_update=is_update)
        logger.info(
            f"Background {operation} completed for marketplace assistant {assistant_id}",
            extra={"assistant_id": assistant_id, "is_update": is_update},
        )
    except Exception as index_error:
        logger.error(
            f"Background {operation} failed for marketplace assistant {assistant_id}: {index_error}",
            extra={"assistant_id": assistant_id, "error": str(index_error), "is_update": is_update},
            exc_info=True,
        )
