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
from datetime import timezone as tz
from unittest.mock import MagicMock, patch

from codemie.core.models import CreatedByUser
from codemie.rest_api.models.assistant import Assistant, AssistantListResponse
from codemie.service.assistant.assistant_repository import AssistantRepository, AssistantScope


@pytest.fixture
def mock_admin_user():
    user = MagicMock()
    user.is_admin = True
    user.project_names = ["DEMO_PROJECT"]
    user.admin_project_names = []
    user.id = "admin_user"
    return user


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.is_admin = False
    user.is_external_user = False
    user.project_names = ["DEMO_PROJECT"]
    user.admin_project_names = []
    user.id = "test_user"
    return user


@pytest.fixture
def mock_external_user():
    user = MagicMock()
    user.is_admin = False
    user.is_external_user = True
    user.project_names = ["DEMO_PROJECT"]
    user.admin_project_names = []
    user.id = "external_user"
    user.user_type = "external"
    return user


@pytest.fixture
def mock_assistant():
    return Assistant(
        id="test_id",
        icon_url="test icon url",
        name="test name",
        description="test description",
        system_prompt="test system prompt",
        system_prompt_history=[],
        project="DEMO_PROJECT",
        toolkits=[],
        shared=True,
        is_react=True,
        is_global=False,
        created_date="2022-01-02T00:00:00.000Z",
        agent_mode="general",
        creator="system",
        update_date="2022-01-02T00:00:00.000Z",
    )


@patch("codemie.service.assistant.assistant_repository.Session")
def test_query_visible_to_admin_user(mock_session_class, mock_admin_user, mock_assistant):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_assistant]
    mock_session.exec.return_value.one.return_value = 1

    result = AssistantRepository().query(
        user=mock_admin_user,
        scope=AssistantScope.VISIBLE_TO_USER,
        filters={"project": ["DEMO_PROJECT"]},
        page=1,
        per_page=10,
        minimal_response=False,
    )

    assert isinstance(result["data"][0], Assistant)
    assert len(result["data"]) == 1
    assert result["pagination"]["total"] == 1


@patch("codemie.service.assistant.assistant_repository.Session")
def test_query_visible_to_user(mock_session_class, mock_user, mock_assistant):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_assistant]
    mock_session.exec.return_value.one.return_value = 1

    result = AssistantRepository().query(
        user=mock_user,
        scope=AssistantScope.VISIBLE_TO_USER,
        filters={"project": ["DEMO_PROJECT"]},
        page=1,
        per_page=10,
        minimal_response=False,
    )

    assert isinstance(result["data"][0], Assistant)
    assert len(result["data"]) == 1
    assert result["pagination"]["total"] == 1


@patch("codemie.service.assistant.assistant_repository.Session")
def test_query_created_by_user(mock_session_class, mock_user, mock_assistant):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_assistant]
    mock_session.exec.return_value.one.return_value = 1

    result = AssistantRepository().query(
        user=mock_user,
        scope=AssistantScope.CREATED_BY_USER,
        filters={"project": ["DEMO_PROJECT"]},
        page=1,
        per_page=10,
        minimal_response=False,
    )

    assert isinstance(result["data"][0], Assistant)
    assert len(result["data"]) == 1
    assert result["pagination"]["total"] == 1


@patch("codemie.service.assistant.assistant_repository.Session")
def test_query_minimal_response(mock_session_class, mock_user, mock_assistant):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_assistant]
    mock_session.exec.return_value.one.return_value = 1

    result = AssistantRepository().query(
        user=mock_user,
        scope=AssistantScope.CREATED_BY_USER,
        filters={"project": ["DEMO_PROJECT"]},
        page=1,
        per_page=10,
        minimal_response=True,
    )

    assert isinstance(result["data"][0], AssistantListResponse)
    assert len(result["data"]) == 1
    assert result["pagination"]["total"] == 1


@patch("codemie.service.assistant.assistant_repository.Session")
def test_query_with_search(mock_session_class, mock_user, mock_assistant):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [mock_assistant]
    mock_session.exec.return_value.one.return_value = 1

    result = AssistantRepository().query(
        user=mock_user,
        scope=AssistantScope.CREATED_BY_USER,
        filters={"project": ["DEMO_PROJECT"], "search": "test search"},
        page=1,
        per_page=10,
        minimal_response=False,
    )

    assert isinstance(result["data"][0], Assistant)
    assert len(result["data"]) == 1
    assert result["pagination"]["total"] == 1


@patch("codemie.service.assistant.assistant_repository.Session")
@patch('codemie.service.assistant.assistant_repository.Assistant', Assistant)
def test_get_users_postgres(mock_session_class, mock_user, mock_assistant):
    # Set up return values for database query
    mock_users = [
        CreatedByUser(id="user1", username="user1", name="User One"),
        CreatedByUser(id="user2", username="user2", name="User Two"),
        CreatedByUser(id="user3", username="user3", name="User Three"),
    ]
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = mock_users
    mock_session.exec.return_value.one.return_value = 3

    # Configure session context manager
    repository = AssistantRepository()
    # Call the method
    users = repository.get_users(mock_user, AssistantScope.VISIBLE_TO_USER)
    # Validate results
    assert len(users) == 3
    assert users == mock_users


# External User Tests
@patch("codemie.service.assistant.assistant_repository.Session")
def test_query_marketplace_external_user_filters_by_project(mock_session_class, mock_external_user):
    """Test that external users only see marketplace assistants from allowed projects"""
    # Create assistants from different projects
    assistant_codemie = Assistant(
        id="codemie_assist",
        name="CodeMie Assistant",
        description="CodeMie test assistant",
        project="codemie",
        is_global=True,
        system_prompt="test",
        toolkits=[],
        creator="system",
    )
    assistant_epm = Assistant(
        id="epm_assist",
        name="EPM Assistant",
        description="EPM test assistant",
        project="your-project",
        is_global=True,
        system_prompt="test",
        toolkits=[],
        creator="system",
    )

    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    # External users should only see codemie and your-project assistants
    mock_session.exec.return_value.all.return_value = [assistant_codemie, assistant_epm]
    mock_session.exec.return_value.one.return_value = 2

    result = AssistantRepository().query(user=mock_external_user, scope=AssistantScope.MARKETPLACE, page=0, per_page=10)

    assert len(result["data"]) == 2
    assert result["pagination"]["total"] == 2
    # Verify the query was constructed to filter by project
    assert mock_session.exec.called


@patch("codemie.service.assistant.assistant_repository.Session")
def test_query_all_scope_external_user_filters_by_project(mock_session_class, mock_external_user):
    """Test that external users with scope=all only see allowed marketplace assistants"""
    assistant_codemie = Assistant(
        id="codemie_assist",
        name="CodeMie Assistant",
        description="CodeMie test assistant",
        project="codemie",
        is_global=True,
        system_prompt="test",
        toolkits=[],
        creator="system",
    )

    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [assistant_codemie]
    mock_session.exec.return_value.one.return_value = 1

    result = AssistantRepository().query(user=mock_external_user, scope=AssistantScope.ALL, page=0, per_page=10)

    assert len(result["data"]) == 1
    assert result["pagination"]["total"] == 1


@patch("codemie.service.assistant.assistant_repository.Session")
def test_query_visible_to_user_external_user_filters_by_project(mock_session_class, mock_external_user):
    """Test that external users with visible_to_user scope only see allowed marketplace assistants"""
    assistant_codemie = Assistant(
        id="codemie_assist",
        name="CodeMie Assistant",
        description="CodeMie test assistant",
        project="codemie",
        is_global=True,
        system_prompt="test",
        toolkits=[],
        creator="system",
    )

    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.all.return_value = [assistant_codemie]
    mock_session.exec.return_value.one.return_value = 1

    result = AssistantRepository().query(
        user=mock_external_user, scope=AssistantScope.VISIBLE_TO_USER, page=0, per_page=10
    )

    assert len(result["data"]) == 1
    assert result["pagination"]["total"] == 1


@patch("codemie.service.assistant.assistant_repository.Session")
def test_query_marketplace_internal_user_sees_all_projects(mock_session_class, mock_user):
    """Test that internal users see all marketplace assistants regardless of project"""
    assistant_codemie = Assistant(
        id="codemie_assist",
        name="CodeMie Assistant",
        description="CodeMie test assistant",
        project="codemie",
        is_global=True,
        system_prompt="test",
        toolkits=[],
        creator="system",
    )
    assistant_other = Assistant(
        id="other_assist",
        name="Other Assistant",
        description="Other test assistant",
        project="other-project",
        is_global=True,
        system_prompt="test",
        toolkits=[],
        creator="system",
    )

    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    # Internal users should see all marketplace assistants
    mock_session.exec.return_value.all.return_value = [assistant_codemie, assistant_other]
    mock_session.exec.return_value.one.return_value = 2

    result = AssistantRepository().query(user=mock_user, scope=AssistantScope.MARKETPLACE, page=0, per_page=10)

    assert len(result["data"]) == 2
    assert result["pagination"]["total"] == 2


@patch("codemie.service.assistant.assistant_repository.Session")
def test_external_user_cannot_see_non_global_assistants(mock_session_class, mock_external_user):
    """Test that external users cannot see non-global assistants even in their project"""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    # External users should not see any non-global assistants
    mock_session.exec.return_value.all.return_value = []
    mock_session.exec.return_value.one.return_value = 0

    result = AssistantRepository().query(
        user=mock_external_user, scope=AssistantScope.VISIBLE_TO_USER, page=0, per_page=10
    )

    assert len(result["data"]) == 0
    assert result["pagination"]["total"] == 0


class TestEnrichSystemPromptHistory:
    """Tests for the enrich_system_prompt_history method"""

    @pytest.fixture
    def mock_assistant_with_id(self):
        """Create a mock assistant with ID and version_count"""
        assistant = MagicMock(spec=Assistant)
        assistant.id = "test-assistant-id"
        assistant.system_prompt = "Current system prompt"
        assistant.version_count = 5
        return assistant

    @pytest.fixture
    def mock_assistant_without_id(self):
        """Create a mock assistant without ID"""
        assistant = MagicMock(spec=Assistant)
        assistant.id = None
        assistant.system_prompt = "Current system prompt"
        return assistant

    def test_enrich_system_prompt_history_no_assistant_id(self, mock_assistant_without_id):
        """Test that method returns early if assistant has no ID"""
        result = AssistantRepository.enrich_system_prompt_history(mock_assistant_without_id)

        assert result == mock_assistant_without_id

    @patch('codemie.rest_api.models.assistant.AssistantConfiguration')
    def test_enrich_system_prompt_history_no_configs(self, mock_config_class, mock_assistant_with_id):
        """Test with no version configurations"""
        mock_config_class.get_version_history.return_value = []

        result = AssistantRepository.enrich_system_prompt_history(mock_assistant_with_id)

        assert result.system_prompt_history == []
        mock_config_class.get_version_history.assert_called_once_with(
            assistant_id="test-assistant-id", page=0, per_page=1000
        )

    @patch('codemie.rest_api.models.assistant.AssistantConfiguration')
    def test_enrich_system_prompt_history_all_same_prompts(self, mock_config_class, mock_assistant_with_id):
        """Test when all versions have the same system_prompt - should return empty history"""
        # Create mock configs with the same system_prompt
        mock_configs = [
            MagicMock(
                version_number=5,
                system_prompt="Current system prompt",
                created_date=datetime(2024, 1, 5, tzinfo=tz.utc),
                created_by=CreatedByUser(id="user1", username="user1", name="User One"),
            ),
            MagicMock(
                version_number=4,
                system_prompt="Current system prompt",
                created_date=datetime(2024, 1, 4, tzinfo=tz.utc),
                created_by=CreatedByUser(id="user2", username="user2", name="User Two"),
            ),
            MagicMock(
                version_number=3,
                system_prompt="Current system prompt",
                created_date=datetime(2024, 1, 3, tzinfo=tz.utc),
                created_by=CreatedByUser(id="user3", username="user3", name="User Three"),
            ),
        ]
        mock_config_class.get_version_history.return_value = mock_configs

        result = AssistantRepository.enrich_system_prompt_history(mock_assistant_with_id)

        # Should return empty history since all prompts are the same
        assert result.system_prompt_history == []

    @patch('codemie.rest_api.models.assistant.AssistantConfiguration')
    def test_enrich_system_prompt_history_with_changes(self, mock_config_class, mock_assistant_with_id):
        """Test with system_prompt changes - should only include changed versions"""
        # Create mock configs with different system_prompts
        mock_configs = [
            MagicMock(
                version_number=5,  # Current version - should be skipped
                system_prompt="Current system prompt",
                created_date=datetime(2024, 1, 5, tzinfo=tz.utc),
                created_by=CreatedByUser(id="user1", username="user1", name="User One"),
            ),
            MagicMock(
                version_number=4,  # Same as current - should be skipped
                system_prompt="Current system prompt",
                created_date=datetime(2024, 1, 4, tzinfo=tz.utc),
                created_by=CreatedByUser(id="user2", username="user2", name="User Two"),
            ),
            MagicMock(
                version_number=3,  # Different - should be included
                system_prompt="Previous system prompt",
                created_date=datetime(2024, 1, 3, tzinfo=tz.utc),
                created_by=CreatedByUser(id="user3", username="user3", name="User Three"),
            ),
            MagicMock(
                version_number=2,  # Same as v3 - should be skipped
                system_prompt="Previous system prompt",
                created_date=datetime(2024, 1, 2, tzinfo=tz.utc),
                created_by=CreatedByUser(id="user4", username="user4", name="User Four"),
            ),
            MagicMock(
                version_number=1,  # Different - should be included
                system_prompt="Original system prompt",
                created_date=datetime(2024, 1, 1, tzinfo=tz.utc),
                created_by=CreatedByUser(id="user5", username="user5", name="User Five"),
            ),
        ]
        mock_config_class.get_version_history.return_value = mock_configs

        result = AssistantRepository.enrich_system_prompt_history(mock_assistant_with_id)

        # Should only include versions 3 and 1 (where prompts changed)
        assert len(result.system_prompt_history) == 2
        assert result.system_prompt_history[0].system_prompt == "Previous system prompt"
        assert result.system_prompt_history[0].created_by.name == "User Three"
        assert result.system_prompt_history[1].system_prompt == "Original system prompt"
        assert result.system_prompt_history[1].created_by.name == "User Five"

    @patch('codemie.rest_api.models.assistant.AssistantConfiguration')
    def test_enrich_system_prompt_history_skips_current_version(self, mock_config_class, mock_assistant_with_id):
        """Test that current version is always skipped"""
        mock_configs = [
            MagicMock(
                version_number=5,  # Current version - should be skipped
                system_prompt="Different prompt",  # Even if different, should be skipped
                created_date=datetime(2024, 1, 5, tzinfo=tz.utc),
                created_by=CreatedByUser(id="user1", username="user1", name="User One"),
            ),
        ]
        mock_config_class.get_version_history.return_value = mock_configs

        result = AssistantRepository.enrich_system_prompt_history(mock_assistant_with_id)

        # Current version should always be skipped
        assert result.system_prompt_history == []

    @patch('codemie.configs.logger.logger')
    @patch('codemie.rest_api.models.assistant.AssistantConfiguration')
    def test_enrich_system_prompt_history_error_handling(self, mock_config_class, mock_logger, mock_assistant_with_id):
        """Test error handling when get_version_history fails"""
        mock_config_class.get_version_history.side_effect = Exception("Database error")

        result = AssistantRepository.enrich_system_prompt_history(mock_assistant_with_id)

        # Should handle error gracefully and return the assistant
        assert result == mock_assistant_with_id
        mock_logger.warning.assert_called_once()
        assert "Failed to enrich system_prompt_history" in str(mock_logger.warning.call_args)

    @patch('codemie.rest_api.models.assistant.AssistantConfiguration')
    def test_enrich_system_prompt_history_preserves_metadata(self, mock_config_class, mock_assistant_with_id):
        """Test that created_by and date are preserved correctly"""
        mock_configs = [
            MagicMock(
                version_number=5,
                system_prompt="Current system prompt",
                created_date=datetime(2024, 1, 5, tzinfo=tz.utc),
                created_by=CreatedByUser(id="user1", username="user1", name="User One"),
            ),
            MagicMock(
                version_number=4,
                system_prompt="Old prompt",
                created_date=datetime(2024, 1, 4, 10, 30, 0, tzinfo=tz.utc),
                created_by=CreatedByUser(id="user2", username="john.doe", name="John Doe"),
            ),
        ]
        mock_config_class.get_version_history.return_value = mock_configs

        result = AssistantRepository.enrich_system_prompt_history(mock_assistant_with_id)

        assert len(result.system_prompt_history) == 1
        history_entry = result.system_prompt_history[0]
        assert history_entry.system_prompt == "Old prompt"
        assert history_entry.date == datetime(2024, 1, 4, 10, 30, 0, tzinfo=tz.utc)
        assert history_entry.created_by.id == "user2"
        assert history_entry.created_by.username == "john.doe"
        assert history_entry.created_by.name == "John Doe"


class TestProjectWithMarketplaceQuery:
    """Tests for PROJECT_WITH_MARKETPLACE scope - verifies filter logic"""

    @pytest.fixture
    def repository(self):
        return AssistantRepository()

    @pytest.mark.parametrize(
        "is_external,expected_has_project_filter",
        [
            (False, False),  # Internal user: no project filter for global assistants
            (True, True),  # External user: project filter applied for global assistants
        ],
    )
    @patch('codemie.service.assistant.assistant_repository.config')
    def test_marketplace_condition_project_filtering(
        self, mock_config, repository, is_external, expected_has_project_filter
    ):
        """Verify that marketplace condition applies project filtering only for external users"""
        mock_config.EXTERNAL_USER_ALLOWED_PROJECTS = ["codemie"]

        user = MagicMock()
        user.is_external_user = is_external
        user.project_names = ["APP1"]

        condition = repository._get_marketplace_condition(user)
        condition_str = str(condition).lower()

        # All users should have is_global check
        assert "is_global" in condition_str

        # Only external users should have project filtering
        has_project_filter = " in(" in condition_str or " in " in condition_str
        assert has_project_filter == expected_has_project_filter

    @pytest.mark.parametrize(
        "is_admin,project,has_user_guard",
        [
            (True, None, False),  # Admin without project: sees all non-global
            (True, "PROJECT1", False),  # Admin with project: sees all non-global (no restrictions)
            (False, "PROJECT1", True),  # Regular user with project: project filter + user guard
            (False, None, True),  # Regular user without project: user guard applies
        ],
    )
    def test_non_global_base_condition_user_guard(self, repository, is_admin, project, has_user_guard):
        """Verify non-global condition applies user guard (shared/admin/created_by) for non-admins"""
        user = MagicMock()
        user.is_admin = is_admin
        user.project_names = ["APP1"]
        user.admin_project_names = ["APP2"]
        user.id = "user123"

        condition = repository._get_non_global_base_condition(user, project=project)
        condition_str = str(condition).lower()

        # All conditions should check is_global=False
        assert "is_global" in condition_str

        # Check for user guard (shared AND applications OR applications_admin OR created_by)
        # User guard is present if we see visibility checks like "shared" or complex OR conditions
        has_guard = "shared" in condition_str and " or " in condition_str
        assert has_guard == has_user_guard

    @pytest.mark.parametrize(
        "is_admin",
        [False, True],
    )
    def test_non_global_with_project_includes_project_filter(self, repository, is_admin):
        """Verify that when project is specified, it's included in the condition for non-admin users"""
        user = MagicMock()
        user.is_admin = is_admin
        user.project_names = ["APP1"]
        user.admin_project_names = ["APP2"]
        user.id = "user123"

        condition = repository._get_non_global_base_condition(user, project="TARGET_PROJECT")
        condition_str = str(condition).lower()

        # For admins: no project filter (they see all non-global)
        # For regular users: should check project filter
        if not is_admin:
            assert "project" in condition_str or "target_project" in condition_str

    @patch("codemie.service.assistant.assistant_repository.Session")
    def test_project_with_marketplace_combines_global_and_non_global(self, mock_session_class, repository, mock_user):
        """PROJECT_WITH_MARKETPLACE should return both project and marketplace assistants"""
        non_global = Assistant(
            id="ng1",
            name="Project",
            description="Non-global",
            project="DEMO",
            is_global=False,
            shared=True,
            system_prompt="test",
            toolkits=[],
            creator="user",
        )
        global_assist = Assistant(
            id="g1",
            name="Global",
            description="Global",
            project="OTHER",
            is_global=True,
            system_prompt="test",
            toolkits=[],
            creator="system",
        )

        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.all.return_value = [non_global, global_assist]
        mock_session.exec.return_value.one.return_value = 2

        result = repository.query(
            user=mock_user,
            scope=AssistantScope.PROJECT_WITH_MARKETPLACE,
            filters={"project": "DEMO"},
            page=1,
            per_page=10,
        )

        assert len(result["data"]) == 2
        assert any(a.is_global for a in result["data"])
        assert any(not a.is_global for a in result["data"])

    @patch("codemie.service.assistant.assistant_repository.Session")
    def test_project_filter_ignored_for_global_assistants(self, mock_session_class, repository, mock_user):
        """Global assistants should be returned regardless of project filter"""
        global_other_project = Assistant(
            id="g1",
            name="Global Other",
            description="Global from other",
            project="OTHER_PROJECT",
            is_global=True,
            system_prompt="test",
            toolkits=[],
            creator="system",
        )

        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.all.return_value = [global_other_project]
        mock_session.exec.return_value.one.return_value = 1

        result = repository.query(
            user=mock_user,
            scope=AssistantScope.PROJECT_WITH_MARKETPLACE,
            filters={"project": "DEMO_PROJECT"},  # Different project
            page=1,
            per_page=10,
        )

        # Global assistant from OTHER_PROJECT should still be returned
        assert len(result["data"]) == 1
        assert result["data"][0].is_global is True

    def test_build_query_handles_search_filter_for_both_types(self, repository, mock_user):
        """Search filter should be applied to both global and non-global assistants"""
        filters = {"project": "DEMO", "search": "test"}

        query = repository._build_project_with_marketplace_query(mock_user, filters)
        whereclause_str = str(query.whereclause).lower()

        # Query should have OR condition combining both types
        assert " or " in whereclause_str
        # Should reference the name field (search filter)
        assert "name" in whereclause_str and "like" in whereclause_str
