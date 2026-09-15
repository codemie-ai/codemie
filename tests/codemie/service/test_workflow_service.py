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

from datetime import datetime
from unittest.mock import patch, MagicMock
from uuid import UUID
from datetime import timezone

import pytest
from typing import Union
from yaml import parser

from codemie.configs import config
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
    YamlConfigHistory,
)

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
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


def _history_entry_arg(mock_update) -> YamlConfigHistory | None:
    history = mock_update.call_args.kwargs.get("yaml_config_history")
    if not history:
        return None
    return history[0]


def _stub_update(workflow: WorkflowConfig) -> MagicMock:
    mock_update = MagicMock()
    object.__setattr__(workflow, "update", mock_update)
    return mock_update


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


def test_update_workflow(workflow_service, workflow_config, update_workflow_request, user):
    _stub_update(workflow_config)
    update_workflow_model = update_workflow_request.model_dump()
    expected_updater = user.as_user_model()
    original_yaml = workflow_config.yaml_config

    _ = workflow_service.update_workflow(workflow_config, WorkflowConfig(**update_workflow_model), user)

    workflow_config.update.assert_called_once()
    history_entry = _history_entry_arg(workflow_config.update)
    assert history_entry is not None
    assert history_entry.yaml_config == (original_yaml or "")
    assert workflow_config.updated_by == expected_updater
    for field_name in set(workflow_service._editable_non_boolean_fields):
        update_field_request = getattr(update_workflow_request, field_name)
        updated_model_field = getattr(workflow_config, field_name)
        assert updated_model_field == update_field_request, f"Mismatch in {field_name}"


def test_update_workflow_nothing_to_update(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    update_workflow_request_defaults: UpdateWorkflowRequest,
    user: User,
) -> None:
    _stub_update(workflow_config)
    original_workflow_dict = workflow_config.model_dump()
    update_workflow_model = update_workflow_request_defaults.model_dump()
    expected_updater = user.as_user_model()

    result = workflow_service.update_workflow(workflow_config, WorkflowConfig(**update_workflow_model), user)

    assert result == workflow_config
    workflow_config.update.assert_called_once()
    assert _history_entry_arg(workflow_config.update) is None
    assert workflow_config.updated_by == expected_updater
    for field_name in set(workflow_service._editable_non_boolean_fields) - {"supervisor_prompt"}:
        original_workflow_model_field = original_workflow_dict.get(field_name)
        updated_model_field = getattr(workflow_config, field_name)
        assert updated_model_field == original_workflow_model_field, f"Mismatch in {field_name}"
    assert workflow_config.supervisor_prompt is None


def test_update_workflow_clears_description_with_empty_string(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    """An empty string description must be applied, not silently skipped."""
    _stub_update(workflow_config)
    assert workflow_config.description == "A test workflow"

    updated = WorkflowConfig(
        name=workflow_config.name,
        description="",
        project="demo",
        mode=WorkflowMode.SEQUENTIAL,
    )
    workflow_service.update_workflow(workflow_config, updated, user)

    assert workflow_config.description == ""
    workflow_config.update.assert_called_once()
    assert _history_entry_arg(workflow_config.update) is None


def test_update_workflow_skips_none_fields(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    """Optional fields set to None must not overwrite existing values."""
    _stub_update(workflow_config)
    workflow_config.icon_url = "http://example.com/icon.png"

    updated = WorkflowConfig(
        name=workflow_config.name,
        description=workflow_config.description,
        project="demo",
        mode=WorkflowMode.SEQUENTIAL,
        icon_url=None,
    )
    workflow_service.update_workflow(workflow_config, updated, user)

    assert workflow_config.icon_url == "http://example.com/icon.png"
    workflow_config.update.assert_called_once()
    assert _history_entry_arg(workflow_config.update) is None


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


@pytest.mark.parametrize(
    ("slug", "required_variables"),
    [
        ("template-java-sonar-issues-fixer-workflow", ["default_branch", "git_project_id"]),
        ("template-newsletter-generator-workflow", ["jira_project"]),
        ("template-python-unit-tests-generation-workflow", ["jira_project"]),
        ("amna-windup-jira-resolver", ["git_datasource_id"]),
        ("amna-py-unittest-gen-simple", ["git_datasource_id"]),
        ("amna-okta-implementation-migration", ["git_datasource_id"]),
        ("amna-linq-to-sql-migration", ["git_datasource_id"]),
        ("amna-java-migration", ["git_datasource_id"]),
        ("amna-dot-net-8-migration", ["git_datasource_id", "instructions_datasource_id"]),
        ("amna-cobol-migration-java", ["jira_project"]),
    ],
)
def test_placeholder_templates_expose_and_materialize_required_variables(
    workflow_service: WorkflowService, slug: str, required_variables: list[str]
) -> None:
    template = workflow_service.get_prebuilt_workflow_by_slug(slug)

    assert template is not None
    assert template.required_variables == required_variables

    seed = workflow_service.materialize_template(
        slug,
        {name: f"value-for-{name}" for name in required_variables},
    )

    assert seed is not None
    assert "${input:" not in "\n".join(value or "" for value in seed.values())


@patch.object(WorkflowService, "_cached_prebuilt_workflows", new_callable=list)
def test_get_prebuilt_workflows_continues_after_one_template_error(
    _mock_cached_workflows, tmp_path, monkeypatch, workflow_service
):
    (tmp_path / "good_template.yaml").write_text(
        "name: Good Template\n"
        "description: ok\n"
        "slug: good-template\n"
        "mode: Sequential\n"
        "execution_config:\n"
        "  assistants: []\n"
        "  states: []\n"
    )
    (tmp_path / "explode_template.yaml").write_text("name: explode\n")
    monkeypatch.setattr(config, "WORKFLOW_TEMPLATES_DIR", str(tmp_path))

    original_from_yaml = WorkflowConfigTemplate.from_yaml

    def from_yaml_or_raise(yaml_str: str):
        if "explode" in yaml_str:
            raise RuntimeError("boom")
        return original_from_yaml(yaml_str)

    with patch.object(WorkflowConfigTemplate, "from_yaml", side_effect=from_yaml_or_raise):
        templates = workflow_service.get_prebuilt_workflows()

    assert [template.slug for template in templates] == ["good-template"]


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
def test_yaml_reflects_assistants_and_states(
    mock_logger: MagicMock,
    user: User,
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    update_workflow_request_defaults: UpdateWorkflowRequest,
) -> None:
    _stub_update(workflow_config)
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
    workflow_config.update.assert_called_once()
    assert _history_entry_arg(workflow_config.update) is not None


@patch('codemie.service.workflow_service.logger')
def test_invalid_yaml_logs_error(
    mock_logger: MagicMock,
    user: User,
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    update_workflow_request_defaults: WorkflowConfig,
) -> None:
    _stub_update(workflow_config)
    invalid_yaml = "states: [missing_bracket"
    expected_error_substring = "while parsing a flow sequence"
    expected_log_error_substring = "Failed to update workflow: "
    update_workflow_model = WorkflowConfig(**update_workflow_request_defaults.model_dump())
    update_workflow_model.yaml_config = invalid_yaml

    with pytest.raises(parser.ParserError) as p_error:
        workflow_service.update_workflow(workflow_config, update_workflow_model, user)

    assert expected_error_substring in str(p_error.value)
    workflow_config.update.assert_not_called()
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
    """Test that executions are not deleted when their conversation still exists"""
    service = WorkflowService()

    with (
        patch.object(
            WorkflowService,
            'get_workflow_executions',
            return_value=[workflow_execution_with_chat, workflow_execution_without_chat],
        ),
        patch.object(WorkflowService, 'delete_workflow_execution') as mock_delete,
        patch(
            "codemie.rest_api.models.conversation.Conversation.get_existing_ids",
            return_value={"conversation_456"},
        ),
    ):
        service.delete_all_executions_by_workflow_id("workflow_123")

    mock_delete.assert_called_once_with(workflow_execution_without_chat.id)
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
    """Test that no deletions occur when all executions have an existing conversation"""
    service = WorkflowService()

    with (
        patch.object(WorkflowService, 'get_workflow_executions', return_value=[workflow_execution_with_chat]),
        patch.object(WorkflowService, 'delete_workflow_execution') as mock_delete,
        patch(
            "codemie.rest_api.models.conversation.Conversation.get_existing_ids",
            return_value={"conversation_456"},
        ),
    ):
        service.delete_all_executions_by_workflow_id("workflow_123")

    mock_delete.assert_not_called()


def test_delete_all_executions_includes_orphaned_executions(
    workflow_execution_with_chat: WorkflowExecution,
    workflow_execution_without_chat: WorkflowExecution,
):
    """Test that executions with a deleted conversation are included in bulk delete"""
    service = WorkflowService()

    with (
        patch.object(
            WorkflowService,
            'get_workflow_executions',
            return_value=[workflow_execution_with_chat, workflow_execution_without_chat],
        ),
        patch.object(WorkflowService, 'delete_workflow_execution') as mock_delete,
        patch(
            "codemie.rest_api.models.conversation.Conversation.get_existing_ids",
            return_value=set(),
        ),
    ):
        service.delete_all_executions_by_workflow_id("workflow_123")

    assert mock_delete.call_count == 2


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


class TestGetNestingDepth:
    def test_top_level_returns_zero(self):
        top = MagicMock()
        top.parent_execution_id = None
        with patch.object(WorkflowService, 'find_workflow_execution_by_id', return_value=top):
            assert WorkflowService.get_nesting_depth("exec-0") == 0

    def test_one_parent_returns_one(self):
        child = MagicMock()
        child.parent_execution_id = "parent-id"
        parent = MagicMock()
        parent.parent_execution_id = None

        def _find(eid):
            return child if eid == "child-id" else parent

        with patch.object(WorkflowService, 'find_workflow_execution_by_id', side_effect=_find):
            assert WorkflowService.get_nesting_depth("child-id") == 1

    def test_counts_true_depth_independent_of_global_default(self):
        # A chain of 3 ancestors must report depth 3 even when the global default is 1;
        # the per-workflow max_nesting_level (enforced by the caller) may exceed it.
        chain = {"n4": "n3", "n3": "n2", "n2": "n1", "n1": None}

        def _find(eid):
            m = MagicMock()
            m.parent_execution_id = chain.get(eid)
            return m

        with patch.object(WorkflowService, 'find_workflow_execution_by_id', side_effect=_find):
            with patch('codemie.service.workflow_service.config') as mock_cfg:
                mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 1
                result = WorkflowService.get_nesting_depth("n4")
        assert result == 3

    def test_cyclic_lineage_terminates(self):
        # Corrupted lineage A->B->A must terminate via the visited-set guard, not loop forever.
        cycle = {"A": "B", "B": "A"}

        def _find(eid):
            m = MagicMock()
            m.parent_execution_id = cycle.get(eid)
            return m

        with patch.object(WorkflowService, 'find_workflow_execution_by_id', side_effect=_find):
            result = WorkflowService.get_nesting_depth("A")
        assert result == 2  # counted A->B and B->A, then detected the repeat and stopped


def test_create_workflow_execution_sets_lineage_when_parent_given(
    workflow_config: WorkflowConfig,
    user_model: UserEntity,
):
    parent_exec = MagicMock()
    parent_exec.active_sub_execution_id = None

    with (
        patch.object(WorkflowExecution, 'save'),
        patch.object(WorkflowService, 'find_workflow_execution_by_id', return_value=parent_exec),
        patch('uuid.uuid4', return_value=UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')),
        patch.object(WorkflowService, '_augment_user_input_with_history', return_value="input"),
    ):
        result = WorkflowService.create_workflow_execution(
            workflow_config,
            user_model,
            "task input",
            parent_execution_id="parent-exec-id",
        )

    assert result.parent_execution_id == "parent-exec-id"
    assert parent_exec.active_sub_execution_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    parent_exec.save.assert_called_once()


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


# ===== append_user_message_on_resume tests =====


def test_append_user_message_on_resume_appends_to_execution_history():
    """append_user_message_on_resume builds a USER GeneratedMessage and appends it to execution history."""
    service = WorkflowService()
    execution = WorkflowExecution(
        workflow_id="wf-1",
        execution_id="exec-1",
        history=[],
        conversation_id=None,
    )

    with patch.object(WorkflowExecution, "update") as mock_update:
        service.append_user_message_on_resume(execution, "hello resume")

    assert len(execution.history) == 1
    msg = execution.history[0]
    assert msg.role == ChatRole.USER.value
    assert msg.message == "hello resume"
    assert msg.message_raw == "hello resume"
    assert msg.history_index == 0
    mock_update.assert_called_once_with(refresh=True)


def test_append_user_message_on_resume_increments_history_index():
    """history_index for the new message is one higher than the current max."""
    from codemie.rest_api.models.conversation import GeneratedMessage

    service = WorkflowService()
    existing_msg = GeneratedMessage(role=ChatRole.USER.value, message="first", history_index=3)
    execution = WorkflowExecution(
        workflow_id="wf-1",
        execution_id="exec-2",
        history=[existing_msg],
        conversation_id=None,
    )

    with patch.object(WorkflowExecution, "update"):
        service.append_user_message_on_resume(execution, "second")

    assert execution.history[-1].history_index == 4


def test_append_user_message_on_resume_also_updates_conversation():
    """When conversation_id is set, the message is also appended to the Conversation."""
    service = WorkflowService()
    execution = WorkflowExecution(
        workflow_id="wf-1",
        execution_id="exec-3",
        history=[],
        conversation_id="conv-42",
    )
    mock_conversation = MagicMock()
    mock_conversation.history = []
    mock_conversation.finished_at = None

    mock_conv_class = MagicMock()
    mock_conv_class.get_by_id.return_value = mock_conversation

    with (
        patch.object(WorkflowExecution, "update"),
        patch("codemie.rest_api.models.conversation.Conversation.get_by_id", mock_conv_class.get_by_id),
    ):
        service.append_user_message_on_resume(execution, "hello conv")

    mock_conv_class.get_by_id.assert_called_once_with("conv-42")
    assert len(mock_conversation.history) == 2
    user_msg = mock_conversation.history[0]
    assert user_msg.message == "hello conv"
    assert user_msg.role == ChatRole.USER.value
    assistant_ref = mock_conversation.history[1]
    assert assistant_ref.role == ChatRole.ASSISTANT.value
    assert assistant_ref.workflow_execution_ref is True
    assert assistant_ref.execution_id == "exec-3"
    assert assistant_ref.assistant_id == "wf-1"
    assert assistant_ref.history_index == user_msg.history_index
    mock_conversation.update.assert_called_once()


def test_append_user_message_on_resume_raises_409_when_conversation_finished(user):
    execution = WorkflowExecution(
        workflow_id="wf-1",
        execution_id="exec-1",
        conversation_id="conv-1",
        history=[],
        overall_status=WorkflowExecutionStatusEnum.INTERRUPTED,
        project=EXAMPLE_PROJECT,
        created_by=user.as_user_model(),
    )
    finished_conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id=user.id,
        history=[],
        finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )
    workflow_service = WorkflowService()

    with (
        patch("codemie.rest_api.models.conversation.Conversation.get_by_id", return_value=finished_conversation),
        patch.object(WorkflowExecution, "update", return_value=None) as mock_exec_update,
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            workflow_service.append_user_message_on_resume(execution, "hello")
    assert exc_info.value.code == 409
    mock_exec_update.assert_not_called()


def test_create_workflow_execution_raises_409_when_existing_conversation_finished(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user_model: UserEntity,
):
    finished_conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id=user_model.user_id,
        history=[],
        finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )

    with patch("codemie.rest_api.models.conversation.Conversation.get_by_id", return_value=finished_conversation):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            workflow_service.create_workflow_execution(
                workflow_config, user_model, user_input="hi", conversation_id="conv-1"
            )
    assert exc_info.value.code == 409


def test_append_user_message_on_resume_no_conversation_update_when_no_conversation_id():
    """When conversation_id is None, no Conversation lookup or update happens."""
    service = WorkflowService()
    execution = WorkflowExecution(
        workflow_id="wf-1",
        execution_id="exec-4",
        history=[],
        conversation_id=None,
    )
    mock_conv_class = MagicMock()

    with (
        patch.object(WorkflowExecution, "update"),
        patch("codemie.rest_api.models.conversation.Conversation.get_by_id", mock_conv_class.get_by_id),
    ):
        service.append_user_message_on_resume(execution, "no conv")

    mock_conv_class.get_by_id.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for _update_workflow_values – start_hint explicit update block
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored_hint, updated_hint, expected_hint",
    [
        ("old hint", "new hint", "new hint"),
        (None, "new hint", "new hint"),
        ("some hint", "some hint", "some hint"),
    ],
    ids=["hint_changed", "hint_set_from_none", "hint_unchanged"],
)
def test_update_workflow_values_start_hint_updated_when_different(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
    stored_hint: str,
    updated_hint: str,
    expected_hint: str,
) -> None:
    """_update_workflow_values must assign start_hint from updated config when values differ."""
    _stub_update(workflow_config)
    workflow_config.start_hint = stored_hint

    updated_config = WorkflowConfig(
        name=workflow_config.name,
        description=workflow_config.description,
        project=workflow_config.project,
        start_hint=updated_hint,
    )

    workflow_service._update_workflow_values(workflow_config, updated_config, user)

    assert workflow_config.start_hint == expected_hint
    workflow_config.update.assert_called_once()
    assert _history_entry_arg(workflow_config.update) is None


@pytest.mark.parametrize(
    "stored_hint, updated_hint",
    [
        ("existing hint", None),
        ("existing hint", ""),
    ],
    ids=["cleared_to_none", "cleared_to_empty_string"],
)
def test_update_workflow_values_start_hint_cleared(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
    stored_hint: str,
    updated_hint,
) -> None:
    """_update_workflow_values must support explicitly clearing start_hint to None or empty string.

    The general editable-fields loop skips falsy values, so the explicit
    start_hint block is the only path that allows clearing the field.
    """
    _stub_update(workflow_config)
    workflow_config.start_hint = stored_hint

    updated_config = WorkflowConfig(
        name=workflow_config.name,
        description=workflow_config.description,
        project=workflow_config.project,
        start_hint=updated_hint,
    )

    workflow_service._update_workflow_values(workflow_config, updated_config, user)

    assert workflow_config.start_hint == updated_hint
    workflow_config.update.assert_called_once()
    assert _history_entry_arg(workflow_config.update) is None


# ── WorkflowService execution-state helpers ───────────────────────────────────


class TestFindExecutionStateOutput:
    def test_returns_output_for_valid_state_id(self):
        mock_state = MagicMock()
        mock_state.output = "previous node result"
        with patch("codemie.service.workflow_service.WorkflowExecutionState") as mock_cls:
            mock_cls.get_by_id.return_value = mock_state
            result = WorkflowService.find_execution_state_output("state-abc")
        assert result == "previous node result"
        mock_cls.get_by_id.assert_called_once_with(id_="state-abc")

    def test_returns_none_when_state_not_found(self):
        with patch("codemie.service.workflow_service.WorkflowExecutionState") as mock_cls:
            mock_cls.get_by_id.return_value = None
            result = WorkflowService.find_execution_state_output("missing-id")
        assert result is None

    def test_returns_none_on_exception(self):
        with patch("codemie.service.workflow_service.WorkflowExecutionState") as mock_cls:
            mock_cls.get_by_id.side_effect = Exception("ES unavailable")
            result = WorkflowService.find_execution_state_output("err-id")
        assert result is None


class TestFindLastExecutionStateOutput:
    def test_returns_output_of_last_succeeded_state(self):
        succeeded = MagicMock()
        succeeded.status = WorkflowExecutionStatusEnum.SUCCEEDED
        succeeded.output = "child final answer"
        with patch("codemie.service.workflow_service.WorkflowExecutionState") as mock_cls:
            mock_cls.get_all_by_fields.return_value = [succeeded]
            result = WorkflowService.find_last_execution_state_output("exec-123")
        assert result == "child final answer"
        mock_cls.get_all_by_fields.assert_called_once_with(
            fields={"execution_id.keyword": "exec-123"},
            order_by="update_date",
            order_desc=True,
        )

    def test_skips_non_succeeded_states(self):
        failed_state = MagicMock()
        failed_state.status = WorkflowExecutionStatusEnum.FAILED
        succeeded_state = MagicMock()
        succeeded_state.status = WorkflowExecutionStatusEnum.SUCCEEDED
        succeeded_state.output = "real output"
        with patch("codemie.service.workflow_service.WorkflowExecutionState") as mock_cls:
            mock_cls.get_all_by_fields.return_value = [failed_state, succeeded_state]
            result = WorkflowService.find_last_execution_state_output("exec-xyz")
        assert result == "real output"

    def test_returns_none_when_no_succeeded_state(self):
        with patch("codemie.service.workflow_service.WorkflowExecutionState") as mock_cls:
            mock_cls.get_all_by_fields.return_value = []
            result = WorkflowService.find_last_execution_state_output("exec-empty")
        assert result is None

    def test_returns_none_on_exception(self):
        with patch("codemie.service.workflow_service.WorkflowExecutionState") as mock_cls:
            mock_cls.get_all_by_fields.side_effect = Exception("timeout")
            result = WorkflowService.find_last_execution_state_output("exec-err")
        assert result is None


class TestYamlContentChanged:
    def test_identical_strings_returns_false(self):
        assert WorkflowService._yaml_content_changed("key: value", "key: value") is False

    def test_comment_only_difference_returns_false(self):
        assert WorkflowService._yaml_content_changed("key: value", "key: value\n# comment only") is False

    @pytest.mark.parametrize(
        ("old_yaml", "new_yaml"),
        [
            ("first: 1\nsecond: 2", "second: 2\nfirst: 1"),
            ("key: value", 'key: "value"'),
            ("items:\n- one\n- two", "items: [one, two]"),
        ],
        ids=["reordered_keys", "quote_style", "block_vs_flow_list"],
    )
    def test_formatting_only_difference_returns_false(self, old_yaml: str, new_yaml: str):
        assert WorkflowService._yaml_content_changed(old_yaml, new_yaml) is False

    @pytest.mark.parametrize(
        ("old_yaml", "new_yaml"),
        [
            (None, ""),
            (None, "# comment only"),
            ("# comment only", None),
        ],
        ids=["none_to_empty", "none_to_comment", "comment_to_none"],
    )
    def test_absent_and_semantically_empty_yaml_returns_false(
        self,
        old_yaml: str | None,
        new_yaml: str | None,
    ):
        assert WorkflowService._yaml_content_changed(old_yaml, new_yaml) is False

    def test_trailing_newline_difference_returns_false(self):
        assert WorkflowService._yaml_content_changed("key: value\n", "key: value") is False

    def test_crlf_vs_lf_returns_false(self):
        assert WorkflowService._yaml_content_changed("key: value\r\n", "key: value\n") is False

    def test_different_yaml_content_returns_true(self):
        assert WorkflowService._yaml_content_changed("key: a", "key: b") is True

    def test_none_vs_none_returns_false(self):
        assert WorkflowService._yaml_content_changed(None, None) is False

    def test_none_vs_yaml_returns_true(self):
        assert WorkflowService._yaml_content_changed(None, "key: value") is True

    def test_yaml_vs_none_returns_true(self):
        assert WorkflowService._yaml_content_changed("key: value", None) is True

    def test_invalid_yaml_uses_string_comparison(self):
        assert WorkflowService._yaml_content_changed("{bad yaml", "{bad yaml") is False
        assert WorkflowService._yaml_content_changed("{bad yaml", "{different bad") is True


def test_save_workflow_schema_persists_via_update(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
) -> None:
    mock_file_result = MagicMock()
    mock_file_result.to_encoded_url.return_value = "https://example.com/wf.svg"
    mock_files_repo = MagicMock()
    mock_files_repo.write_file.return_value = mock_file_result
    mock_update = _stub_update(workflow_config)

    with patch("codemie.service.workflow_service.FileRepositoryFactory") as mock_factory:
        mock_factory.return_value.get_current_repository.return_value = mock_files_repo
        workflow_service.save_workflow_schema(workflow_config, b"<svg/>")

    assert workflow_config.schema_url == "https://example.com/wf.svg"
    mock_update.assert_called_once_with()


def test_update_workflow_yaml_change_prepends_history(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    _stub_update(workflow_config)
    workflow_config.yaml_config = "assistants: []"
    updated = WorkflowConfig(
        name=workflow_config.name,
        description=workflow_config.description,
        project="demo",
        yaml_config="assistants:\n- id: new_assistant",
    )

    workflow_service._update_workflow_values(workflow_config, updated, user)

    workflow_config.update.assert_called_once()
    assert workflow_config.yaml_config == "assistants:\n- id: new_assistant"
    history_entry = _history_entry_arg(workflow_config.update)
    assert history_entry is not None
    assert history_entry.yaml_config == "assistants: []"


def test_update_workflow_metadata_only_does_not_prepend_history(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    _stub_update(workflow_config)
    workflow_config.yaml_config = "assistants: []"
    updated = WorkflowConfig(
        name="New Name",
        description=workflow_config.description,
        project="demo",
    )

    workflow_service._update_workflow_values(workflow_config, updated, user)

    workflow_config.update.assert_called_once()
    assert _history_entry_arg(workflow_config.update) is None


def test_update_workflow_same_yaml_does_not_prepend_history(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    _stub_update(workflow_config)
    workflow_config.yaml_config = "assistants: []"
    updated = WorkflowConfig(
        name=workflow_config.name,
        description=workflow_config.description,
        project="demo",
        yaml_config="assistants: []",
    )

    workflow_service._update_workflow_values(workflow_config, updated, user)

    workflow_config.update.assert_called_once()
    assert _history_entry_arg(workflow_config.update) is None


def test_update_workflow_comment_only_yaml_does_not_drift_live_config(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    _stub_update(workflow_config)
    workflow_config.yaml_config = "key: value"
    updated = WorkflowConfig(
        name=workflow_config.name,
        description=workflow_config.description,
        project="demo",
        yaml_config="key: value\n# comment only",
    )

    workflow_service._update_workflow_values(workflow_config, updated, user)

    workflow_config.update.assert_called_once()
    assert workflow_config.yaml_config == "key: value"
    assert _history_entry_arg(workflow_config.update) is None


def test_update_workflow_comment_only_yaml_does_not_replace_absent_config_or_prepend_history(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    _stub_update(workflow_config)
    workflow_config.yaml_config = None
    updated = WorkflowConfig(
        name=workflow_config.name,
        description=workflow_config.description,
        project="demo",
        yaml_config="# comment only",
    )

    workflow_service._update_workflow_values(workflow_config, updated, user)

    workflow_config.update.assert_called_once()
    assert workflow_config.yaml_config is None
    assert _history_entry_arg(workflow_config.update) is None


def test_update_workflow_newline_only_yaml_does_not_drift_live_config(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    _stub_update(workflow_config)
    workflow_config.yaml_config = "key: value"
    updated = WorkflowConfig(
        name=workflow_config.name,
        description=workflow_config.description,
        project="demo",
        yaml_config="key: value\n",
    )

    workflow_service._update_workflow_values(workflow_config, updated, user)

    workflow_config.update.assert_called_once()
    assert workflow_config.yaml_config == "key: value"
    assert _history_entry_arg(workflow_config.update) is None


def test_update_then_save_schema_preserves_history(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    mock_update = _stub_update(workflow_config)
    workflow_config.yaml_config = "assistants: []"
    workflow_config.yaml_config_history = [
        YamlConfigHistory(
            yaml_config="assistants: []",
            date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            created_by=user.as_user_model(),
        )
    ]
    updated = WorkflowConfig(
        name="Renamed Workflow",
        description=workflow_config.description,
        project="demo",
        yaml_config="assistants: []",
    )

    workflow_service.update_workflow(workflow_config, updated, user)

    assert _history_entry_arg(workflow_config.update) is None
    assert len(workflow_config.yaml_config_history) >= 1

    mock_update.reset_mock()
    mock_files_repo = MagicMock()
    mock_file_result = MagicMock()
    mock_file_result.to_encoded_url.return_value = "https://example.com/schema.svg"
    mock_files_repo.write_file.return_value = mock_file_result

    with patch("codemie.service.workflow_service.FileRepositoryFactory") as mock_factory:
        mock_factory.return_value.get_current_repository.return_value = mock_files_repo
        workflow_service.save_workflow_schema(workflow_config, b"<svg/>")

    assert workflow_config.schema_url == "https://example.com/schema.svg"
    mock_update.assert_called_once_with()
    assert len(workflow_config.yaml_config_history) >= 1


@patch("codemie.service.workflow_service.WorkflowMonitoringService.send_update_workflow_metric")
def test_update_then_save_schema_prepends_history_then_persists_schema(
    mock_metric: MagicMock,
    workflow_service: WorkflowService,
    user: User,
) -> None:
    """YAML change prepends history via update(); schema persist uses update() without an explicit history list."""
    prior = YamlConfigHistory(yaml_config="prior-yaml", date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    stored = WorkflowConfig(
        id="wf_1",
        name="Old Name",
        description="d",
        yaml_config="old: yaml",
        project="demo",
        yaml_config_history=[prior],
    )
    mock_update = _stub_update(stored)
    updated = WorkflowConfig(
        name="Old Name",
        description="d",
        project="demo",
        yaml_config="old: changed",
    )

    mock_file_result = MagicMock()
    mock_file_result.to_encoded_url.return_value = "https://example.com/wf.svg"
    mock_files_repo = MagicMock()
    mock_files_repo.write_file.return_value = mock_file_result

    with patch("codemie.service.workflow_service.FileRepositoryFactory") as mock_factory:
        mock_factory.return_value.get_current_repository.return_value = mock_files_repo
        workflow_service._update_workflow_values(stored, updated, user)
        workflow_service.save_workflow_schema(stored, b"<svg/>")

    assert stored.yaml_config == "old: changed"
    assert stored.schema_url == "https://example.com/wf.svg"
    assert mock_update.call_count == 2
    history = mock_update.call_args_list[0].kwargs["yaml_config_history"]
    assert [entry.yaml_config for entry in history] == ["old: yaml", "prior-yaml"]
    assert mock_update.call_args_list[1].args == ()
    assert mock_update.call_args_list[1].kwargs == {}
    mock_metric.assert_called_once()
