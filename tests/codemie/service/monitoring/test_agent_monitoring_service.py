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

from unittest.mock import patch

import pytest
from codemie.core.constants import ToolType
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.monitoring.agent_monitoring_service import AgentMonitoringService
from codemie.service.monitoring.metrics_constants import TOOLS_USAGE_TOTAL_METRIC, TOOLS_USAGE_TOKENS_METRIC


@pytest.fixture
def additional_attributes():
    return {"custom_attribute": "custom_value"}


@pytest.fixture
def mock_assistant():
    return Assistant(
        name="test_assistant",
        description="Test Assistant",
        project="test",
        toolkits=[],
        system_prompt="",
        llm_model_type="test_model",
        slug="test",
        mcp_servers=[],  # Add empty mcp_servers list
        assistant_ids=[],  # Add empty assistant_ids list
    )


@pytest.fixture
def mock_user():
    return User(name="test_user", username="test@example.com", id="test_id")


@patch.object(AgentMonitoringService, "send_count_metric")
def test_send_tool_metrics_success(mock_send_count_metric):
    tool_name = "test_tool"
    success = True
    output_tokens_used = 100
    tool_metadata = {"agent_name": "test", "llm_model": "test", "user_name": "test"}
    additional_attributes = {"extra": "attribute"}

    expected_attributes = {
        "tool_name": tool_name,
        "llm_model": "test",
        "user_name": "test",
        "assistant_name": "test",
        "tool_type": ToolType.INTERNAL,
        "project": "",
        **additional_attributes,
    }

    AgentMonitoringService.send_tool_metrics(
        tool_name,
        success,
        output_tokens_used,
        tool_metadata,
        additional_attributes,
    )

    mock_send_count_metric.assert_any_call(
        name=TOOLS_USAGE_TOTAL_METRIC,
        attributes=expected_attributes,
    )

    mock_send_count_metric.assert_any_call(
        name=TOOLS_USAGE_TOKENS_METRIC,
        attributes=expected_attributes,
        count=output_tokens_used,
    )


@patch.object(AgentMonitoringService, "send_count_metric")
def test_send_tool_metrics_failure(mock_send_count_metric):
    tool_name = "test_tool"
    output_tokens_used = 100
    success = False
    tool_metadata = {"agent_name": "test", "llm_model": "test", "user_name": "test"}
    additional_attributes = {"error_class": "test"}

    AgentMonitoringService.send_tool_metrics(
        tool_name, success, output_tokens_used, tool_metadata, additional_attributes
    )

    assert all(
        call.kwargs["attributes"]["error_class"] == "test" for call in mock_send_count_metric.call_args_list
    ), "Error class attribute should be 'test'"


@patch.object(AgentMonitoringService, "send_count_metric")
def test_send_tool_metrics_no_metadata(mock_send_count_metric):
    tool_name = "test_tool"
    success = True

    AgentMonitoringService.send_tool_metrics(tool_name, success)

    assert all(
        call.kwargs["attributes"]["assistant_name"] == "" for call in mock_send_count_metric.call_args_list
    ), "Agent name attribute should be ''"


@patch.object(AgentMonitoringService, "send_count_metric")
def test_send_tool_metrics_none_project(mock_send_count_metric):
    """Project attribute must be empty string when metadata contains project=None.

    When assistant.project is None (no project assigned), the tool metadata dict
    contains {"project": None}. metadata.get("project", "") returns None because
    the key exists; using `or ""` ensures an empty string is stored instead so
    Elasticsearch keyword filters still work correctly.
    """
    tool_name = "test_tool"
    tool_metadata = {"agent_name": "test", "llm_model": "test", "user_name": "test", "project": None}

    AgentMonitoringService.send_tool_metrics(tool_name, success=True, tool_metadata=tool_metadata)

    assert all(
        call.kwargs["attributes"]["project"] == "" for call in mock_send_count_metric.call_args_list
    ), "Project attribute must be '' when metadata project is None"


@patch.object(AgentMonitoringService, "send_count_metric")
@pytest.mark.parametrize(
    "slug, expected_slug",
    [(None, "mock_id"), ("", ""), ("existing_slug", "existing_slug")],
    ids=("nullable_slug", "empty_slug", "non_nullable_slug"),
)
def test_send_assistant_mngmnt_metric(mock_send_count_metric, mock_assistant, mock_user, slug, expected_slug):
    metric_name = "assistant_management"
    success = True
    additional_attributes = {"extra": "attribute"}

    mock_assistant.slug = slug
    mock_assistant.id = "mock_id"
    # Set mcp_servers to empty list to avoid MCP server metrics
    mock_assistant.mcp_servers = []

    # Include assistant_description in expected attributes
    expected_attributes = {
        "assistant_name": mock_assistant.name,
        "assistant_description": mock_assistant.description,
        "project": mock_assistant.project,
        "slug": expected_slug,
        "llm_model": mock_assistant.llm_model_type,
        "user_id": mock_user.id,
        "user_name": mock_user.name,
        "user_email": mock_user.username,
        "nested_assistants_count": len(mock_assistant.assistant_ids),
        **additional_attributes,
    }

    AgentMonitoringService.send_assistant_mngmnt_metric(
        metric_name, mock_assistant, success, mock_user, additional_attributes
    )

    mock_send_count_metric.assert_called_once_with(name=metric_name, attributes=expected_attributes)


@patch.object(AgentMonitoringService, "send_count_metric")
def test_send_assistant_mngmnt_metric_error(mock_send_count_metric, mock_assistant, mock_user):
    metric_name = "assistant_management"
    success = False
    additional_attributes = {"error_class": "Error"}

    # Set mcp_servers to empty list to avoid MCP server metrics
    mock_assistant.mcp_servers = []

    # Include assistant_description in expected attributes
    expected_attributes = {
        "assistant_name": mock_assistant.name,
        "assistant_description": mock_assistant.description,
        "project": mock_assistant.project,
        "slug": mock_assistant.slug,
        "llm_model": mock_assistant.llm_model_type,
        "user_id": mock_user.id,
        "user_name": mock_user.name,
        "user_email": mock_user.username,
        "nested_assistants_count": len(mock_assistant.assistant_ids),
        **additional_attributes,
    }

    AgentMonitoringService.send_assistant_mngmnt_metric(
        metric_name, mock_assistant, success, mock_user, additional_attributes
    )

    mock_send_count_metric.assert_called_once_with(name=metric_name + "_error", attributes=expected_attributes)
