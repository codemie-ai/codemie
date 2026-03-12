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

"""Metric name constants for analytics queries.

This module defines valid metric names used in Elasticsearch queries to prevent
typos and ensure consistency across the analytics service layer.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class MetricName(str, Enum):
    """Valid metric names for analytics queries.

    These correspond to the metric_name field in the codemie_metrics_logs* index.
    """

    # Core usage metrics
    CONVERSATION_ASSISTANT_USAGE = "conversation_assistant_usage"
    DATASOURCE_TOKENS_USAGE = "datasource_tokens_usage"
    WORKFLOW_EXECUTION_TOTAL = "workflow_execution_total"

    # Tools and integrations
    CODEMIE_TOOLS_USAGE_TOTAL = "codemie_tools_usage_total"
    CODEMIE_TOOLS_USAGE_TOKENS = "codemie_tools_usage_tokens"
    CODEMIE_TOOLS_USAGE_ERRORS = "codemie_tools_usage_errors"
    AGENT_EXECUTION_TOTAL = "agent_execution_total"
    WEBHOOK_INVOCATION_TOTAL = "webhook_invocation_total"

    # MCP metrics (assistant creation/update events with MCP attribution)
    MCP_CREATE_ASSISTANT = "create_assistant"
    MCP_UPDATE_ASSISTANT = "update_assistant"

    # Budget monitoring
    BUDGET_SOFT_LIMIT_WARNING = "codemie_llm_soft_budget_limit"
    BUDGET_HARD_LIMIT_VIOLATION = "codemie_llm_hard_budget_limit"

    # CLI metrics
    CLI_TOOL_USAGE_TOTAL = "codemie_cli_tool_usage_total"  # New CLI session metric with tool usage tracking
    CLI_COMMAND_EXECUTION_TOTAL = (
        "codemie_cli_usage_total"  # Legacy CLI session metric (kept for backward compatibility)
    )
    CLI_LLM_USAGE_TOTAL = "codemie_litellm_proxy_usage"  # Token/cost data from LiteLLM proxy (filter: cli_request=true)
    CLI_AGENT_USAGE_TOTAL = "cli_agent_usage_total"
    CLI_ERROR_TOTAL = "cli_error_total"
    CLI_REPOSITORY_ACTIVITY_TOTAL = "cli_repository_activity_total"

    # LLM Proxy metrics
    LLM_PROXY_REQUESTS_TOTAL = "llm_proxy_requests_total"
    LLM_PROXY_ERRORS_TOTAL = "llm_proxy_errors_total"

    @classmethod
    def to_list(cls, *metrics: MetricName) -> list[str]:
        """Convert metric enum values to list of strings.

        Args:
            *metrics: Variable number of MetricName enum values

        Returns:
            List of metric name strings

        Example:
            >>> MetricName.to_list(MetricName.CONVERSATION_ASSISTANT_USAGE, MetricName.WORKFLOW_EXECUTION_TOTAL)
            ['conversation_assistant_usage', 'workflow_execution_total']
        """
        return [metric.value for metric in metrics]

    @classmethod
    def to_list_from_group(cls, metric_group: Any) -> list[str]:
        """Convert a metric group (list of MetricName) to list of strings.

        Args:
            metric_group: List of MetricName enum values (e.g., MetricName.SUMMARY_METRICS).
                          Accepts ClassVar[list[MetricName]] from class attributes.

        Returns:
            List of metric name strings

        Example:
            >>> MetricName.to_list_from_group(MetricName.SUMMARY_METRICS)
            ['conversation_assistant_usage', 'datasource_tokens_usage', 'workflow_execution_total']

        Note:
            Uses Any type to work around type checker limitations with ClassVar in Enums.
            At runtime, always receives list[MetricName].
        """
        return [metric.value if hasattr(metric, "value") else metric for metric in metric_group]


# Metric Groups - commonly used combinations
# These constants eliminate hardcoded metric lists and prevent errors from missing metrics
MetricName.SUMMARY_METRICS = [
    MetricName.CONVERSATION_ASSISTANT_USAGE,
    MetricName.DATASOURCE_TOKENS_USAGE,
    MetricName.WORKFLOW_EXECUTION_TOTAL,
    MetricName.CLI_TOOL_USAGE_TOTAL,  # New CLI session metric (primary)
    MetricName.CLI_COMMAND_EXECUTION_TOTAL,  # Legacy CLI session metric (fallback for backward compatibility)
    MetricName.CLI_LLM_USAGE_TOTAL,  # Token/cost data from LiteLLM proxy
]

MetricName.TOOLS_METRICS = [
    MetricName.CODEMIE_TOOLS_USAGE_TOTAL,
    MetricName.CODEMIE_TOOLS_USAGE_TOKENS,
    MetricName.CODEMIE_TOOLS_USAGE_ERRORS,
]

MetricName.MCP_METRICS = [
    MetricName.MCP_CREATE_ASSISTANT,
    MetricName.MCP_UPDATE_ASSISTANT,
]

MetricName.USAGE_METRICS = [
    MetricName.CONVERSATION_ASSISTANT_USAGE,
    MetricName.WORKFLOW_EXECUTION_TOTAL,
]

# Activity metrics - all metrics that indicate project/user activity
MetricName.ACTIVITY_METRICS = [
    MetricName.CONVERSATION_ASSISTANT_USAGE,
    MetricName.CLI_TOOL_USAGE_TOTAL,  # New CLI session metric (primary)
    MetricName.CLI_COMMAND_EXECUTION_TOTAL,  # Legacy CLI session metric (fallback)
    MetricName.CLI_LLM_USAGE_TOTAL,  # Token/cost data from LiteLLM proxy
    MetricName.WORKFLOW_EXECUTION_TOTAL,
    MetricName.DATASOURCE_TOKENS_USAGE,
]

# Spending metrics - same as activity for consistency
MetricName.SPENDING_METRICS = [
    MetricName.CONVERSATION_ASSISTANT_USAGE,
    MetricName.CLI_TOOL_USAGE_TOTAL,  # New CLI session metric (primary)
    MetricName.CLI_COMMAND_EXECUTION_TOTAL,  # Legacy CLI session metric (fallback)
    MetricName.CLI_LLM_USAGE_TOTAL,  # Token/cost data from LiteLLM proxy
    MetricName.WORKFLOW_EXECUTION_TOTAL,
    MetricName.DATASOURCE_TOKENS_USAGE,
]
