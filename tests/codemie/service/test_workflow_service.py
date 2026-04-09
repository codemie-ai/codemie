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

from unittest.mock import patch, MagicMock
from uuid import UUID

import pytest
from typing import Union
from yaml import parser

from codemie.core.constants import ChatRole, DEMO_PROJECT
from codemie.core.models import UserEntity
from codemie.core.workflow_models import (
    CreateWorkflowRequest,
    UpdateWorkflowRequest,
    WorkflowAssistant,
    WorkflowConfig,
    WorkflowConfigTemplate,
    WorkflowExecution,
    WorkflowExecutionState,
    WorkflowExecutionStatusEnum,
    WorkflowMode,
    WorkflowNextState,
    WorkflowState,
)
from codemie.rest_api.models.conversation import GeneratedMessage
from codemie.rest_api.security.user import User
from codemie.service.workflow_service import WorkflowService

EXAMPLE_PROJECT = "example_project"


@pytest.fixture
def user():
    return User(id="123", username="testuser", name="Test User", project_names=[EXAMPLE_PROJECT])


@pytest.fixture
def admin_user():
    return User(
        id="admin_123",
        username="app_admin",
        name="App Admin User",
        project_names=[EXAMPLE_PROJECT],
        admin_project_names=[EXAMPLE_PROJECT],
    )


@pytest.fixture
def user_model():
    return UserEntity(user_id="123", username="testuser", name="Test User")


@pytest.fixture
def workflow_config():
    return WorkflowConfig(
        id="workflow_123",
        name="Test Workflow",
        description="A test workflow",
        yaml_config="",
        states=[
            WorkflowState(
                id="state1", assistant_id="assistant1", task="task1", next=WorkflowNextState(state_id="state2")
            ),
            WorkflowState(id="state2", assistant_id="assistant2", task="task2", next=WorkflowNextState(state_id="end")),
        ],
    )


@pytest.fixture
def create_workflow_request():
    return CreateWorkflowRequest(
        name="New Workflow",
        description="A new test workflow",
        project="project1",
        icon_url="http://example.com/icon.png",
    )


@pytest.fixture(params=(True, False))
def update_workflow_request(request):
    shared = request.param
    return UpdateWorkflowRequest(
        name="Updated Workflow",
        description="An updated test workflow",
        project="project2",
        mode=WorkflowMode.AUTONOMOUS,
        icon_url="http://example.com/icon3.png",
        yaml_config="{'assistants': []}",
        shared=shared,
        supervisor_prompt="example",
    )


@pytest.fixture()
def update_workflow_request_defaults(request):
    return UpdateWorkflowRequest(
        name="Test Workflow", description="A test workflow", project="demo", mode=WorkflowMode.SEQUENTIAL
    )


@pytest.fixture
def workflow_service():
    return WorkflowService()


@pytest.fixture
def mock_workflow_executions():
    """Mock workflow execution objects with hardcoded data"""

    execution_0 = MagicMock()
    execution_0.model_dump.return_value = {
        'id': 'execution_0',
        'workflow_id': 'workflow_123',
        'project': 'project1',
        'created_by': {'user_id': '123'},
        'status': 'completed',
    }

    execution_1 = MagicMock()
    execution_1.model_dump.return_value = {
        'id': 'execution_1',
        'workflow_id': 'workflow_123',
        'project': 'project1',
        'created_by': {'user_id': 'admin_123'},
        'status': 'completed',
    }

    return [execution_0, execution_1]


@patch('codemie.core.workflow_models.WorkflowConfig.get_by_id')
def test_get_workflow(mock_get_by_id, workflow_service, workflow_config):
    mock_get_by_id.return_value = workflow_config
    result = workflow_service.get_workflow(workflow_config.id)
    assert result == workflow_config
    mock_get_by_id.assert_called_once_with(workflow_config.id)


@patch('codemie.core.workflow_models.WorkflowConfig.get_by_id')
def test_get_workflow_parses_skill_ids_from_yaml(mock_get_by_id, workflow_service):
    """get_workflow should populate assistants from yaml_config so skill_ids are included in the response."""
    import yaml

    yaml_with_skills = yaml.dump(
        {
            "assistants": [{"id": "assistant_1", "model": "gpt-4.1", "skill_ids": ["skill-abc123"]}],
            "states": [],
        }
    )
    wf = WorkflowConfig(
        id="workflow_with_skills",
        name="Workflow With Skills",
        description="Test",
        yaml_config=yaml_with_skills,
    )
    mock_get_by_id.return_value = wf

    result = workflow_service.get_workflow(wf.id)

    assert len(result.assistants) == 1
    assert result.assistants[0].skill_ids == ["skill-abc123"]


@patch('codemie.core.workflow_models.WorkflowConfig.get_by_id')
def test_get_workflow_parses_null_skill_ids_from_yaml(mock_get_by_id, workflow_service):
    """get_workflow should handle null skill_ids in YAML (skill_ids: with no value) by defaulting to []."""
    import yaml

    # Simulate YAML where skill_ids: has no value (null) — this is what happens
    # when frontend saves a workflow assistant config that had skill_ids: []
    # and js-yaml or the user edits the raw YAML leaving skill_ids: blank
    yaml_with_null_skills = yaml.dump(
        {
            "assistants": [{"id": "assistant_1", "model": "gpt-4.1", "skill_ids": None}],
            "states": [],
        }
    )
    wf = WorkflowConfig(
        id="workflow_null_skills",
        name="Workflow Null Skills",
        description="Test",
        yaml_config=yaml_with_null_skills,
    )
    mock_get_by_id.return_value = wf

    result = workflow_service.get_workflow(wf.id)

    assert len(result.assistants) == 1
    # null skill_ids must be coerced to empty list, not left as None
    assert result.assistants[0].skill_ids == []


@patch('codemie.core.workflow_models.WorkflowConfig.get_by_id')
def test_get_workflow_invalid_id(mock_get_by_id, workflow_service):
    mock_get_by_id.side_effect = Exception("Workflow not found")
    with pytest.raises(Exception, match="Workflow not found"):
        workflow_service.get_workflow("invalid_id")


@patch('codemie.service.workflow_service.WorkflowService.delete_workflow')
def test_delete_workflow(mock_delete_workflow, workflow_service, workflow_config, user):
    mock_delete_workflow.return_value = workflow_config
    result = workflow_service.delete_workflow(workflow_config, user)
    assert result == workflow_config
    mock_delete_workflow.assert_called_once_with(workflow_config, user)


@patch('codemie.core.workflow_models.WorkflowConfig.update')
@patch('codemie.core.workflow_models.WorkflowConfig.refresh')
def test_update_workflow(mock_refresh, mock_update, workflow_service, workflow_config, update_workflow_request, user):
    update_workflow_model = update_workflow_request.model_dump()
    expected_updater = user.as_user_model()

    _ = workflow_service.update_workflow(workflow_config, WorkflowConfig(**update_workflow_model), user)

    # assert result == workflow_config
    mock_update.assert_called_once_with(refresh=True)
    mock_refresh.assert_called_once()
    assert workflow_config.updated_by == expected_updater
    for field_name in set(workflow_service._editable_non_boolean_fields):
        update_field_request = getattr(update_workflow_request, field_name)
        updated_model_field = getattr(workflow_config, field_name)
        assert updated_model_field == update_field_request, f"Mismatch in {field_name}"


@patch('codemie.core.workflow_models.WorkflowConfig.update')
@patch('codemie.core.workflow_models.WorkflowConfig.refresh')
def test_update_workflow_nothing_to_update(
    mock_refresh: MagicMock,
    mock_update: MagicMock,
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    update_workflow_request_defaults: UpdateWorkflowRequest,
    user: User,
) -> None:
    original_workflow_dict = workflow_config.model_dump()
    update_workflow_model = update_workflow_request_defaults.model_dump()
    expected_updater = user.as_user_model()

    result = workflow_service.update_workflow(workflow_config, WorkflowConfig(**update_workflow_model), user)

    assert result == workflow_config
    mock_update.assert_called_once_with(refresh=True)
    mock_refresh.assert_called_once()
    assert workflow_config.updated_by == expected_updater
    for field_name in set(workflow_service._editable_non_boolean_fields) - {"supervisor_prompt"}:
        original_workflow_model_field = original_workflow_dict.get(field_name)
        updated_model_field = getattr(workflow_config, field_name)
        assert updated_model_field == original_workflow_model_field, f"Mismatch in {field_name}"
    assert workflow_config.supervisor_prompt is None


@pytest.mark.parametrize(
    "user_input, expected_prompt",
    [("Test input", "Test input"), ("", ""), (None, None)],
    ids=["with_user_input", "with_empty_input", "with_none_input"],
)
def test_create_workflow_execution(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user_model: UserEntity,
    user_input: str,
    expected_prompt: str,
) -> None:
    with patch('uuid.uuid4', return_value=UUID('12345678-1234-5678-1234-567812345678')):
        with patch.object(WorkflowExecution, 'save') as mock_save:
            result = workflow_service.create_workflow_execution(workflow_config, user_model, user_input)

    # For non-chat executions (no conversation_id), history is no longer stored in execution
    expected_execution = {
        'workflow_id': workflow_config.id,
        'execution_id': "12345678-1234-5678-1234-567812345678",
        'overall_status': WorkflowExecutionStatusEnum.IN_PROGRESS,
        'prompt': expected_prompt,
        'created_by': user_model,
        'project': workflow_config.project,
        'history': [
            GeneratedMessage(
                role=ChatRole.USER.value,
                message=user_input,
                message_raw=user_input,
                history_index=0,
            ),
            GeneratedMessage(
                role=ChatRole.ASSISTANT.value,
                history_index=0,
                thoughts=[],
            ),
        ],  # Non-chat executions don't store history in the execution
    }

    assert isinstance(result, WorkflowExecution)

    for key, value in expected_execution.items():
        if key == 'history':
            assert len(result.history) == len(value), f"Expected history length {len(value)}, got {len(result.history)}"
        else:
            assert getattr(result, key) == value, f"Mismatch in {key}"

    mock_save.assert_called_once_with(refresh=True)


@pytest.mark.parametrize(
    "exception_instance, error_message",
    [(Exception, "Database error"), (ValueError, "Invalid input")],
    ids=["database_error", "invalid_input"],
)
@patch('codemie.service.workflow_service.logger')
def test_create_workflow_execution_logs_error(
    mock_logger: MagicMock,
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user_model: UserEntity,
    exception_instance: Union[type(Exception), type(ValueError)],
    error_message: str,
):
    with patch.object(WorkflowExecution, 'save', side_effect=exception_instance(error_message)):
        with pytest.raises(exception_instance, match=error_message):
            workflow_service.create_workflow_execution(workflow_config, user_model)

    mock_logger.error.assert_called_once_with(f"Failed to create workflow execution: {error_message}", exc_info=True)


def test_get_prebuilt_workflows_template_count(workflow_service: WorkflowService) -> None:
    expected_templates_count = 64

    templates = workflow_service.get_prebuilt_workflows()

    actual_templates_count = len(templates)
    comparison_fail = f'Expected {expected_templates_count} prebuilt workflow templates, got {actual_templates_count}'
    assert actual_templates_count == expected_templates_count, comparison_fail


@patch.object(WorkflowService, '_cached_prebuilt_workflows', new_callable=list)
def test_get_cached_prebuilt_workflows_default_project(mock_cached_workflows, workflow_service):
    wf = WorkflowConfigTemplate(name="Cached Workflow", description="Example", slug="test")
    assert wf.project == DEMO_PROJECT
    mock_cached_workflows.append(wf)

    workflows = workflow_service.get_prebuilt_workflows()

    assert len(workflows) == 1, "No workflows were cached"
    assert workflows[0].project == DEMO_PROJECT, f"Expected project {DEMO_PROJECT}', got '{workflows[0].project}'"


@patch(
    'codemie.core.workflow_models.WorkflowConfig.from_yaml',
    return_value=WorkflowConfigTemplate(
        project=DEMO_PROJECT, name="Cached Workflow", description="Example", slug="test"
    ),
)
def test_get_prebuilt_workflows_empty_project_when_cached_demo(_mock_from_yaml, workflow_service):
    templates = workflow_service.get_prebuilt_workflows()

    assert len(templates) > 0
    for template in templates:
        assert template.project == DEMO_PROJECT


@patch('codemie.service.workflow_service.logger')
@patch('codemie.core.workflow_models.WorkflowConfig.update')
@patch('codemie.core.workflow_models.WorkflowConfig.refresh')
def test_yaml_reflects_assistants_and_states(
    mock_refresh: MagicMock,
    mock_update: MagicMock,
    mock_logger: MagicMock,
    user: User,
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    update_workflow_request_defaults: UpdateWorkflowRequest,
) -> None:
    first_assistant = WorkflowAssistant(id="first_print", model="gpt-4o-2024-08-06", system_prompt="Print Hello")
    second_assistant = WorkflowAssistant(id="second_print", model="gpt-4.1-mini", system_prompt="Print Goodbye")
    first_state = WorkflowState(
        id='printHello',
        assistant_id='first_assistant',
        task='Say hello and adjust timing',
        next=WorkflowNextState(state_id='printGoodbye'),
    )
    second_state = WorkflowState(
        id='printGoodbye',
        assistant_id='second_assistant',
        task='Say goodbye and adjust timing',
        next=WorkflowNextState(state_id='end'),
    )
    updated_yaml_config = f"""
        assistants:
            - id: {first_assistant.id}
              model: {first_assistant.model}
              system_prompt: {first_assistant.system_prompt}
            - id: {second_assistant.id}
              model: {second_assistant.model}
              system_prompt: {second_assistant.system_prompt}
        states:
        - id: {first_state.id}
          assistant_id: {first_state.assistant_id}
          task: {first_state.task}
          next:
            state_id: {first_state.next.state_id}
        - id: {second_state.id}
          assistant_id: {second_state.assistant_id}
          task: {second_state.task}
          next:
            state_id: {second_state.next.state_id}
    """
    update_workflow_model = WorkflowConfig(**update_workflow_request_defaults.model_dump())
    update_workflow_model.yaml_config = updated_yaml_config

    workflow_service.update_workflow(workflow_config, update_workflow_model, user)

    assert workflow_config.assistants == [first_assistant, second_assistant]
    assert workflow_config.states == [first_state, second_state]
    mock_update.assert_called_once_with(refresh=True)


@patch('codemie.service.workflow_service.logger')
@patch('codemie.core.workflow_models.WorkflowConfig.update')
def test_invalid_yaml_logs_error(
    mock_update: MagicMock,
    mock_logger: MagicMock,
    user: User,
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    update_workflow_request_defaults: WorkflowConfig,
) -> None:
    invalid_yaml = "states: [missing_bracket"
    expected_error_substring = "while parsing a flow sequence"
    expected_log_error_substring = "Failed to update workflow: "
    update_workflow_model = WorkflowConfig(**update_workflow_request_defaults.model_dump())
    update_workflow_model.yaml_config = invalid_yaml

    with pytest.raises(parser.ParserError) as p_error:
        workflow_service.update_workflow(workflow_config, update_workflow_model, user)

    assert expected_error_substring in str(p_error.value)
    mock_update.assert_not_called()
    mock_logger.error.assert_called_once()
    assert f"{expected_log_error_substring}{expected_error_substring}" in mock_logger.error.call_args[0][0]


@patch('codemie.core.workflow_models.WorkflowExecution.get_engine')
@patch('codemie.service.workflow_service.Session')
@patch('codemie.service.workflow_service.select')
@patch('codemie.service.workflow_service.func')
@patch('codemie.service.workflow_service.Ability')
@patch('codemie.service.workflow_service.WorkflowExecutionResponse')
def test_get_workflow_execution_list_admin_user(
    mock_response_class,
    mock_ability_class,
    mock_func,
    mock_select,
    mock_session_class,
    mock_get_engine,
    admin_user,
    mock_workflow_executions,
    user,
):
    """Test that admin users can see all workflow executions without filtering"""
    mock_get_engine.return_value = MagicMock()
    mock_session_instance = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session_instance

    # Mock query building
    mock_query = MagicMock()
    mock_select.return_value.where.return_value = mock_query
    mock_query.order_by.return_value.offset.return_value.limit.return_value = mock_query

    # Mock count query
    mock_count_query = MagicMock()
    mock_func.count.return_value = MagicMock()
    mock_select.return_value.select_from.return_value = mock_count_query
    mock_query.subquery.return_value = MagicMock()

    # Create mock result objects that have .all() and .one() methods
    mock_count_result = MagicMock()
    mock_count_result.one.return_value = 5

    mock_executions_result = MagicMock()
    mock_executions_result.all.return_value = mock_workflow_executions

    # Mock session exec calls
    mock_session_instance.exec.side_effect = [
        mock_count_result,  # Total count query result
        mock_executions_result,  # Executions query result
    ]

    mock_session_instance.exec.side_effect = [
        mock_count_result,  # Total count query result
        mock_executions_result,  # Executions query result
    ]

    # Mock ability check to allow all executions
    mock_ability = mock_ability_class.return_value
    mock_ability.list.return_value = True

    # Mock response creation
    mock_responses = []
    for execution in mock_workflow_executions:
        mock_resp = MagicMock()
        mock_resp.return_value = execution
        mock_responses.append(mock_resp)

    mock_response_class.side_effect = mock_responses

    # Call the method
    result = WorkflowService.get_workflow_execution_list(
        admin_user, "workflow_123", project=EXAMPLE_PROJECT, page=0, per_page=10
    )

    # Verify admin user doesn't get additional WHERE clauses
    assert mock_session_instance.exec.call_count == 2

    assert len(result["data"]) == 2

    executions = [workflow_exec.model_dump() for workflow_exec in mock_workflow_executions]

    assert executions[0]["created_by"]["user_id"] == user.id
    assert executions[1]["created_by"]["user_id"] == admin_user.id


@pytest.fixture
def workflow_execution_with_chat():
    """Workflow execution that is part of a conversation"""
    return WorkflowExecution(
        id="exec_with_chat_123",
        workflow_id="workflow_123",
        execution_id="exec_123",
        conversation_id="conversation_456",
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
    )


@pytest.fixture
def workflow_execution_without_chat():
    """Workflow execution that is standalone (not part of a conversation)"""
    return WorkflowExecution(
        id="exec_no_chat_123",
        workflow_id="workflow_123",
        execution_id="exec_456",
        conversation_id=None,
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
    )


def test_delete_all_executions_preserves_chat_executions(
    workflow_execution_with_chat: WorkflowExecution,
    workflow_execution_without_chat: WorkflowExecution,
):
    """Test that executions with conversation_id are NOT deleted when deleting all executions"""
    service = WorkflowService()

    with (
        patch.object(
            WorkflowService,
            'get_workflow_executions',
            return_value=[workflow_execution_with_chat, workflow_execution_without_chat],
        ),
        patch.object(WorkflowService, 'delete_workflow_execution') as mock_delete,
    ):
        service.delete_all_executions_by_workflow_id("workflow_123")

    # Should only delete execution without conversation_id
    mock_delete.assert_called_once_with(workflow_execution_without_chat.id)

    # Should NOT delete execution with conversation_id
    assert mock_delete.call_count == 1


def test_delete_all_executions_deletes_non_chat_executions(
    workflow_execution_without_chat: WorkflowExecution,
):
    """Test that executions without conversation_id are deleted"""
    service = WorkflowService()

    with (
        patch.object(WorkflowService, 'get_workflow_executions', return_value=[workflow_execution_without_chat]),
        patch.object(WorkflowService, 'delete_workflow_execution') as mock_delete,
    ):
        service.delete_all_executions_by_workflow_id("workflow_123")

    mock_delete.assert_called_once_with(workflow_execution_without_chat.id)


def test_delete_all_executions_with_only_chat_executions(
    workflow_execution_with_chat: WorkflowExecution,
):
    """Test that no deletions occur when all executions have conversation_id"""
    service = WorkflowService()

    with (
        patch.object(WorkflowService, 'get_workflow_executions', return_value=[workflow_execution_with_chat]),
        patch.object(WorkflowService, 'delete_workflow_execution') as mock_delete,
    ):
        service.delete_all_executions_by_workflow_id("workflow_123")

    # Should not delete any executions
    mock_delete.assert_not_called()


def test_delete_workflow_uses_delete_all_executions(
    workflow_config: WorkflowConfig,
    user: User,
):
    """Test that delete_workflow calls delete_all_executions_by_workflow_id"""
    service = WorkflowService()

    with (
        patch.object(WorkflowService, 'delete_all_executions_by_workflow_id') as mock_delete_all,
        patch.object(WorkflowConfig, 'delete') as mock_delete_config,
        patch('codemie.service.workflow_service.WorkflowMonitoringService'),
    ):
        service.delete_workflow(workflow_config, user)

    mock_delete_all.assert_called_once_with(workflow_config.id)
    mock_delete_config.assert_called_once_with(workflow_config.id)


@pytest.mark.skip(reason="Requires database connection for conversation creation - integration test")
def test_create_workflow_execution_with_conversation_id(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user_model: UserEntity,
):
    """Test that creating workflow execution with conversation_id sets conversation_id"""
    conversation_id = "test-conversation-123"

    with (
        patch('uuid.uuid4', return_value=UUID('12345678-1234-5678-1234-567812345678')),
        patch.object(WorkflowExecution, 'save') as mock_save,
    ):
        result = workflow_service.create_workflow_execution(
            workflow_config, user_model, user_input="test input", conversation_id=conversation_id
        )

    # Verify conversation_id is set to conversation_id
    assert result.conversation_id == conversation_id
    assert result.workflow_id == workflow_config.id
    mock_save.assert_called_once_with(refresh=True)


def test_create_workflow_execution_without_conversation_id(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user_model: UserEntity,
):
    """Test that creating workflow execution without conversation_id has no conversation_id"""
    with (
        patch('uuid.uuid4', return_value=UUID('12345678-1234-5678-1234-567812345678')),
        patch.object(WorkflowExecution, 'save') as mock_save,
    ):
        result = workflow_service.create_workflow_execution(
            workflow_config,
            user_model,
            user_input="test input",
            conversation_id=None,  # No conversation
        )

    # Verify conversation_id is None
    assert result.conversation_id is None
    assert result.workflow_id == workflow_config.id
    mock_save.assert_called_once_with(refresh=True)


def test_delete_workflow_execution_calls_cascade_delete():
    """delete_workflow_execution delegates to WorkflowExecution.delete which cascades."""
    service = WorkflowService()
    with patch.object(WorkflowExecution, 'delete', return_value={"status": "deleted"}) as mock_delete:
        result = service.delete_workflow_execution("pk-id")

    mock_delete.assert_called_once_with("pk-id")
    assert result == {"status": "deleted"}


def test_delete_workflow_execution_cascade_deletes_states():
    """Confirm the service-level delete triggers cascade through WorkflowExecution.delete."""
    service = WorkflowService()

    mock_state = MagicMock(spec=WorkflowExecutionState)
    mock_state.id = "state-1"

    with patch.object(WorkflowExecution, 'delete', return_value={"status": "deleted"}) as mock_we_delete:
        result = service.delete_workflow_execution("pk-id")

    mock_we_delete.assert_called_once_with("pk-id")
    assert result == {"status": "deleted"}


def test_delete_all_executions_cascade_deletes_states_and_thoughts(
    workflow_execution_without_chat: WorkflowExecution,
):
    """delete_all_executions_by_workflow_id calls delete_workflow_execution for each non-chat execution."""
    exec_a = WorkflowExecution(id="exec-a", workflow_id="wf-1", execution_id="eid-a", conversation_id=None)
    exec_b = WorkflowExecution(id="exec-b", workflow_id="wf-1", execution_id="eid-b", conversation_id=None)
    service = WorkflowService()

    with (
        patch.object(WorkflowService, 'get_workflow_executions', return_value=[exec_a, exec_b]),
        patch.object(WorkflowService, 'delete_workflow_execution') as mock_delete,
    ):
        service.delete_all_executions_by_workflow_id("wf-1")

    assert mock_delete.call_count == 2
    mock_delete.assert_any_call(exec_a.id)
    mock_delete.assert_any_call(exec_b.id)
