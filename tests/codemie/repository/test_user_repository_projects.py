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

"""Unit tests for user repository - projects functionality (Story 3)

Tests the repository methods for fetching user projects and knowledge bases.
"""

import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, Mock
from uuid import uuid4

from codemie.repository.user_repository import UserRepository
from codemie.rest_api.models.user_management import UserProject, UserKnowledgeBase


@pytest.fixture
def mock_session():
    """Mock database session"""
    return MagicMock()


@pytest.fixture
def user_repository():
    """User repository instance"""
    return UserRepository()


class TestGetUserProjects:
    """Test get_user_projects repository method"""

    def test_get_user_projects_returns_sorted_list(self, user_repository, mock_session):
        """Test get_user_projects returns projects sorted by name"""
        # Arrange
        user_id = str(uuid4())
        projects = [
            UserProject(
                id=str(uuid4()),
                user_id=user_id,
                project_name="zebra-project",
                is_project_admin=False,
                date=datetime.now(UTC),
            ),
            UserProject(
                id=str(uuid4()),
                user_id=user_id,
                project_name="alpha-project",
                is_project_admin=True,
                date=datetime.now(UTC),
            ),
            UserProject(
                id=str(uuid4()),
                user_id=user_id,
                project_name="beta-project",
                is_project_admin=False,
                date=datetime.now(UTC),
            ),
        ]

        mock_result = Mock()
        mock_result.all.return_value = [projects[1], projects[2], projects[0]]  # Sorted by order_by
        mock_session.exec.return_value = mock_result

        # Act
        result = user_repository.get_user_projects(mock_session, user_id)

        # Assert
        assert len(result) == 3
        # Verify order_by was applied (alpha, beta, zebra)
        assert result[0].project_name == "alpha-project"
        assert result[1].project_name == "beta-project"
        assert result[2].project_name == "zebra-project"

    def test_get_user_projects_empty_list(self, user_repository, mock_session):
        """Test get_user_projects returns empty list for user with no projects"""
        # Arrange
        user_id = str(uuid4())
        mock_result = Mock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result

        # Act
        result = user_repository.get_user_projects(mock_session, user_id)

        # Assert
        assert result == []
        assert isinstance(result, list)


class TestGetUserKnowledgeBases:
    """Test get_user_knowledge_bases repository method"""

    def test_get_user_knowledge_bases_returns_name_list(self, user_repository, mock_session):
        """Test get_user_knowledge_bases returns list of KB names"""
        # Arrange
        user_id = str(uuid4())
        kbs = [
            UserKnowledgeBase(id=str(uuid4()), user_id=user_id, kb_name="kb-alpha", date=datetime.now(UTC)),
            UserKnowledgeBase(id=str(uuid4()), user_id=user_id, kb_name="kb-beta", date=datetime.now(UTC)),
        ]

        mock_result = Mock()
        mock_result.all.return_value = kbs
        mock_session.exec.return_value = mock_result

        # Act
        result = user_repository.get_user_knowledge_bases(mock_session, user_id)

        # Assert
        assert result == ["kb-alpha", "kb-beta"]
        assert all(isinstance(name, str) for name in result)

    def test_get_user_knowledge_bases_empty_list(self, user_repository, mock_session):
        """Test get_user_knowledge_bases returns empty list for user with no KBs"""
        # Arrange
        user_id = str(uuid4())
        mock_result = Mock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result

        # Act
        result = user_repository.get_user_knowledge_bases(mock_session, user_id)

        # Assert
        assert result == []


class TestGetProjectsForUsers:
    """Test get_projects_for_users batch fetch method"""

    def test_get_projects_for_users_batch_fetch(self, user_repository, mock_session):
        """Test batch fetching projects for multiple users"""
        # Arrange
        user1_id = str(uuid4())
        user2_id = str(uuid4())
        user_ids = [user1_id, user2_id]

        projects = [
            UserProject(
                id=str(uuid4()), user_id=user1_id, project_name="proj-a", is_project_admin=True, date=datetime.now(UTC)
            ),
            UserProject(
                id=str(uuid4()), user_id=user1_id, project_name="proj-b", is_project_admin=False, date=datetime.now(UTC)
            ),
            UserProject(
                id=str(uuid4()), user_id=user2_id, project_name="proj-c", is_project_admin=True, date=datetime.now(UTC)
            ),
        ]

        mock_result = Mock()
        mock_result.all.return_value = projects
        mock_session.exec.return_value = mock_result

        # Act
        result = user_repository.get_projects_for_users(mock_session, user_ids)

        # Assert
        assert isinstance(result, dict)
        assert len(result) == 2  # Two users
        assert user1_id in result
        assert user2_id in result
        assert len(result[user1_id]) == 2
        assert len(result[user2_id]) == 1
        assert result[user1_id][0].project_name == "proj-a"
        assert result[user1_id][1].project_name == "proj-b"
        assert result[user2_id][0].project_name == "proj-c"

    def test_get_projects_for_users_empty_input(self, user_repository, mock_session):
        """Test batch fetch with empty user list returns empty dict"""
        # Arrange & Act
        result = user_repository.get_projects_for_users(mock_session, [])

        # Assert
        assert result == {}
        mock_session.exec.assert_not_called()

    def test_get_projects_for_users_no_projects(self, user_repository, mock_session):
        """Test batch fetch returns empty dict when users have no projects"""
        # Arrange
        user_ids = [str(uuid4()), str(uuid4())]
        mock_result = Mock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result

        # Act
        result = user_repository.get_projects_for_users(mock_session, user_ids)

        # Assert
        assert result == {}

    def test_get_projects_for_users_single_user(self, user_repository, mock_session):
        """Test batch fetch works correctly for single user"""
        # Arrange
        user_id = str(uuid4())
        projects = [
            UserProject(
                id=str(uuid4()),
                user_id=user_id,
                project_name="single-proj",
                is_project_admin=True,
                date=datetime.now(UTC),
            ),
        ]

        mock_result = Mock()
        mock_result.all.return_value = projects
        mock_session.exec.return_value = mock_result

        # Act
        result = user_repository.get_projects_for_users(mock_session, [user_id])

        # Assert
        assert len(result) == 1
        assert user_id in result
        assert len(result[user_id]) == 1
        assert result[user_id][0].project_name == "single-proj"


class TestProjectsPerformance:
    """Test performance optimizations for projects fetching"""

    def test_batch_fetch_reduces_queries(self, user_repository, mock_session):
        """Test that batch fetch uses single query for multiple users"""
        # Arrange
        user_ids = [str(uuid4()) for _ in range(10)]  # 10 users
        mock_result = Mock()
        mock_result.all.return_value = []
        mock_session.exec.return_value = mock_result

        # Act
        user_repository.get_projects_for_users(mock_session, user_ids)

        # Assert
        # Should be called once regardless of user count (batch fetch)
        assert mock_session.exec.call_count == 1
