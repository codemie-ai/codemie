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

from collections.abc import Mapping
from typing import Optional, TYPE_CHECKING, Dict, Any

from codemie.core.constants import (
    TOOL_TYPE,
    ToolType,
)
from codemie.core.dependecies import get_current_project
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.tool_run_context import ToolRunContext
from codemie.service.monitoring.metrics_constants import (
    TOOLS_USAGE_TOTAL_METRIC,
    TOOLS_USAGE_TOKENS_METRIC,
    TOOLS_USAGE_ERRORS_METRIC,
    SCRIPT_TOOL_CALLS_REFUSED_METRIC,
    INVOKED_BY_SCRIPT,
    ScriptCallKind,
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
            **ToolRunContext.from_metadata(metadata, with_user_id=False).dimensions(),
            MetricsAttributes.TOOL_NAME: tool_name,
            MetricsAttributes.TOOL_TYPE: metadata.get(TOOL_TYPE, ToolType.INTERNAL),
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
    def send_script_tool_call_metrics(
        cls,
        *,
        kind: str,
        tool_name: str,
        tool_type: str,
        dimensions: Mapping[str, str],
        error_code: str | None = None,
        error_class: str | None = None,
    ) -> None:
        """One record for a tool call made from a workspace script, from explicit values.

        The polling thread that settles the call has no request contextvars, so nothing is read from them here, and no
        tokens are written (the script run itself is counted once as ``execute_workspace_script``). ``dimensions`` is
        the run's shared set (user, project, assistant, workflow, model, source kind). ``kind`` picks the metric:
        success and abandoned go to the usage total with ``status``, an error to the errors total, a refusal only to
        the refused counter. ``error_code`` is written for errors and refusals, ``error_class`` for errors.
        """
        attributes = {
            **dimensions,
            MetricsAttributes.TOOL_NAME: tool_name,
            MetricsAttributes.BASE_TOOL_NAME: tool_name,
            MetricsAttributes.TOOL_TYPE: tool_type,
        }
        if kind != ScriptCallKind.REFUSED:
            # The refused counter is script-only by definition and (ticket decision 4) has no `invoked_by`.
            attributes[MetricsAttributes.INVOKED_BY] = INVOKED_BY_SCRIPT
        if kind in (ScriptCallKind.SUCCESS, ScriptCallKind.ABANDONED):
            attributes[MetricsAttributes.STATUS] = kind
            name = TOOLS_USAGE_TOTAL_METRIC
        else:
            attributes[MetricsAttributes.ERROR_CODE] = error_code or ""
            # Like a direct call, `error_class` is the exception's class name; the protocol code is `error_code`.
            # Written only when the handler saw an exception (a timeout, say, has none).
            if kind == ScriptCallKind.ERROR and error_class:
                attributes[MetricsAttributes.ERROR_CLASS] = error_class
            name = SCRIPT_TOOL_CALLS_REFUSED_METRIC if kind == ScriptCallKind.REFUSED else TOOLS_USAGE_ERRORS_METRIC
        cls.send_count_metric(name=name, attributes=attributes)

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
