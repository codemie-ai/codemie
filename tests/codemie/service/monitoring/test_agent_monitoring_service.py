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
def test_send_tool_metrics_writes_assistant_and_workflow_ids_when_the_run_has_them(mock_send_count_metric):
    AgentMonitoringService.send_tool_metrics("t", True, tool_metadata={"assistant_id": "asst-1", "workflow_id": "wf-1"})

    for call in mock_send_count_metric.call_args_list:
        assert call.kwargs["attributes"]["assistant_id"] == "asst-1"
        assert call.kwargs["attributes"]["workflow_id"] == "wf-1"


@patch.object(AgentMonitoringService, "send_count_metric")
def test_send_tool_metrics_leaves_empty_ids_out(mock_send_count_metric):
    AgentMonitoringService.send_tool_metrics("t", False, tool_metadata={"assistant_id": "", "workflow_id": None})
    AgentMonitoringService.send_tool_metrics("t", True)

    for call in mock_send_count_metric.call_args_list:
        assert "assistant_id" not in call.kwargs["attributes"]
        assert "workflow_id" not in call.kwargs["attributes"]


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


_DIMENSIONS = {
    "assistant_name": "a",
    "llm_model": "m",
    "user_name": "u",
    "user_id": "uid",
    "project": "p",
}
_SCRIPT_ARGS = {"tool_name": "t", "tool_type": "internal", "dimensions": _DIMENSIONS}


@pytest.mark.parametrize(
    ("kind", "metric", "extra"),
    [
        ("success", "codemie_tools_usage_total", {"status": "success"}),
        ("abandoned", "codemie_tools_usage_total", {"status": "abandoned"}),
        ("error", "codemie_tools_usage_errors_total", {"error_code": "tool_failed", "error_class": "ValueError"}),
        ("refused", "codemie_script_tool_calls_refused_total", {"error_code": "tool_failed"}),
    ],
)
@patch.object(AgentMonitoringService, "send_count_metric")
def test_send_script_tool_call_metrics_writes_one_record_without_tokens(mock_send, kind, metric, extra):
    AgentMonitoringService.send_script_tool_call_metrics(
        kind=kind, error_code="tool_failed", error_class="ValueError", **_SCRIPT_ARGS
    )

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["name"] == metric
    attributes = mock_send.call_args.kwargs["attributes"]
    if kind == "refused":
        assert "invoked_by" not in attributes
    else:
        assert attributes["invoked_by"] == "script"
    assert attributes["base_tool_name"] == "t"
    assert attributes["user_id"] == "uid"
    for key, value in extra.items():
        assert attributes[key] == value
    if kind in ("success", "abandoned"):
        assert "error_code" not in attributes
    if kind != "error":
        assert "error_class" not in attributes


@pytest.mark.parametrize("kind", ["success", "abandoned", "error", "refused"])
@patch.object(AgentMonitoringService, "send_count_metric")
def test_send_script_tool_call_metrics_writes_every_dimension_of_the_run(mock_send, kind):
    dimensions = {**_DIMENSIONS, "assistant_id": "virtual", "workflow_id": "wf-1", "skill_id": "sk1"}

    AgentMonitoringService.send_script_tool_call_metrics(
        kind=kind, tool_name="t", tool_type="internal", dimensions=dimensions, error_code="tool_failed"
    )

    attributes = mock_send.call_args.kwargs["attributes"]
    for key, value in dimensions.items():
        assert attributes[key] == value


@patch.object(AgentMonitoringService, "send_count_metric")
def test_send_script_tool_call_metrics_does_not_change_the_dimensions_it_is_given(mock_send):
    dimensions = dict(_DIMENSIONS)

    AgentMonitoringService.send_script_tool_call_metrics(kind="success", **{**_SCRIPT_ARGS, "dimensions": dimensions})

    assert dimensions == _DIMENSIONS


@patch.object(AgentMonitoringService, "send_count_metric")
def test_an_error_without_an_exception_has_a_code_but_no_class(mock_send):
    AgentMonitoringService.send_script_tool_call_metrics(kind="error", error_code="timeout", **_SCRIPT_ARGS)

    attributes = mock_send.call_args.kwargs["attributes"]
    assert attributes["error_code"] == "timeout"
    assert "error_class" not in attributes
