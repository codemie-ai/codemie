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

import httpx
import pytest
import requests
from unittest.mock import MagicMock

from pydantic import ValidationError
from codemie.core.workflow_models.constants import (
    RETRY_POLICY_DEFAULT_INITIAL_INTERVAL,
    RETRY_POLICY_DEFAULT_MAX_ATTEMPTS,
    RETRY_POLICY_DEFAULT_MAX_INTERVAL,
    RETRY_POLICY_DEFAULT_BACKOFF_FACTOR,
)
from codemie.core.workflow_models.workflow_models import (
    InvalidCredentialsError,
    TruncatedOutputError,
    WorkflowNextState,
    WorkflowRetryPolicy,
    WorkflowState,
    WorkflowTool,
)
from codemie.core.workflow_models.workflow_config import WorkflowConfig


class TestWorkflowState:
    def test_validation_with_assistant_id(self):
        assert WorkflowState(
            id="state1", assistant_id="assistant1", task="task1", next=WorkflowNextState(state_id="state2")
        )

    def test_validation_with_custom_node_id(self):
        assert WorkflowState(
            id="state1", custom_node_id="node1", task="task1", next=WorkflowNextState(state_id="state2")
        )

    def test_validation_with_tool_id(self):
        assert WorkflowState(id="state1", tool_id="tool1", task="task1", next=WorkflowNextState(state_id="state2"))

    def test_validation_with_undefined_type(self):
        expected_error = "1 validation error for WorkflowState\nnext\n  Field required"

        with pytest.raises(ValidationError) as valid_err:
            WorkflowState(id="state1", assistant_id="assistant1", task="task1")

        assert expected_error in str(valid_err.value)

    def test_interrupt_before_default(self):
        state = WorkflowState(id="state1", next=WorkflowNextState(state_id="state2"), assistant_id="assistant1")
        assert state.interrupt_before is False

    def test_interrupt_before_new_key_true(self):
        state = WorkflowState(
            id="state1", next=WorkflowNextState(state_id="state2"), assistant_id="assistant1", interrupt_before=True
        )
        assert state.interrupt_before is True

    def test_interrupt_before_new_key_false(self):
        state = WorkflowState(
            id="state1", next=WorkflowNextState(state_id="state2"), assistant_id="assistant1", interrupt_before=False
        )
        assert state.interrupt_before is False

    def test_interrupt_before_old_key_true_populates_new(self):
        # noinspection PyArgumentList
        state = WorkflowState(
            id="state1",
            next=WorkflowNextState(state_id="state2"),
            assistant_id="assistant1",
            wait_for_user_confirmation=True,  # Deprecated
        )
        assert state.interrupt_before is True

    def test_interrupt_before_old_key_false_populates_new(self):
        # noinspection PyArgumentList
        state = WorkflowState(
            id="state1",
            next=WorkflowNextState(state_id="state2"),
            assistant_id="assistant1",
            wait_for_user_confirmation=False,  # Deprecated
        )
        assert state.interrupt_before is False

    def test_interrupt_before_both_keys_raises_error(self):
        expected_error = "Only one of 'interrupt_before' or 'wait_for_user_confirmation' must be provided"

        with pytest.raises(ValidationError) as valid_err:
            # noinspection PyArgumentList
            WorkflowState(
                id="state1",
                next=WorkflowNextState(state_id="state2"),
                assistant_id="assistant1",
                interrupt_before=True,
                wait_for_user_confirmation=True,  # Deprecated
            )
        assert expected_error in str(valid_err.value)


def test_workflow_retry_policy_default_values():
    policy = WorkflowConfig._get_default_retry_policy()

    assert policy.initial_interval == RETRY_POLICY_DEFAULT_INITIAL_INTERVAL
    assert policy.backoff_factor == RETRY_POLICY_DEFAULT_BACKOFF_FACTOR
    assert policy.max_interval == RETRY_POLICY_DEFAULT_MAX_INTERVAL
    assert policy.max_attempts == RETRY_POLICY_DEFAULT_MAX_ATTEMPTS


@pytest.fixture
def mock_default_retry_on(mocker) -> MagicMock:
    yield mocker.patch("codemie.core.workflow_models.workflow_models.default_retry_on")


@pytest.mark.parametrize(
    "exception_instance", [InvalidCredentialsError("Invalid Credentials"), TruncatedOutputError(" Truncated Error")]
)
def test_custom_retry_on_retry_false_exceptions(mock_default_retry_on, exception_instance):
    expected_retry_decision = False

    result = WorkflowRetryPolicy.custom_retry_on(exception_instance)

    assert result == expected_retry_decision


def _get_http_status_error_instance(status_code):
    response = httpx.Response(status_code=status_code)
    exception_instance = httpx.HTTPStatusError("error", request=MagicMock(), response=response)
    return exception_instance


def _get_http_error_instance(status_code):
    response = requests.Response()
    response.status_code = status_code
    exception_instance = requests.HTTPError(response=response)
    return exception_instance


@pytest.mark.parametrize("default_retry_on_decision", (False, True))
@pytest.mark.parametrize("exception_instance_getter", (_get_http_status_error_instance, _get_http_error_instance))
@pytest.mark.parametrize("status_code", [401, 403, 404], ids=("Unauthorized", "Forbidden", "Not Found"))
def test_custom_retry_on_retry_false_custom_http_exceptions(
    mock_default_retry_on, status_code, exception_instance_getter, default_retry_on_decision
):
    exception_instance = exception_instance_getter(status_code)
    expected_retry_decision = False
    mock_default_retry_on.return_value = default_retry_on_decision

    result = WorkflowRetryPolicy.custom_retry_on(exception_instance)

    assert result == expected_retry_decision


@pytest.mark.parametrize("exception_instance_getter", (_get_http_status_error_instance, _get_http_error_instance))
@pytest.mark.parametrize("status_code", [400, 405, 409], ids=("Bad Request", "Method Not Allowed", "Conflict"))
def test_custom_retry_on_retry_false_http_exceptions(status_code, exception_instance_getter):
    exception_instance = exception_instance_getter(status_code)
    expected_retry_decision = False
    result = WorkflowRetryPolicy.custom_retry_on(exception_instance)

    assert result == expected_retry_decision


@pytest.mark.parametrize("exception_instance_getter", (_get_http_status_error_instance, _get_http_error_instance))
@pytest.mark.parametrize("status_code", [500, 501, 502], ids=("Internal Server Error", "Not Implemented", "Bad Gateay"))
def test_custom_retry_on_retry_true_http_exceptions(status_code, exception_instance_getter):
    exception_instance = exception_instance_getter(status_code)
    expected_retry_decision = True

    result = WorkflowRetryPolicy.custom_retry_on(exception_instance)

    assert result == expected_retry_decision


class TestWorkflowTool:
    """Tests for WorkflowTool model and its configuration fields."""

    def test_workflow_tool_basic_creation(self):
        """Test basic WorkflowTool creation with required fields."""
        tool = WorkflowTool(id="test_tool", tool="test_tool_name")

        assert tool.id == "test_tool"
        assert tool.tool == "test_tool_name"
        # Check default values
        assert tool.resolve_dynamic_values_in_response is False
        assert tool.trace is False
        assert tool.mcp_server is None

    def test_workflow_tool_resolve_dynamic_values_in_response_default(self):
        """Test that resolve_dynamic_values_in_response defaults to False."""
        tool = WorkflowTool(id="test_tool", tool="test_tool_name")

        assert tool.resolve_dynamic_values_in_response is False

    def test_workflow_tool_resolve_dynamic_values_in_response_explicit_true(self):
        """Test setting resolve_dynamic_values_in_response to True."""
        tool = WorkflowTool(id="test_tool", tool="test_tool_name", resolve_dynamic_values_in_response=True)

        assert tool.resolve_dynamic_values_in_response is True

    def test_workflow_tool_resolve_dynamic_values_in_response_explicit_false(self):
        """Test setting resolve_dynamic_values_in_response to False."""
        tool = WorkflowTool(id="test_tool", tool="test_tool_name", resolve_dynamic_values_in_response=False)

        assert tool.resolve_dynamic_values_in_response is False

    def test_workflow_tool_serialization_includes_new_field(self):
        """Test that serialization includes the new field."""
        tool = WorkflowTool(id="test_tool", tool="test_tool_name", resolve_dynamic_values_in_response=True)

        # Test model_dump includes the field
        data = tool.model_dump()
        assert "resolve_dynamic_values_in_response" in data
        assert data["resolve_dynamic_values_in_response"] is True

    def test_workflow_tool_deserialization_handles_new_field(self):
        """Test that deserialization handles the new field correctly."""
        # Test with field present
        data_with_field = {"id": "test_tool", "tool": "test_tool_name", "resolve_dynamic_values_in_response": True}

        tool = WorkflowTool(**data_with_field)
        assert tool.resolve_dynamic_values_in_response is True

        # Test without field (should use default)
        data_without_field = {"id": "test_tool", "tool": "test_tool_name"}

        tool = WorkflowTool(**data_without_field)
        assert tool.resolve_dynamic_values_in_response is False

    def test_workflow_tool_all_optional_fields(self):
        """Test WorkflowTool with all optional fields set."""
        from codemie.rest_api.models.assistant import MCPServerDetails

        mcp_server = MCPServerDetails(name="test_server", command="test_command", args=["--arg1", "--arg2"])

        tool = WorkflowTool(
            id="test_tool",
            tool="test_tool_name",
            integration_alias="test_alias",
            tool_result_json_pointer="/result/data",
            trace=True,
            mcp_server=mcp_server,
            resolve_dynamic_values_in_response=True,
        )

        assert tool.id == "test_tool"
        assert tool.tool == "test_tool_name"
        assert tool.integration_alias == "test_alias"
        assert tool.tool_result_json_pointer == "/result/data"
        assert tool.trace is True
        assert tool.mcp_server is not None
        assert tool.resolve_dynamic_values_in_response is True
