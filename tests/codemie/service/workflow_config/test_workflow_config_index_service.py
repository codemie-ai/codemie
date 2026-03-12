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
from unittest.mock import patch, MagicMock
from codemie.rest_api.security.user import User
from codemie.service.workflow_config.workflow_config_index_service import WorkflowConfigIndexService


@pytest.fixture
def mock_admin_user():
    return User(id='test', roles=['admin'])


@pytest.fixture
def mock_user():
    return User(id='test_user', roles=['user'], project_names=['demo'])


@patch('codemie.service.workflow_config.workflow_config_index_service.Session')
def test_workflow_config_index_service_filter_by_user(mock_session_class, mock_admin_user):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []
    mock_session.exec.return_value.one.return_value = 0

    WorkflowConfigIndexService.run(user=mock_admin_user, filter_by_user=True, page=1, per_page=20)

    actual_query = str(mock_session.exec.call_args[0][0])
    expected_conditions = "WHERE workflows.created_by[:created_by_1] = :param_1 AND workflows.mode = :mode_1 ORDER BY workflows.update_date DESC NULLS LAST\n LIMIT :param_2 OFFSET :param_3"

    assert actual_query.endswith(expected_conditions)


@patch('codemie.service.workflow_config.workflow_config_index_service.Session')
def test_workflow_config_index_service_all_for_admin(mock_session_class, mock_admin_user):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []
    mock_session.exec.return_value.one.return_value = 0

    WorkflowConfigIndexService.run(user=mock_admin_user, filter_by_user=False, page=0, per_page=20)

    # For admin, no additional where clauses should be added
    actual_query = str(mock_session.exec.call_args[0][0])
    expected_conditions = "FROM workflows \nWHERE workflows.mode = :mode_1 ORDER BY workflows.update_date DESC NULLS LAST\n LIMIT :param_1 OFFSET :param_2"

    assert actual_query.endswith(expected_conditions)


@patch('codemie.service.workflow_config.workflow_config_index_service.Session')
def test_workflow_config_index_service_all_for_user(mock_session_class, mock_user):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = []
    mock_session.exec.return_value.one.return_value = 0

    # Mock is_admin to return False for this test
    with patch.object(type(mock_user), 'is_admin', new_callable=lambda: property(lambda self: False)):
        WorkflowConfigIndexService.run(user=mock_user, filter_by_user=False, page=0, per_page=20)

        # Verify complex query for regular user
        actual_query = str(mock_session.exec.call_args[0][0])
        expected_conditions = "WHERE (workflows.project IN (__[POSTCOMPILE_project_1]) AND workflows.shared OR workflows.project IN (__[POSTCOMPILE_project_2]) OR (workflows.created_by ->> :created_by_1) = :param_1) AND workflows.mode = :mode_1 ORDER BY workflows.update_date DESC NULLS LAST\n LIMIT :param_2 OFFSET :param_3"
        assert actual_query.endswith(expected_conditions)


@patch('codemie.service.workflow_config.workflow_config_index_service.Session')
def test_get_users_for_admin(mock_session_class, mock_admin_user):
    """Test get_users() returns all distinct workflow creators for admin users"""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    # Mock database response - these are CreatedByUser objects from workflow's created_by field
    # The created_by field stores objects with user_id, username, and name
    mock_creator1 = MagicMock()
    mock_creator1.user_id = "user1"
    mock_creator1.username = "user1"
    mock_creator1.name = "User One"

    mock_creator2 = MagicMock()
    mock_creator2.user_id = "user2"
    mock_creator2.username = "user2"
    mock_creator2.name = "User Two"

    mock_session.exec.return_value.all.return_value = [mock_creator1, mock_creator2]

    result = WorkflowConfigIndexService.get_users(user=mock_admin_user)

    assert len(result) == 2
    assert result[0].id == "user1"
    assert result[0].username == "user1"
    assert result[0].name == "User One"
    assert result[1].id == "user2"
    assert result[1].username == "user2"
    assert result[1].name == "User Two"

    # Verify query was called
    mock_session.exec.assert_called_once()
    actual_query = str(mock_session.exec.call_args[0][0])

    # Admin should see all users, so only mode filter should be applied
    assert "SELECT DISTINCT workflows.created_by" in actual_query
    assert "workflows.mode = :mode_1" in actual_query


@patch('codemie.service.workflow_config.workflow_config_index_service.Session')
def test_get_users_for_regular_user(mock_session_class, mock_user):
    """Test get_users() returns only visible workflow creators for regular users"""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    # Mock database response - these are creator objects from workflow's created_by field
    mock_creator = MagicMock()
    mock_creator.user_id = "user1"
    mock_creator.username = "user1"
    mock_creator.name = "User One"

    mock_session.exec.return_value.all.return_value = [mock_creator]

    # Mock is_admin to return False for this test
    with patch.object(type(mock_user), 'is_admin', new_callable=lambda: property(lambda self: False)):
        result = WorkflowConfigIndexService.get_users(user=mock_user)

        assert len(result) == 1
        assert result[0].id == "user1"
        assert result[0].username == "user1"
        assert result[0].name == "User One"

        # Verify query includes visibility filters for regular user
        actual_query = str(mock_session.exec.call_args[0][0])
        assert "SELECT DISTINCT workflows.created_by" in actual_query
        # Should include project/shared conditions for non-admin
        assert "workflows.project" in actual_query


@patch('codemie.service.workflow_config.workflow_config_index_service.Session')
def test_get_users_filters_out_empty_names(mock_session_class, mock_admin_user):
    """Test get_users() filters out users with empty names"""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    # Mock response with some users having empty names and None values
    mock_creator1 = MagicMock()
    mock_creator1.user_id = "user1"
    mock_creator1.username = "user1"
    mock_creator1.name = "User One"

    mock_creator2 = MagicMock()
    mock_creator2.user_id = "user2"
    mock_creator2.username = "user2"
    mock_creator2.name = ""  # Empty name - should be filtered out

    mock_creator3 = MagicMock()
    mock_creator3.user_id = "user3"
    mock_creator3.username = "user3"
    mock_creator3.name = "User Three"

    mock_session.exec.return_value.all.return_value = [
        mock_creator1,
        mock_creator2,
        mock_creator3,
        None,  # None value - should be filtered out
    ]

    result = WorkflowConfigIndexService.get_users(user=mock_admin_user)

    # Should only return users with non-empty names
    assert len(result) == 2
    assert result[0].id == "user1"
    assert result[0].name == "User One"
    assert result[1].id == "user3"
    assert result[1].name == "User Three"

    # Should filter out None and users with empty names
    assert all(creator and creator.name for creator in result)
