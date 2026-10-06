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
    CustomWorkflowNode,
    InvalidCredentialsError,
    TruncatedOutputError,
    WorkflowAssistant,
    WorkflowNextState,
    WorkflowRetryPolicy,
    WorkflowState,
    WorkflowStateCondition,
    WorkflowStateSwitch,
    WorkflowStateSwitchCondition,
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


def test_custom_retry_on_retry_false_litellm_bad_request_error(mock_default_retry_on):
    """TC_WRP_001: litellm BadRequestError must not be retried — unrecoverable payload error."""
    from litellm.exceptions import BadRequestError

    exception_instance = BadRequestError(
        message="The image was specified using the image/png media type, but appears to be image/gif",
        model="anthropic.claude-haiku-3",
        llm_provider="bedrock",
    )

    result = WorkflowRetryPolicy.custom_retry_on(exception_instance)

    assert result is False


def test_custom_retry_on_retry_false_task_exception_wrapping_bad_request_error(mock_default_retry_on):
    """TC_WRP_002: TaskException wrapping litellm BadRequestError must not be retried."""
    from litellm.exceptions import BadRequestError
    from codemie.core.exceptions import TaskException

    original = BadRequestError(
        message="The image was specified using the image/png media type, but appears to be image/gif",
        model="anthropic.claude-haiku-3",
        llm_provider="bedrock",
    )
    exception_instance = TaskException("Graph node execution failed.", original_exc=original)

    result = WorkflowRetryPolicy.custom_retry_on(exception_instance)

    assert result is False


class TestWorkflowAssistant:
    """Tests for WorkflowAssistant model skill_ids field."""

    def test_skill_ids_defaults_to_empty_list(self):
        """Test that skill_ids defaults to an empty list when not provided."""
        assistant = WorkflowAssistant(id="asst1")

        assert assistant.skill_ids == []

    def test_skill_ids_can_be_set(self):
        """Test that skill_ids can be set with a list of skill IDs."""
        skill_ids = ["skill-abc123", "skill-def456"]
        assistant = WorkflowAssistant(id="asst1", skill_ids=skill_ids)

        assert assistant.skill_ids == skill_ids

    def test_skill_ids_serialization(self):
        """Test that skill_ids is included in model serialization."""
        skill_ids = ["skill-abc123"]
        assistant = WorkflowAssistant(id="asst1", skill_ids=skill_ids)

        data = assistant.model_dump()
        assert "skill_ids" in data
        assert data["skill_ids"] == skill_ids

    def test_skill_ids_deserialization_missing_uses_default(self):
        """Test that skill_ids defaults when missing from deserialized data."""
        data = {"id": "asst1"}
        assistant = WorkflowAssistant(**data)

        assert assistant.skill_ids == []


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

        mcp_server = MCPServerDetails(name="test_server", command="npx", args=["--arg1", "--arg2"])

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


class TestWorkflowPoolConfig:
    def test_default_values(self):
        from codemie.core.workflow_models import WorkflowPoolConfig

        cfg = WorkflowPoolConfig()
        assert cfg.enabled is False
        assert cfg.min_size == 2
        assert cfg.max_size == 5
        assert cfg.refill_interval_seconds == 30

    def test_min_size_lower_bound(self):
        from codemie.core.workflow_models import WorkflowPoolConfig

        with pytest.raises(ValidationError):
            WorkflowPoolConfig(min_size=0)

    def test_min_size_upper_bound(self):
        from codemie.core.workflow_models import WorkflowPoolConfig

        with pytest.raises(ValidationError):
            WorkflowPoolConfig(min_size=21)

    def test_max_size_upper_bound(self):
        from codemie.core.workflow_models import WorkflowPoolConfig

        with pytest.raises(ValidationError):
            WorkflowPoolConfig(max_size=51)

    def test_refill_interval_lower_bound(self):
        from codemie.core.workflow_models import WorkflowPoolConfig

        with pytest.raises(ValidationError):
            WorkflowPoolConfig(refill_interval_seconds=4)


class TestWorkflowStateWorkflowId:
    def test_workflow_id_is_valid_fourth_discriminant(self):
        state = WorkflowState(
            id="s1",
            workflow_id="wf-123",
            next=WorkflowNextState(state_id="s2"),
        )
        assert state.workflow_id == "wf-123"

    def test_workflow_id_mutual_exclusion_with_assistant_id(self):
        with pytest.raises(ValidationError):
            WorkflowState(
                id="s1",
                workflow_id="wf-123",
                assistant_id="asst-1",
                next=WorkflowNextState(state_id="s2"),
            )

    def test_workflow_id_mutual_exclusion_with_tool_id(self):
        with pytest.raises(ValidationError):
            WorkflowState(
                id="s1",
                workflow_id="wf-123",
                tool_id="tool-1",
                next=WorkflowNextState(state_id="s2"),
            )

    def test_workflow_id_mutual_exclusion_with_custom_node_id(self):
        with pytest.raises(ValidationError):
            WorkflowState(
                id="s1",
                workflow_id="wf-123",
                custom_node_id="node-1",
                next=WorkflowNextState(state_id="s2"),
            )

    def test_no_discriminant_still_raises(self):
        with pytest.raises(ValidationError):
            WorkflowState(id="s1", next=WorkflowNextState(state_id="s2"))


class TestWorkflowNextStateMutualExclusion:
    """Regression for CR-008: a model_validator(mode='after') enforcing mutual exclusion
    between condition/switch and state_id/state_ids was deleted from WorkflowNextState with
    no replacement; contradictory next-state configs used to be rejected at construction
    time and now silently pass.

    The iter_key-related checks were briefly dropped to permit state_ids+iter_key, but the
    runtime never implemented that combination: `_handle_multiple_states` in workflow.py
    fans out over `state_ids` unconditionally and never looks at `iter_key`, so the
    combination silently did nothing instead of iterating. All four original checks are
    restored."""

    def test_condition_and_switch_together_raises(self):
        with pytest.raises(ValidationError, match="Only one of 'condition' or 'switch'"):
            WorkflowNextState(
                condition=WorkflowStateCondition(expression="x == 1", then="s1", otherwise="s2"),
                switch=WorkflowStateSwitch(
                    cases=[WorkflowStateSwitchCondition(condition="x == 1", state_id="s1")], default="s2"
                ),
            )

    def test_state_id_and_state_ids_together_raises(self):
        with pytest.raises(ValidationError, match="Only one of 'state_id' or 'state_ids'"):
            WorkflowNextState(state_id="s1", state_ids=["s2", "s3"])

    def test_condition_alone_is_valid(self):
        next_state = WorkflowNextState(condition=WorkflowStateCondition(expression="x == 1", then="s1", otherwise="s2"))
        assert next_state.condition is not None
        assert next_state.switch is None

    def test_state_ids_with_iter_key_raises(self):
        with pytest.raises(ValidationError, match="Cannot iterate over sequence of states"):
            WorkflowNextState(state_ids=["s1", "s2"], iter_key=".")

    def test_state_ids_with_condition_raises(self):
        with pytest.raises(ValidationError, match="Cannot use `condition` or `switch`"):
            WorkflowNextState(
                state_ids=["s1", "s2"],
                condition=WorkflowStateCondition(expression="x == 1", then="s1", otherwise="s2"),
            )

    def test_state_ids_with_switch_raises(self):
        with pytest.raises(ValidationError, match="Cannot use `condition` or `switch`"):
            WorkflowNextState(
                state_ids=["s1", "s2"],
                switch=WorkflowStateSwitch(
                    cases=[WorkflowStateSwitchCondition(condition="x == 1", state_id="s1")], default="s2"
                ),
            )


class TestCustomWorkflowNodeIdBackfill:
    """Regression for CR-007: a model_validator(mode='before') that back-filled
    custom_node_id from id when absent was deleted with no replacement; legacy
    workflow YAML custom_nodes entries with id but no custom_node_id used to fail
    pydantic validation on load."""

    def test_custom_node_id_backfilled_from_id_when_absent(self):
        node = CustomWorkflowNode(id="legacy-node-1")
        assert node.custom_node_id == "legacy-node-1"

    def test_explicit_custom_node_id_is_preserved(self):
        node = CustomWorkflowNode(id="node-1", custom_node_id="explicit-id")
        assert node.custom_node_id == "explicit-id"

    def test_empty_custom_node_id_is_backfilled_from_id(self):
        node = CustomWorkflowNode(id="node-1", custom_node_id="")
        assert node.custom_node_id == "node-1"
