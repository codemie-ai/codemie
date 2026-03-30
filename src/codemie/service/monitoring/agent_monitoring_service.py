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

from typing import Optional, TYPE_CHECKING, Dict, Any

from codemie.core.constants import AGENT_NAME, LLM_MODEL, USER_NAME, TOOL_TYPE, ToolType, PROJECT
from codemie.core.dependecies import get_current_project
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import (
    TOOLS_USAGE_TOTAL_METRIC,
    TOOLS_USAGE_TOKENS_METRIC,
    TOOLS_USAGE_ERRORS_METRIC,
    MetricsAttributes,
    MCP_SERVERS_ASSISTANT_METRIC,
)

if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import Assistant


class AgentMonitoringService(BaseMonitoringService):
    @classmethod
    def send_tool_metrics(
        cls,
        tool_name: str,
        success: bool,
        output_tokens_used: int = 0,
        tool_metadata: Optional[dict] = None,
        additional_attributes: Optional[dict] = None,
    ):
        metadata = tool_metadata or {}
        attributes = {
            MetricsAttributes.TOOL_NAME: tool_name,
            MetricsAttributes.ASSISTANT_NAME: metadata.get(AGENT_NAME, ""),
            MetricsAttributes.LLM_MODEL: metadata.get(LLM_MODEL, ""),
            MetricsAttributes.USER_NAME: metadata.get(USER_NAME, ""),
            MetricsAttributes.TOOL_TYPE: metadata.get(TOOL_TYPE, ToolType.INTERNAL),
            MetricsAttributes.PROJECT: get_current_project(fallback=metadata.get(PROJECT)),
        }
        if additional_attributes:
            attributes.update(additional_attributes)

        if success:
            cls.send_count_metric(
                name=TOOLS_USAGE_TOTAL_METRIC,
                attributes=attributes,
            )
            cls.send_count_metric(
                name=TOOLS_USAGE_TOKENS_METRIC,
                attributes=attributes,
                count=output_tokens_used,
            )
        else:
            cls.send_count_metric(
                name=TOOLS_USAGE_ERRORS_METRIC,
                attributes=attributes,
            )

    @classmethod
    def send_assistant_mngmnt_metric(
        cls,
        metric_name: str,
        assistant: 'Assistant',
        success: bool,
        user: User,
        additional_attributes: Optional[dict] = None,
    ):
        attributes = {
            MetricsAttributes.ASSISTANT_NAME: assistant.name,
            MetricsAttributes.ASSISTANT_DESCRIPTION: assistant.description,
            MetricsAttributes.PROJECT: get_current_project(fallback=assistant.project),
            MetricsAttributes.SLUG: assistant.slug if assistant.slug is not None else assistant.id,
            MetricsAttributes.LLM_MODEL: assistant.llm_model_type,
            MetricsAttributes.USER_ID: user.id,
            MetricsAttributes.USER_NAME: user.name,
            MetricsAttributes.USER_EMAIL: user.username,
            MetricsAttributes.NESTED_ASSISTANTS_COUNT: len(assistant.assistant_ids) if assistant.assistant_ids else 0,
        }
        for mcp in assistant.mcp_servers:
            config = f"{mcp.config}_{mcp.arguments}"
            attributes[MetricsAttributes.MCP_SERVER_NAME] = mcp.name
            attributes[MetricsAttributes.MCP_SERVER_CONFIG] = config
            cls.send_count_metric(
                name=f"{MCP_SERVERS_ASSISTANT_METRIC}_{metric_name}",
                attributes=attributes,
            )
        if additional_attributes:
            attributes.update(additional_attributes)
        if success:
            cls.send_count_metric(
                name=metric_name,
                attributes=attributes,
            )
        else:
            cls.send_count_metric(
                name=metric_name + "_error",
                attributes=attributes,
            )

    @classmethod
    def track_reaction_metric(
        cls,
        metric_name: str,
        assistant: 'Assistant',
        user_id: str,
        success: bool,
        additional_attributes: Optional[Dict[str, Any]] = None,
    ):
        """Track metrics for assistant reaction operations

        Args:
            metric_name: Name of the metric to track
            assistant: Assistant object
            user_id: ID of the user performing the reaction
            success: Whether the operation was successful
            additional_attributes: Additional attributes to include in the metric
        """
        attributes = {
            MetricsAttributes.ASSISTANT_NAME: assistant.name,
            MetricsAttributes.ASSISTANT_ID: assistant.id,
            MetricsAttributes.PROJECT: get_current_project(fallback=assistant.project),
            MetricsAttributes.SLUG: assistant.slug if assistant.slug is not None else assistant.id,
            MetricsAttributes.USER_ID: user_id,
        }

        if additional_attributes:
            attributes.update(additional_attributes)

        if success:
            cls.send_count_metric(
                name=metric_name,
                attributes=attributes,
            )
        else:
            cls.send_count_metric(
                name=f"{metric_name}_error",
                attributes=attributes,
            )
