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

"""
Tests for the assistant user mapping service.
"""

from unittest.mock import MagicMock
import pytest

from codemie.rest_api.models.usage.assistant_user_mapping import (
    ASSISTANT_SCOPE,
    AssistantUserMappingSQL,
    ToolConfig,
)
from codemie.repository.assistants.assistant_user_mapping_repository import AssistantUserMappingRepository
from codemie.service.assistant.assistant_user_mapping_service import AssistantUserMappingService


@pytest.fixture
def mock_repository():
    repository = MagicMock(spec=AssistantUserMappingRepository)
    return repository


@pytest.fixture
def service(mock_repository):
    return AssistantUserMappingService(repository=mock_repository)


@pytest.fixture
def sample_mapping():
    return AssistantUserMappingSQL(
        id="test-id",
        assistant_id="test-assistant-id",
        user_id="test-user-id",
        tools_config=[
            ToolConfig(name="Git", integration_id="git-integration-id"),
            ToolConfig(name="JIRA", integration_id="jira-integration-id"),
        ],
    )


@pytest.fixture
def tools_config_list():
    return [
        {"name": "Git", "integration_id": "git-integration-id"},
        {"name": "JIRA", "integration_id": "jira-integration-id"},
    ]


def test_create_or_update_mapping(service, mock_repository, tools_config_list, sample_mapping):
    # Arrange
    assistant_id = "test-assistant-id"
    user_id = "test-user-id"
    mock_repository.get_mapping.return_value = None  # no existing mapping
    mock_repository.create_or_update_mapping.return_value = sample_mapping

    # Act
    result = service.create_or_update_mapping(assistant_id, user_id, tools_config_list)

    # Assert
    mock_repository.create_or_update_mapping.assert_called_once()
    assert result == sample_mapping

    # Check that the tools_config was properly converted
    args = mock_repository.create_or_update_mapping.call_args[0]
    assert args[0] == assistant_id
    assert args[1] == user_id
    saved = {tc.name: tc.integration_id for tc in args[2]}
    assert saved == {"Git": "git-integration-id", "JIRA": "jira-integration-id"}
    assert all(isinstance(tc, ToolConfig) for tc in args[2])


def test_create_or_update_mapping_remembers_a_regular_slot_reset_to_none(service, mock_repository, sample_mapping):
    """A regular slot sent with an empty integration_id is remembered as "no integration".

    Deleting it would make the choice indistinguishable from "never chose anything", and auto
    lookup would override the user's decision on the next load.
    """
    # Arrange: existing mapping has Git + JIRA; user resets Git to "None".
    mock_repository.get_mapping.return_value = sample_mapping
    incoming = [{"name": "Git", "integration_id": ""}]

    # Act
    service.create_or_update_mapping("test-assistant-id", "test-user-id", incoming)

    # Assert: JIRA untouched, Git kept as an explicit "no integration".
    saved = {tc.name: tc.integration_id for tc in mock_repository.create_or_update_mapping.call_args[0][2]}
    assert saved == {"JIRA": "jira-integration-id", "Git": ""}


def test_create_or_update_mapping_leaves_untouched_slots(service, mock_repository, sample_mapping):
    """Slots absent from the payload are preserved; only the sent slot is upserted."""
    # Arrange: existing Git + JIRA; payload only updates Git.
    mock_repository.get_mapping.return_value = sample_mapping
    incoming = [{"name": "Git", "integration_id": "git-new-id"}]

    # Act
    service.create_or_update_mapping("test-assistant-id", "test-user-id", incoming)

    # Assert: Git updated, JIRA untouched.
    saved = {tc.name: tc.integration_id for tc in mock_repository.create_or_update_mapping.call_args[0][2]}
    assert saved == {"Git": "git-new-id", "JIRA": "jira-integration-id"}


def test_create_or_update_mapping_keeps_the_last_regular_slot_as_explicit_none(
    service, mock_repository, sample_mapping
):
    """Clearing the only regular slot keeps it, recorded as an explicit "no integration"."""
    # Arrange: existing has a single Git slot; user clears it.
    single = AssistantUserMappingSQL(
        id="test-id",
        assistant_id="test-assistant-id",
        user_id="test-user-id",
        tools_config=[ToolConfig(name="Git", integration_id="git-integration-id")],
    )
    mock_repository.get_mapping.return_value = single
    incoming = [{"name": "Git", "integration_id": ""}]

    # Act
    service.create_or_update_mapping("test-assistant-id", "test-user-id", incoming)

    # Assert
    saved = {tc.name: tc.integration_id for tc in mock_repository.create_or_update_mapping.call_args[0][2]}
    assert saved == {"Git": ""}


def test_get_mapping(service, mock_repository, sample_mapping):
    # Arrange
    assistant_id = "test-assistant-id"
    user_id = "test-user-id"
    mock_repository.get_mapping.return_value = sample_mapping

    # Act
    result = service.get_mapping(assistant_id, user_id)

    # Assert
    mock_repository.get_mapping.assert_called_once_with(assistant_id, user_id, ASSISTANT_SCOPE)
    assert result == sample_mapping


def test_get_mapping_not_found(service, mock_repository):
    # Arrange
    assistant_id = "test-assistant-id"
    user_id = "test-user-id"
    mock_repository.get_mapping.return_value = None

    # Act
    result = service.get_mapping(assistant_id, user_id)

    # Assert
    mock_repository.get_mapping.assert_called_once_with(assistant_id, user_id, ASSISTANT_SCOPE)
    assert result is None


def test_get_mappings_by_assistant(service, mock_repository):
    # Arrange
    assistant_id = "test-assistant-id"
    expected_mappings = [
        AssistantUserMappingSQL(
            id="test-id-1",
            assistant_id=assistant_id,
            user_id="user-1",
            tools_config=[ToolConfig(name="Git", integration_id="git-id-1")],
        ),
        AssistantUserMappingSQL(
            id="test-id-2",
            assistant_id=assistant_id,
            user_id="user-2",
            tools_config=[ToolConfig(name="Git", integration_id="git-id-2")],
        ),
    ]
    mock_repository.get_mappings_by_assistant.return_value = expected_mappings

    # Act
    result = service.get_mappings_by_assistant(assistant_id)

    # Assert
    mock_repository.get_mappings_by_assistant.assert_called_once_with(assistant_id)
    assert result == expected_mappings


def test_get_mappings_by_user(service, mock_repository):
    # Arrange
    user_id = "test-user-id"
    expected_mappings = [
        AssistantUserMappingSQL(
            id="test-id-1",
            assistant_id="assistant-1",
            user_id=user_id,
            tools_config=[ToolConfig(name="Git", integration_id="git-id-1")],
        ),
        AssistantUserMappingSQL(
            id="test-id-2",
            assistant_id="assistant-2",
            user_id=user_id,
            tools_config=[ToolConfig(name="Git", integration_id="git-id-2")],
        ),
    ]
    mock_repository.get_mappings_by_user.return_value = expected_mappings

    # Act
    result = service.get_mappings_by_user(user_id)

    # Assert
    mock_repository.get_mappings_by_user.assert_called_once_with(user_id)
    assert result == expected_mappings


def test_singleton_instance():
    from codemie.service.assistant.assistant_user_mapping_service import assistant_user_mapping_service

    # Ensure the singleton instance is an instance of the service
    assert isinstance(assistant_user_mapping_service, AssistantUserMappingService)


def test_workflow_scoped_save_merges_within_its_own_scope(service, mock_repository, tools_config_list):
    # Arrange: a workflow-scoped save must never merge against the assistant-scoped row,
    # otherwise assistant slots would be silently copied into the workflow.
    mock_repository.get_mapping.return_value = None

    # Act
    service.create_or_update_mapping(
        assistant_id="test-assistant-id",
        user_id="test-user-id",
        tools_config=tools_config_list,
        workflow_id="workflow-1",
    )

    # Assert
    mock_repository.get_mapping.assert_called_once_with("test-assistant-id", "test-user-id", "workflow-1")
    assert mock_repository.create_or_update_mapping.call_args[0][3] == "workflow-1"


def test_apply_to_assistant_writes_assistant_scope_and_clears_the_workflow_row(
    service, mock_repository, tools_config_list
):
    # Arrange
    mock_repository.get_mapping.return_value = None

    # Act
    service.create_or_update_mapping(
        assistant_id="test-assistant-id",
        user_id="test-user-id",
        tools_config=tools_config_list,
        workflow_id="workflow-1",
        apply_to_assistant=True,
    )

    # Assert: one atomic call, so a failure cannot leave the stale workflow row overriding the
    # selection the user just applied everywhere.
    mock_repository.promote_to_assistant_scope.assert_called_once()
    args = mock_repository.promote_to_assistant_scope.call_args[0]
    assert args[0] == "test-assistant-id"
    assert args[1] == "test-user-id"
    assert args[3] == "workflow-1"
    mock_repository.create_or_update_mapping.assert_not_called()


def test_effective_config_overlays_workflow_slots_on_assistant_slots(service, mock_repository):
    # Arrange: the assistant scope is the baseline, the workflow scope overrides it per slot.
    assistant_mapping = AssistantUserMappingSQL(
        id="assistant-row",
        assistant_id="test-assistant-id",
        user_id="test-user-id",
        tools_config=[
            ToolConfig(name="Git", integration_id="assistant-git"),
            ToolConfig(name="JIRA", integration_id="assistant-jira"),
        ],
    )
    workflow_mapping = AssistantUserMappingSQL(
        id="workflow-row",
        assistant_id="test-assistant-id",
        user_id="test-user-id",
        workflow_id="workflow-1",
        tools_config=[ToolConfig(name="Git", integration_id="workflow-git")],
    )
    mock_repository.get_mapping.side_effect = lambda a, u, w: (
        assistant_mapping if w == ASSISTANT_SCOPE else workflow_mapping
    )

    # Act
    configs, has_assistant_scope = service.get_effective_tools_config("test-assistant-id", "test-user-id", "workflow-1")

    # Assert
    assert {c.name: c.integration_id for c in configs} == {
        "Git": "workflow-git",
        "JIRA": "assistant-jira",
    }
    assert has_assistant_scope is True


def test_effective_config_falls_back_to_the_assistant_scope(service, mock_repository, sample_mapping):
    # Arrange
    mock_repository.get_mapping.side_effect = lambda a, u, w: sample_mapping if w == ASSISTANT_SCOPE else None

    # Act
    configs, has_assistant_scope = service.get_effective_tools_config("test-assistant-id", "test-user-id", "workflow-1")

    # Assert
    assert {c.name for c in configs} == {"Git", "JIRA"}
    assert has_assistant_scope is True


def test_effective_config_reports_a_missing_assistant_scope(service, mock_repository):
    # Arrange: the panel needs this flag to decide whether the checkbox starts ticked.
    mock_repository.get_mapping.return_value = None

    # Act
    configs, has_assistant_scope = service.get_effective_tools_config("test-assistant-id", "test-user-id", "workflow-1")

    # Assert
    assert configs == []
    assert has_assistant_scope is False


def test_effective_config_ignores_an_empty_assistant_row(service, mock_repository):
    # An empty leftover row (every slot reset to "None") is not a selection, so the panel must
    # still treat the next choice as the user's first one.
    empty_mapping = AssistantUserMappingSQL(
        id="assistant-row", assistant_id="test-assistant-id", user_id="test-user-id", tools_config=[]
    )
    mock_repository.get_mapping.side_effect = lambda a, u, w: empty_mapping if w == ASSISTANT_SCOPE else None

    configs, has_assistant_scope = service.get_effective_tools_config("test-assistant-id", "test-user-id", "workflow-1")

    assert configs == []
    assert has_assistant_scope is False


def test_clearing_a_slot_in_workflow_scope_records_an_explicit_none(service, mock_repository):
    # "None" is a decision, not the absence of one: inside a workflow it overrides the
    # assistant-wide selection with "no integration" instead of falling back to it.
    assistant_mapping = AssistantUserMappingSQL(
        id="assistant-row",
        assistant_id="test-assistant-id",
        user_id="test-user-id",
        tools_config=[ToolConfig(name="Git", integration_id="assistant-git")],
    )
    workflow_mapping = AssistantUserMappingSQL(
        id="workflow-row",
        assistant_id="test-assistant-id",
        user_id="test-user-id",
        workflow_id="workflow-1",
        tools_config=[ToolConfig(name="Git", integration_id="workflow-git")],
    )
    mock_repository.get_mapping.side_effect = lambda a, u, w: (
        assistant_mapping if w == ASSISTANT_SCOPE else workflow_mapping
    )

    service.create_or_update_mapping(
        assistant_id="test-assistant-id",
        user_id="test-user-id",
        tools_config=[{"name": "Git", "integration_id": ""}],
        workflow_id="workflow-1",
    )

    saved = {tc.name: tc.integration_id for tc in mock_repository.create_or_update_mapping.call_args[0][2]}
    assert saved == {"Git": ""}
    assert mock_repository.create_or_update_mapping.call_args[0][3] == "workflow-1"
