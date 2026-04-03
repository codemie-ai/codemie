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

import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from codemie.core.ability import Action
from codemie.core.workflow_models import (
    WorkflowExecution,
    WorkflowExecutionStatusEnum,
    WorkflowExecutionStateThoughtWithChildren,
    WorkflowExecutionStateThought,
    WorkflowExecutionStateThoughtShort,
)
from codemie.rest_api.security.user import User
from codemie.core.models import UserEntity


class TestWorkflowExecutionStateThought:
    @pytest.fixture
    def mock_thought(self):
        return WorkflowExecutionStateThought(
            id='state1',
            execution_state_id='123',
            author_name='author1',
            author_type='user',
            content='content1',
            date=datetime.now(),
        )

    @patch('codemie.core.workflow_models.workflow_execution.Session')
    def test_get_root(self, mock_session_class, mock_thought):
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.all.return_value = [mock_thought]

        result = WorkflowExecutionStateThought.get_root(state_ids=["state1", "state2"], include_children_field=True)
        assert isinstance(result[0], WorkflowExecutionStateThoughtWithChildren)

        result = WorkflowExecutionStateThought.get_root(state_ids=["state1", "state2"], include_children_field=False)
        assert isinstance(result[0], WorkflowExecutionStateThoughtShort)

    @patch('codemie.core.workflow_models.workflow_execution.Session')
    def test_get_all(self, mock_session_class, mock_thought):
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.all.return_value = [mock_thought]

        result = WorkflowExecutionStateThought.get_all(ids=['123'])
        assert isinstance(result[0], WorkflowExecutionStateThoughtWithChildren)

    @patch('codemie.core.workflow_models.workflow_execution.Session')
    def test_get_all_by_parent_ids(self, mock_session_class, mock_thought):
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.all.return_value = [mock_thought]

        result = WorkflowExecutionStateThought.get_all_by_parent_ids(parent_ids=['123'])
        assert isinstance(result[0], WorkflowExecutionStateThoughtWithChildren)


class TestWorkflowExecution:
    @pytest.fixture
    def mock_workflow_execution(self):
        return WorkflowExecution(
            id="execution_123",
            workflow_id="workflow_123",
            execution_id="exec_123",
            overall_status=WorkflowExecutionStatusEnum.NOT_STARTED,
            project="test_project",
            created_by=UserEntity(user_id="user_123", username="test-user"),
        )

    @pytest.fixture
    def mock_user(self):
        return User(
            id="user_123",
            project_names=["test_project"],
            admin_project_names=["test_project"],
        )

    def test_get_by_workflow_id(self, mock_workflow_execution):
        with patch.object(WorkflowExecution, 'get_all_by_fields') as mock_get_all:
            mock_get_all.return_value = [mock_workflow_execution]

            result = WorkflowExecution.get_by_workflow_id('workflow_123')
            assert len(result) == 1
            assert isinstance(result[0], WorkflowExecution)
            assert result[0].id == mock_workflow_execution.id
            mock_get_all.assert_called_once_with({"workflow_id": "workflow_123"})

    def test_get_by_execution_id(self, mock_workflow_execution):
        with patch.object(WorkflowExecution, 'get_all_by_fields') as mock_get_all:
            mock_get_all.return_value = [mock_workflow_execution]

            result = WorkflowExecution.get_by_execution_id('exec_123')
            assert len(result) == 1
            assert isinstance(result[0], WorkflowExecution)
            assert result[0].id == mock_workflow_execution.id
            mock_get_all.assert_called_once_with({"execution_id": "exec_123"})

    @patch('codemie.core.workflow_models.workflow_execution.Session')
    def test_delete(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session

        # Test successful delete
        mock_execution = WorkflowExecution(id='execution_123', workflow_id='workflow_123', execution_id='exec_123')
        mock_session.get.return_value = mock_execution

        result = WorkflowExecution.delete('execution_123')
        assert result == {"status": "deleted"}
        mock_session.delete.assert_called_once_with(mock_execution)
        mock_session.commit.assert_called_once()

        # Test delete non-existent
        mock_session.reset_mock()
        mock_session.get.return_value = None
        result = WorkflowExecution.delete('non_existent')
        assert result == {"status": "not found"}
        mock_session.delete.assert_not_called()
        mock_session.commit.assert_not_called()

    @patch('codemie.core.workflow_models.workflow_execution.Session')
    def test_delete_cascades_to_states_and_thoughts(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session

        mock_execution = WorkflowExecution(id='exec-pk-id', workflow_id='wf-1', execution_id='exec-001')
        mock_session.get.return_value = mock_execution

        state1 = MagicMock()
        state1.id = 's1'
        state2 = MagicMock()
        state2.id = 's2'
        mock_session.exec.return_value.all.return_value = [state1, state2]

        result = WorkflowExecution.delete('exec-pk-id')

        assert result == {"status": "deleted"}
        # exec called at least twice: once for states SELECT, once for thoughts DELETE, once for states DELETE
        assert mock_session.exec.call_count >= 3
        mock_session.delete.assert_called_once_with(mock_execution)
        mock_session.commit.assert_called_once()

    @patch('codemie.core.workflow_models.workflow_execution.Session')
    def test_delete_with_no_states_does_not_error(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session

        mock_execution = WorkflowExecution(id='exec-pk-id', workflow_id='wf-1', execution_id='exec-001')
        mock_session.get.return_value = mock_execution
        # No states exist
        mock_session.exec.return_value.all.return_value = []

        result = WorkflowExecution.delete('exec-pk-id')

        assert result == {"status": "deleted"}
        mock_session.delete.assert_called_once_with(mock_execution)
        mock_session.commit.assert_called_once()

    @patch('codemie.core.workflow_models.workflow_execution.Session')
    def test_delete_not_found_returns_status(self, mock_session_class):
        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = None

        result = WorkflowExecution.delete('nonexistent')

        assert result == {"status": "not found"}
        mock_session.exec.assert_not_called()
        mock_session.delete.assert_not_called()
        mock_session.commit.assert_not_called()

    def test_start_progress(self, mock_workflow_execution):
        with patch.object(WorkflowExecution, 'save') as mock_save:
            mock_workflow_execution.start_progress()
            assert mock_workflow_execution.overall_status == WorkflowExecutionStatusEnum.IN_PROGRESS
            mock_save.assert_called_once()

    def test_is_owned_by(self, mock_workflow_execution, mock_user):
        assert mock_workflow_execution.is_owned_by(mock_user) is True

        other_user = User(id="other_user", project_names=["test_project"])
        assert mock_workflow_execution.is_owned_by(other_user) is False

    def test_is_managed_by(self, mock_workflow_execution, mock_user):
        assert mock_workflow_execution.is_managed_by(mock_user) is True

        non_admin_user = User(id="non_admin", project_names=["test_project"], admin_project_names=[])
        assert mock_workflow_execution.is_managed_by(non_admin_user) is False

    def test_is_shared_with(self, mock_workflow_execution, mock_user):
        assert mock_workflow_execution.is_shared_with(mock_user) is True

        other_user = User(id="other_user", project_names=["other_project"])
        assert mock_workflow_execution.is_shared_with(other_user) is False

    @patch('codemie.core.workflow_models.workflow_execution.Ability')
    def test_get_by_workflow_id_with_user(self, mock_ability, mock_workflow_execution, mock_user):
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        with patch.object(WorkflowExecution, 'get_all_by_fields') as mock_get_all:
            mock_get_all.return_value = [mock_workflow_execution]

            result = WorkflowExecution.get_by_workflow_id('workflow_123', user=mock_user)
            assert len(result) == 1
            assert isinstance(result[0], WorkflowExecution)
            assert result[0].id == mock_workflow_execution.id
            mock_get_all.assert_called_once_with({"workflow_id": "workflow_123"})

            # Verify ability check was called
            mock_ability.assert_called_once_with(mock_user)
            mock_ability_instance.can.assert_called_once_with(Action.READ, mock_workflow_execution)

            # Test when user doesn't have permission
            mock_ability_instance.can.return_value = False
            result = WorkflowExecution.get_by_workflow_id('workflow_123', user=mock_user)
            assert len(result) == 0


class TestWorkflowExecutionStateResponseStateId:
    """Tests for state_id field on WorkflowExecutionStateResponse."""

    def test_state_id_uses_db_value_when_set(self):
        """When state_id is stored in DB (new records), it is returned as-is."""
        from codemie.core.workflow_models.workflow_execution import WorkflowExecutionStateResponse

        response = WorkflowExecutionStateResponse(
            execution_id="exec-1",
            name="assistant_2 1 of 5",
            state_id="assistant_2",
        )

        assert response.state_id == "assistant_2"

    def test_state_id_not_overridden_when_explicitly_provided(self):
        """Explicitly provided state_id is never overridden by model_post_init."""
        from codemie.core.workflow_models.workflow_execution import WorkflowExecutionStateResponse

        response = WorkflowExecutionStateResponse(
            execution_id="exec-1",
            name="assistant_2 3 of 7",
            state_id="assistant_2",
        )

        assert response.state_id == "assistant_2"
        assert response.name == "assistant_2 3 of 7"
