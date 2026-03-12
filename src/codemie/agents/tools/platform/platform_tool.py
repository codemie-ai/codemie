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

"""Platform monitoring and analytics tool implementations.

This module contains individual tool classes for platform analytics.
Each tool extends CodeMieTool and implements the execute method.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Type, List

from codemie_tools.base.codemie_tool import CodeMieTool
from pydantic import BaseModel

from codemie.agents.tools.platform.models import (
    GetAssistantsInput,
    GetAssistantsOutput,
    GetConversationMetricsInput,
    GetConversationMetricsOutput,
    GetRawConversationsInput,
    GetRawConversationsOutput,
    GetSpendingInput,
    GetSpendingOutput,
    GetKeySpendingInput,
    GetKeySpendingOutput,
    GetConversationAnalyticsInput,
    GetConversationAnalyticsOutput,
    AssistantOutput,
    ConversationMetricOutput,
    ConversationMetricsSummary,
    ConversationOutput,
    MessageOutput,
    SpendingGroupOutput,
    KeySpendingOutput,
    ConversationAnalyticsWithMetricsOutput,
    CreatedByUserOutput,
    ContextOutput,
    ToolOutput,
)
from codemie.agents.tools.platform.tools_vars import (
    GET_ASSISTANTS_TOOL,
    GET_SPENDING_TOOL,
    GET_RAW_CONVERSATIONS_TOOL,
    GET_CONVERSATION_METRICS_TOOL,
    GET_KEY_SPENDING_TOOL,
    GET_CONVERSATION_ANALYTICS_TOOL,
)
from codemie.configs import logger
from codemie.core.exceptions import InvalidFilterCombinationError, UnauthorizedPlatformAccessError
from codemie.rest_api.security.user import User


# ==================== Helper Functions ====================


def _parse_date_filters(since_date: Optional[str], last_n_days: Optional[int]) -> Optional[datetime]:
    """
    Parse date filter inputs into a datetime object.

    Args:
        since_date: ISO 8601 format date string
        last_n_days: Number of days to look back

    Returns:
        Parsed datetime or None if no filter provided (always in UTC)

    Raises:
        InvalidFilterCombinationError: If both filters are provided
    """
    if since_date and last_n_days:
        raise InvalidFilterCombinationError("Cannot specify both since_date and last_n_days")

    if since_date:
        return datetime.fromisoformat(since_date.replace('Z', '+00:00'))

    if last_n_days:
        # Use UTC timezone for consistency
        return datetime.now(timezone.utc) - timedelta(days=last_n_days)

    return None


def _validate_user_permissions(
    user: User,
    project: Optional[str] = None,
    target_user_name: Optional[str] = None,
):
    """
    Validate user permissions for platform analytics tools.

    Rules:
    - If user is admin - allow
    - If user requests for particular project - user must be at least application admin for this project
    - If user requests for particular project and particular user (not own) - user must be application admin
    - If user requests for particular project and own user - allow
    - If user requests for particular user (not own) and no project specified - list available projects
    - If project value equals own username - allow (special case)

    Args:
        user: The requesting user
        project: Optional project filter
        target_user_name: Optional target user name filter (case-insensitive)

    Raises:
        PermissionDeniedError: If user doesn't have sufficient permissions
    """
    # Rule 1: Admin users have full access
    if user.is_admin:
        return

    # Rule 6: Special case - project equals own username
    if project and project == user.username:
        return

    # Determine if querying own data (case-insensitive comparison)
    is_own_user = not target_user_name or target_user_name.lower() == user.name.lower()

    # Rule 4: User requests for particular project and own user - allow
    if project and is_own_user:
        return

    # Rule 2: User requests for particular project - must be application admin
    if project:
        if project not in user.admin_project_names:
            raise UnauthorizedPlatformAccessError(
                f"You don't have admin permissions to access project: '{project}'. "
                f"You are an admin for those projects: '{user.admin_project_names}'."
            )
        return

    # Rule 5: User requests for particular user (not own) without project
    if not is_own_user:
        if not user.admin_project_names:
            raise UnauthorizedPlatformAccessError(
                "You don't have permissions to query other users' data. "
                "You are not an application admin for any project."
            )

        projects_list = ", ".join(user.admin_project_names)
        raise UnauthorizedPlatformAccessError(
            f"When querying other users' data, you must specify a project. "
            f"You are an application admin for: {projects_list}"
        )


def _transform_created_by(created_by) -> CreatedByUserOutput:
    """Transform created_by data to output model."""
    if created_by:
        return CreatedByUserOutput(
            user_id=created_by.id,
            name=created_by.name,
            username=created_by.username,
        )
    return CreatedByUserOutput(
        user_id="system",
        name="system",
        username="system",
    )


def _transform_tools(toolkits) -> List[ToolOutput]:
    """Transform toolkits to tool output models."""
    tools = []
    for toolkit in toolkits or []:
        for tool in toolkit.tools or []:
            tools.append(
                ToolOutput(
                    toolkit=toolkit.toolkit,
                    name=tool.name,
                    label=tool.label or tool.name,
                )
            )
    return tools


def _transform_context(context) -> List[ContextOutput]:
    """Transform context data to output models."""
    return [
        ContextOutput(
            context_type=ctx.context_type,
            name=ctx.name,
        )
        for ctx in (context or [])
    ]


def _transform_assistant(assistant) -> AssistantOutput:
    """Transform full assistant model to output model."""
    return AssistantOutput(
        id=assistant.id,
        name=assistant.name,
        description=assistant.description,
        system_prompt=assistant.system_prompt or "",
        project=assistant.project or "",
        created_by=_transform_created_by(assistant.created_by),
        created_date=assistant.created_date.isoformat() if assistant.created_date else "",
        updated_date=assistant.update_date.isoformat() if assistant.update_date else "",
        llm_model_type=assistant.llm_model_type,
        tools=_transform_tools(assistant.toolkits) if hasattr(assistant, 'toolkits') else [],
        context=_transform_context(assistant.context) if hasattr(assistant, 'context') else [],
        sub_assistants_ids=assistant.assistant_ids if hasattr(assistant, 'assistant_ids') else [],
    )


def _transform_assistant_minimal(assistant) -> AssistantOutput:
    """Transform minimal assistant response to output model with default values for missing fields."""
    return AssistantOutput(
        id=assistant.id,
        name=assistant.name,
        description=assistant.description or "",
        system_prompt="",  # Not available in minimal response
        project="",  # Not available in minimal response
        created_by=_transform_created_by(assistant.created_by),
        created_date="",  # Not available in minimal response
        updated_date="",  # Not available in minimal response
        llm_model_type="",  # Not available in minimal response
        tools=[],  # Not available in minimal response
        context=[],  # Not available in minimal response
        sub_assistants_ids=[],  # Not available in minimal response
    )


def _build_assistant_filters(
    filters: Optional[dict],
    date_filter: Optional[datetime],
    user_name: Optional[str],
    assistant_scope,
    assistant_ids: Optional[List[str]],
    project: Optional[str],
) -> dict:
    """Build filters dictionary for assistant repository query."""
    repository_filters = filters.copy() if filters else {}

    if date_filter:
        repository_filters["created_date"] = {">=": date_filter.isoformat()}

    # user_name filter only applies for created_by_user scope
    from codemie.service.assistant.assistant_repository import AssistantScope

    if user_name and assistant_scope == AssistantScope.CREATED_BY_USER:
        # Use exact match filter on created_by field (mapped to created_by.name.keyword in filter config)
        repository_filters["created_by"] = user_name
    elif user_name:
        logger.warning(
            f"user_name filter '{user_name}' provided but scope is '{assistant_scope.value}'. "
            "user_name filter only applies when scope='created_by_user'"
        )

    if assistant_ids:
        repository_filters["id"] = assistant_ids

    if project:
        repository_filters["project"] = project

    return repository_filters


def _transform_conversation_metric(item) -> ConversationMetricOutput:
    """Transform conversation metric to output model."""
    metric = item.metric
    return ConversationMetricOutput(
        conversation_id=metric.conversation_id,
        user_name=metric.user_name or "unknown",
        project=metric.project or "unknown",
        assistant_ids=item.assistant_ids,
        last_interaction_date=metric.update_date.isoformat() if metric.update_date else "",
        total_messages=metric.number_of_messages or 0,
        total_input_tokens=metric.total_input_tokens or 0,
        total_output_tokens=metric.total_output_tokens or 0,
        total_money_spent=metric.total_money_spent or 0.0,
        average_response_time=metric.avg_response_time or 0.0,
    )


def _calculate_metrics_summary(result) -> tuple[ConversationMetricsSummary, List[ConversationMetricOutput]]:
    """Calculate summary statistics and transform metrics."""
    total_messages = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_money_spent = 0.0
    metric_outputs = []

    for item in result.metrics_with_assistants:
        metric = item.metric
        total_messages += metric.number_of_messages or 0
        total_input_tokens += metric.total_input_tokens or 0
        total_output_tokens += metric.total_output_tokens or 0
        total_money_spent += metric.total_money_spent or 0.0
        metric_outputs.append(_transform_conversation_metric(item))

    summary = ConversationMetricsSummary(
        total_count=result.total_count,
        total_messages=total_messages,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_money_spent=total_money_spent,
    )

    return summary, metric_outputs


def _extract_tools_invoked(msg, full_mode: bool) -> Optional[List[dict]]:
    """
    Extract tool invocation information from message thoughts.

    Returns tool_name and input_text from thoughts, but never includes tool results
    to avoid large response bodies.

    Args:
        msg: Message object with thoughts attribute
        full_mode: Whether to extract tool information

    Returns:
        List of dicts with tool_name and tool_input, or None if not full_mode
    """
    if not full_mode or not hasattr(msg, 'thoughts') or not msg.thoughts:
        return None

    tools_invoked = []
    for thought in msg.thoughts:
        # Extract tool information from thoughts
        # Include tool_name and input_text, but never include results/message (can be huge)
        if hasattr(thought, 'author_name') and thought.author_name:
            tool_info = {
                "tool_name": thought.author_name,
                "tool_input": thought.input_text if hasattr(thought, 'input_text') else None,
            }
            tools_invoked.append(tool_info)

    return tools_invoked if tools_invoked else None


def _transform_message(msg, full_mode: bool) -> MessageOutput:
    """Transform message to output model."""
    return MessageOutput(
        role=msg.role.value if hasattr(msg.role, 'value') else str(msg.role),
        content=msg.message or "",
        timestamp=msg.date.isoformat() if msg.date else "",
        history_index=msg.history_index or 0,
        tools_invoked=_extract_tools_invoked(msg, full_mode),
    )


def _transform_conversation(conversation, full_mode: bool) -> ConversationOutput:
    """Transform conversation to output model."""
    messages = [_transform_message(msg, full_mode) for msg in conversation.history]

    return ConversationOutput(
        conversation_id=conversation.conversation_id,
        conversation_name=conversation.conversation_name or "",
        user_id=conversation.user_id or "unknown",
        user_name=conversation.user_name or "unknown",
        project=conversation.project or "unknown",
        created_date=conversation.date.isoformat() if conversation.date else "",
        messages=messages,
    )


def _transform_conversation_spending_breakdown(conversation_result) -> List[SpendingGroupOutput]:
    """Transform conversation spending breakdown to output models."""
    return [
        SpendingGroupOutput(
            dimension_type=item.dimension_type,
            dimension_id=item.dimension_id,
            dimension_name=item.dimension_name,
            money_spent=item.money_spent,
            input_tokens=item.input_tokens,
            output_tokens=item.output_tokens,
            conversation_count=item.conversation_count,
            workflow_execution_count=item.workflow_execution_count,
            average_cost_per_item=item.average_cost_per_item,
        )
        for item in conversation_result.spending_breakdown
    ]


def _transform_workflow_spending_breakdown(workflow_data: dict) -> List[SpendingGroupOutput]:
    """Transform workflow spending breakdown to output models."""
    return [
        SpendingGroupOutput(
            dimension_type=wf_item['dimension_type'],
            dimension_id=wf_item['dimension_id'],
            dimension_name=wf_item['dimension_name'],
            money_spent=wf_item['money_spent'],
            input_tokens=wf_item['input_tokens'],
            output_tokens=wf_item['output_tokens'],
            conversation_count=None,
            workflow_execution_count=wf_item['workflow_execution_count'],
            average_cost_per_item=wf_item['average_cost_per_item'],
        )
        for wf_item in workflow_data['breakdown']
    ]


def _calculate_total_spending(conversation_result, workflow_data: dict) -> tuple[int, int, float]:
    """Calculate total spending from conversation and workflow data."""
    total_input_tokens = conversation_result.total_input_tokens + workflow_data['total_input_tokens']
    total_output_tokens = conversation_result.total_output_tokens + workflow_data['total_output_tokens']
    total_money_spent_from_metrics = conversation_result.total_money_spent + workflow_data['total_money_spent']

    return total_input_tokens, total_output_tokens, total_money_spent_from_metrics


# ==================== Tool Classes ====================


class GetAssistantsTool(CodeMieTool):
    """Tool for retrieving assistants with platform analytics filters."""

    name: str = GET_ASSISTANTS_TOOL.name
    description: str = GET_ASSISTANTS_TOOL.description
    args_schema: Type[BaseModel] = GetAssistantsInput
    user: User

    def execute(
        self,
        user_name: Optional[str] = None,
        assistant_ids: Optional[List[str]] = None,
        project: Optional[str] = None,
        since_date: Optional[str] = None,
        last_n_days: Optional[int] = None,
        scope: str = "created_by_user",
        filters: Optional[dict] = None,
        minimal_response: bool = False,
        limit: int = 100,
        offset: int = 0,
    ):
        """Execute get_assistants tool using AssistantRepository with scope-based filtering."""
        from codemie.service.assistant.assistant_repository import AssistantRepository, AssistantScope

        target_user = self.user
        if user_name and self.user.name.lower() != user_name.lower():
            # Create a temporary User object with the name field populated
            target_user = User(id=user_name, name=user_name, username=user_name)
            # we can get assistants for defined user only with "created" scope
            scope = AssistantScope.CREATED_BY_USER.value
        logger.info(
            f"Platform tool: get_assistants called by user {self.user.username} "
            f"(user_name={user_name}, project={project}, scope={scope}, limit={limit})"
        )

        # Parse and validate scope
        try:
            assistant_scope = AssistantScope(scope)
        except ValueError:
            raise InvalidFilterCombinationError(
                f"Invalid scope '{scope}'. Must be one of: visible_to_user, created_by_user, marketplace, all"
            )

        # Build filters and query assistants
        date_filter = _parse_date_filters(since_date, last_n_days)
        repository_filters = _build_assistant_filters(
            filters, date_filter, user_name, assistant_scope, assistant_ids, project
        )

        repository = AssistantRepository()
        result = repository.query(
            user=target_user,
            scope=assistant_scope,
            filters=repository_filters,
            page=offset // limit if limit > 0 else 0,
            per_page=limit,
            minimal_response=minimal_response,
            apply_scope=False,
        )

        # Transform results - use appropriate transform function based on response type
        if minimal_response:
            assistant_outputs = [_transform_assistant_minimal(assistant) for assistant in result["data"]]
        else:
            assistant_outputs = [_transform_assistant(assistant) for assistant in result["data"]]

        return GetAssistantsOutput(
            total_count=result["pagination"]["total"],
            assistants=assistant_outputs,
        ).model_dump_json(indent=4)


class GetConversationMetricsTool(CodeMieTool):
    """Tool for retrieving conversation metrics."""

    name: str = GET_CONVERSATION_METRICS_TOOL.name
    description: str = GET_CONVERSATION_METRICS_TOOL.description
    args_schema: Type[BaseModel] = GetConversationMetricsInput
    user: User

    def execute(
        self,
        user_name: Optional[str] = None,
        assistant_ids: Optional[List[str]] = None,
        project: Optional[str] = None,
        since_date: Optional[str] = None,
        last_n_days: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ):
        """Execute get_conversation_metrics tool."""
        from codemie.service.conversation_service import ConversationService

        user_name = user_name or self.user.username
        _validate_user_permissions(self.user, project=project, target_user_name=user_name)

        logger.info(
            f"Platform tool: get_conversation_metrics called by user {self.user.username} "
            f"(user_name={user_name}, project={project}, limit={limit})"
        )

        date_filter = _parse_date_filters(since_date, last_n_days)
        result = ConversationService.get_conversation_metrics_with_filters(
            user_name=user_name,
            assistant_ids=assistant_ids,
            project=project,
            since_date=date_filter,
            limit=limit,
            offset=offset,
        )

        summary, metric_outputs = _calculate_metrics_summary(result)

        return GetConversationMetricsOutput(
            total_metrics_summary=summary,
            metrics=metric_outputs,
        ).model_dump_json(indent=4)


class GetRawConversationsTool(CodeMieTool):
    """Tool for retrieving raw conversation data including message history."""

    name: str = GET_RAW_CONVERSATIONS_TOOL.name
    description: str = GET_RAW_CONVERSATIONS_TOOL.description
    args_schema: Type[BaseModel] = GetRawConversationsInput
    user: User

    def execute(
        self,
        user_name: Optional[str] = None,
        assistant_ids: Optional[List[str]] = None,
        project: Optional[str] = None,
        since_date: Optional[str] = None,
        last_n_days: Optional[int] = None,
        full_mode: bool = False,
        limit: int = 100,
        offset: int = 0,
    ):
        """Execute get_raw_conversations tool."""
        from codemie.service.conversation_service import ConversationService

        user_name = user_name or self.user.name
        _validate_user_permissions(self.user, project=project, target_user_name=user_name)

        logger.info(
            f"Platform tool: get_raw_conversations called by user {self.user.username} "
            f"(user_name={user_name}, project={project}, full_mode={full_mode}, limit={limit})"
        )

        date_filter = _parse_date_filters(since_date, last_n_days)
        total_count, conversations = ConversationService.get_raw_conversations_with_filters(
            user_name=user_name,
            assistant_ids=assistant_ids,
            project=project,
            since_date=date_filter,
            limit=limit,
            offset=offset,
        )

        conversation_outputs = [_transform_conversation(conv, full_mode) for conv in conversations]

        return GetRawConversationsOutput(
            total_count=total_count,
            conversations=conversation_outputs,
        ).model_dump_json(indent=4)


class GetSpendingTool(CodeMieTool):
    """Tool for retrieving spending analytics aggregated by dimension."""

    name: str = GET_SPENDING_TOOL.name
    description: str = GET_SPENDING_TOOL.description
    args_schema: Type[BaseModel] = GetSpendingInput
    user: User

    def execute(
        self,
        user_name: Optional[str] = None,
        assistant_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        project: Optional[str] = None,
        since_date: Optional[str] = None,
        last_n_days: Optional[int] = None,
        include_breakdown: bool = False,
    ):
        """Execute get_spending tool."""
        from codemie.service.conversation_service import ConversationService
        from codemie.service.workflow_service import WorkflowService

        _validate_user_permissions(self.user, project=project, target_user_name=user_name)

        logger.info(
            f"Platform tool: get_spending called by user {self.user.username} "
            f"(user_name={user_name}, project={project}, include_breakdown={include_breakdown})"
        )

        date_filter = _parse_date_filters(since_date, last_n_days)

        # Get conversation and workflow spending analytics
        conversation_result = ConversationService.get_spending_analytics(
            user_name=user_name,
            assistant_id=assistant_id,
            project=project,
            since_date=date_filter,
            include_breakdown=include_breakdown,
        )

        workflow_data = WorkflowService.get_workflow_spending_analytics(
            user_name=user_name,
            workflow_id=workflow_id,
            project=project,
            since_date=date_filter,
            include_breakdown=include_breakdown,
        )

        # Combine spending breakdowns
        spending_breakdown = _transform_conversation_spending_breakdown(conversation_result)
        spending_breakdown.extend(_transform_workflow_spending_breakdown(workflow_data))

        # Calculate totals
        total_input_tokens, total_output_tokens, total_money_spent = _calculate_total_spending(
            conversation_result, workflow_data
        )

        return GetSpendingOutput(
            total_money_spent=total_money_spent,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_conversations=conversation_result.total_conversations,
            total_workflow_executions=workflow_data['total_workflow_executions'],
            spending_breakdown=spending_breakdown,
        ).model_dump_json(indent=4)


class GetKeySpendingTool(CodeMieTool):
    """Tool for retrieving LiteLLM API key spending analytics."""

    name: str = GET_KEY_SPENDING_TOOL.name
    description: str = GET_KEY_SPENDING_TOOL.description
    args_schema: Type[BaseModel] = GetKeySpendingInput
    user: User

    def execute(
        self,
        key_aliases: Optional[List[str]] = None,
        include_details: bool = True,
        page: int = 1,
        size: int = 100,
    ):
        """Execute get_key_spending tool."""
        from codemie.enterprise.litellm import get_litellm_service_or_none
        from codemie.core.exceptions import ServiceUnavailableError

        # Admin-only check
        if not self.user.is_admin:
            raise UnauthorizedPlatformAccessError(
                "You don't have permissions to access LiteLLM key spending data. Admin privileges required."
            )

        logger.info(
            f"Platform tool: get_key_spending called by admin {self.user.username} "
            f"(key_aliases={key_aliases}, include_details={include_details}, page={page}, size={size})"
        )

        # Get LiteLLM service from enterprise
        litellm_service = get_litellm_service_or_none()
        if litellm_service is None:
            logger.warning("LiteLLM service not available - cannot retrieve key spending data")
            raise ServiceUnavailableError(
                "LiteLLM key spending analytics are not available", service_name="LiteLLM Enterprise"
            )

        # Get key spending data (always fetches full data from LiteLLM)
        keys_data = (
            litellm_service.get_key_info(key_aliases, include_details=True, page=page, size=size)
            if key_aliases
            else litellm_service.get_all_keys_spending(include_details=True, page=page, size=size)
        )

        # Transform to output format, filtering fields based on include_details
        if include_details:
            # Include all fields
            key_outputs = [KeySpendingOutput(**key_data.model_dump()) for key_data in keys_data]
        else:
            # Include only essential fields for minimal response
            key_outputs = [
                KeySpendingOutput(
                    key_alias=key_data.key_alias,
                    spend=key_data.spend,
                    max_budget=key_data.max_budget,
                    budget_duration=key_data.budget_duration,
                    budget_reset_at=key_data.budget_reset_at,
                )
                for key_data in keys_data
            ]

        # Calculate totals
        total_spend = sum(k.spend for k in key_outputs)

        return GetKeySpendingOutput(
            total_keys=len(key_outputs),
            total_spend_across_keys=total_spend,
            keys=key_outputs,
        ).model_dump_json(indent=4)


class GetConversationAnalyticsTool(CodeMieTool):
    """Tool for retrieving conversation analytics with combined metrics data."""

    name: str = GET_CONVERSATION_ANALYTICS_TOOL.name
    description: str = GET_CONVERSATION_ANALYTICS_TOOL.description
    args_schema: Type[BaseModel] = GetConversationAnalyticsInput
    user: User

    @staticmethod
    def _transform_analytics_to_dicts(analytics) -> dict:
        """Transform analytics nested Pydantic models to dicts.

        Args:
            analytics: ConversationAnalytics instance

        Returns:
            Dict with transformed nested models
        """
        return {
            "assistants_used": [a.model_dump() for a in analytics.assistants_used],
            "topics": [t.model_dump() for t in analytics.topics],
            "satisfaction": analytics.satisfaction.model_dump() if analytics.satisfaction else None,
            "maturity": analytics.maturity.model_dump() if analytics.maturity else None,
            "anti_patterns": [ap.model_dump() for ap in analytics.anti_patterns],
        }

    @staticmethod
    def _build_output(analytics, metrics, analytics_dicts: dict) -> ConversationAnalyticsWithMetricsOutput:
        """Build combined analytics and metrics output object.

        Args:
            analytics: ConversationAnalytics instance
            metrics: ConversationMetrics instance or None
            analytics_dicts: Pre-transformed analytics dicts

        Returns:
            ConversationAnalyticsWithMetricsOutput instance
        """
        return ConversationAnalyticsWithMetricsOutput(
            # Analytics data
            conversation_id=analytics.conversation_id,
            user_id=analytics.user_id,
            user_name=analytics.user_name,
            project=analytics.project,
            assistants_used=analytics_dicts["assistants_used"],
            topics=analytics_dicts["topics"],
            satisfaction=analytics_dicts["satisfaction"],
            maturity=analytics_dicts["maturity"],
            anti_patterns=analytics_dicts["anti_patterns"],
            last_analysis_date=analytics.last_analysis_date.isoformat(),
            message_count_at_analysis=analytics.message_count_at_analysis,
            llm_model_used=analytics.llm_model_used,
            analysis_duration_seconds=analytics.analysis_duration_seconds,
            # Metrics data (with defaults if not found)
            total_messages=metrics.number_of_messages if metrics else 0,
            total_input_tokens=metrics.total_input_tokens if metrics else 0,
            total_output_tokens=metrics.total_output_tokens if metrics else 0,
            total_money_spent=metrics.total_money_spent if metrics else 0.0,
            average_response_time=metrics.avg_response_time if metrics else 0.0,
            last_interaction_date=metrics.update_date.isoformat() if metrics and metrics.update_date else "",
        )

    def execute(
        self,
        user_name: Optional[str] = None,
        project: Optional[str] = None,
        since_date: Optional[str] = None,
        last_n_days: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ):
        """Execute get_conversation_analytics tool."""
        from codemie.service.conversation_service import ConversationService

        user_name = user_name or self.user.name
        _validate_user_permissions(self.user, project=project, target_user_name=user_name)

        logger.info(
            f"Platform tool: get_conversation_analytics called by user {self.user.username} "
            f"(user_name={user_name}, project={project}, limit={limit})"
        )

        # Parse date filter and fetch data
        date_filter = _parse_date_filters(since_date, last_n_days)
        total_count, combined_results = ConversationService.get_conversation_analytics_with_metrics(
            user_name=user_name,
            project=project,
            since_date=date_filter,
            limit=limit,
            offset=offset,
        )

        # Transform results to output format using helper methods
        combined_outputs = [
            self._build_output(analytics, metrics, self._transform_analytics_to_dicts(analytics))
            for analytics, metrics in combined_results
        ]

        return GetConversationAnalyticsOutput(
            total_count=total_count,
            conversation_analytics=combined_outputs,
        ).model_dump_json(indent=4)
