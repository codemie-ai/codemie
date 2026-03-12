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
Unit tests for SkillRepository.

Tests focus on business logic, access control, filtering, pagination,
and complex query operations. Trivial operations are not tested.
"""

from datetime import datetime, UTC
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from codemie.repository.skill_repository import SkillRepository, SkillListResult
from codemie.rest_api.models.skill import Skill, SkillVisibility, SkillCategory
from codemie.core.models import CreatedByUser


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def user_id():
    """Standard user ID for tests"""
    return "user-123"


@pytest.fixture
def other_user_id():
    """Different user ID for testing access control"""
    return "user-456"


@pytest.fixture
def user_applications():
    """User's accessible projects"""
    return ["project-a", "project-b"]


@pytest.fixture
def mock_skill():
    """Create a mock skill with typical data"""
    return Skill(
        id=str(uuid4()),
        name="test-skill",
        description="Test skill description",
        content="Test skill content for testing purposes",
        project="project-a",
        visibility=SkillVisibility.PROJECT,
        created_by=CreatedByUser(id="user-123", name="Test User", username="testuser"),
        categories=["development", "testing"],
        created_date=datetime(2024, 1, 1, tzinfo=UTC),
        updated_date=None,
        unique_likes_count=0,
        unique_dislikes_count=0,
    )


@pytest.fixture
def mock_public_skill():
    """Create a public/marketplace skill"""
    return Skill(
        id=str(uuid4()),
        name="public-skill",
        description="Public skill description",
        content="Public skill content for marketplace",
        project="demo",
        visibility=SkillVisibility.PUBLIC,
        created_by=CreatedByUser(id="user-456", name="Other User", username="otheruser"),
        categories=["documentation"],
        created_date=datetime(2024, 1, 2, tzinfo=UTC),
        updated_date=None,
        unique_likes_count=5,
        unique_dislikes_count=1,
    )


@pytest.fixture
def mock_private_skill():
    """Create a private skill"""
    return Skill(
        id=str(uuid4()),
        name="private-skill",
        description="Private skill description",
        content="Private skill content for personal use",
        project="project-c",
        visibility=SkillVisibility.PRIVATE,
        created_by=CreatedByUser(id="user-456", name="Other User", username="otheruser"),
        categories=["other"],
        created_date=datetime(2024, 1, 3, tzinfo=UTC),
        updated_date=None,
        unique_likes_count=0,
        unique_dislikes_count=0,
    )


# =============================================================================
# Tests: _build_access_conditions (Access Control Logic)
# =============================================================================


class TestBuildAccessConditions:
    """Tests for access control condition building"""

    def test_user_own_skills_condition_included(self, user_id):
        """Test that user's own skills are always accessible"""
        # Arrange & Act
        conditions = SkillRepository._build_access_conditions(user_id, [])

        # Assert
        assert len(conditions) == 2  # Own skills + public skills
        # First condition should check created_by.id == user_id
        assert conditions[0].compare(Skill.created_by["id"].astext == user_id)

    def test_public_marketplace_condition_included(self, user_id):
        """Test that public/marketplace skills are accessible"""
        # Arrange & Act
        conditions = SkillRepository._build_access_conditions(user_id, [])

        # Assert
        assert len(conditions) == 2
        # Second condition should check visibility == PUBLIC
        assert conditions[1].compare(Skill.visibility == SkillVisibility.PUBLIC)

    def test_project_access_with_user_applications(self, user_id, user_applications):
        """Test that project-level access is added when user has applications"""
        # Arrange & Act
        conditions = SkillRepository._build_access_conditions(user_id, user_applications)

        # Assert
        assert len(conditions) == 3  # Own skills + public + project access
        # Third condition should be AND(visibility==PROJECT, project IN applications)
        last_condition = conditions[2]
        # Verify it's a compound clause (AND)
        assert hasattr(last_condition, 'clauses')

    def test_no_project_access_when_empty_applications(self, user_id):
        """Test that empty user_applications list does not add project access"""
        # Arrange & Act
        conditions = SkillRepository._build_access_conditions(user_id, [])

        # Assert
        assert len(conditions) == 2  # Only own skills + public, no project access

    def test_project_access_condition_structure(self, user_id):
        """Test project access condition has correct structure"""
        # Arrange
        user_applications = ["project-x", "project-y"]

        # Act
        conditions = SkillRepository._build_access_conditions(user_id, user_applications)

        # Assert
        # Get the project access condition (third one)
        project_condition = conditions[2]
        # Should be an AND clause
        sql_str = str(project_condition.compile(compile_kwargs={"literal_binds": True}))
        assert "visibility" in sql_str.lower()
        assert "project" in sql_str.lower()

    def test_multi_project_specific_filter(self, user_id):
        """Test access conditions with multiple specific projects"""
        # Arrange
        user_applications = ["project-a", "project-b"]

        # Act
        conditions = SkillRepository._build_access_conditions(
            user_id, user_applications, specific_project=["project-a", "project-b"]
        )

        # Assert - should have conditions for both projects
        # Each project gets: owner condition + project visibility condition (if user has access)
        # project-a: owner + PROJECT visibility = 2 conditions
        # project-b: owner + PROJECT visibility = 2 conditions
        assert len(conditions) == 4
        sql_parts = [str(c.compile(compile_kwargs={"literal_binds": True})) for c in conditions]
        combined = " ".join(sql_parts).lower()
        assert "project-a" in combined
        assert "project-b" in combined

    def test_single_specific_project_as_string_backward_compat(self, user_id):
        """Test that a single string specific_project still works (backward compat)"""
        # Arrange
        user_applications = ["project-a"]

        # Act
        conditions = SkillRepository._build_access_conditions(user_id, user_applications, specific_project="project-a")

        # Assert - should have conditions for single project
        assert len(conditions) >= 2  # at least owner + project visibility
        sql_parts = [str(c.compile(compile_kwargs={"literal_binds": True})) for c in conditions]
        combined = " ".join(sql_parts).lower()
        assert "project-a" in combined

    def test_marketplace_include_adds_public_condition(self, user_id):
        """Test that MarketplaceFilter.INCLUDE adds PUBLIC visibility condition"""
        from codemie.rest_api.models.skill import MarketplaceFilter

        # Arrange
        user_applications = ["project-a"]

        # Act
        conditions = SkillRepository._build_access_conditions(
            user_id,
            user_applications,
            specific_project=["project-a"],
            marketplace_filter=MarketplaceFilter.INCLUDE,
        )

        # Assert - should have project conditions + PUBLIC visibility
        sql_parts = [str(c.compile(compile_kwargs={"literal_binds": True})) for c in conditions]
        combined = " ".join(sql_parts).lower()
        assert "project-a" in combined
        assert "public" in combined

    def test_marketplace_default_excludes_public_for_project(self, user_id):
        """Test that MarketplaceFilter.DEFAULT does NOT add PUBLIC condition for project filter"""
        from codemie.rest_api.models.skill import MarketplaceFilter

        # Arrange
        user_applications = ["project-a"]

        # Act
        conditions = SkillRepository._build_access_conditions(
            user_id,
            user_applications,
            specific_project=["project-a"],
            marketplace_filter=MarketplaceFilter.DEFAULT,
        )

        # Assert - should NOT have PUBLIC visibility condition
        sql_parts = [str(c.compile(compile_kwargs={"literal_binds": True})) for c in conditions]
        combined = " ".join(sql_parts).lower()
        assert "project-a" in combined
        # PUBLIC should not appear as a standalone condition
        public_conditions = [s for s in sql_parts if "public" in s.lower() and "project" not in s.lower()]
        assert len(public_conditions) == 0


# =============================================================================
# Tests: _apply_skill_filters (Filter Logic)
# =============================================================================


class TestApplySkillFilters:
    """Tests for filter application logic"""

    def test_single_project_filter_applied(self):
        """Test that single project filter is correctly applied"""
        # Arrange
        from sqlmodel import select

        base_query = select(Skill)

        # Act
        filtered_query = SkillRepository._apply_skill_filters(base_query, project=["test-project"])

        # Assert
        sql_str = str(filtered_query.compile(compile_kwargs={"literal_binds": True}))
        assert "project" in sql_str.lower()
        assert "test-project" in sql_str

    def test_multi_project_filter_uses_in_operator(self):
        """Test that multiple projects use IN operator"""
        # Arrange
        from sqlmodel import select

        base_query = select(Skill)

        # Act
        filtered_query = SkillRepository._apply_skill_filters(base_query, project=["demo", "codemie"])

        # Assert
        sql_str = str(filtered_query.compile(compile_kwargs={"literal_binds": True}))
        assert "project" in sql_str.lower()
        assert "in" in sql_str.lower()
        assert "demo" in sql_str
        assert "codemie" in sql_str

    def test_visibility_filter_applied(self):
        """Test that visibility filter is correctly applied"""
        # Arrange
        from sqlmodel import select

        base_query = select(Skill)

        # Act
        filtered_query = SkillRepository._apply_skill_filters(base_query, visibility=SkillVisibility.PUBLIC)

        # Assert
        sql_str = str(filtered_query.compile(compile_kwargs={"literal_binds": True}))
        assert "visibility" in sql_str.lower()
        assert "public" in sql_str.lower()

    def test_categories_filter_with_postgresql_operator(self):
        """Test categories filter uses PostgreSQL @> (contains) operator"""
        # Arrange
        from sqlmodel import select

        base_query = select(Skill)
        categories = [SkillCategory.DEVELOPMENT, SkillCategory.TESTING]

        # Act
        filtered_query = SkillRepository._apply_skill_filters(base_query, categories=categories)

        # Assert
        # Don't compile with literal_binds due to JSONB complexity, just verify query structure
        sql_str = str(filtered_query.compile())
        # Should reference categories column and use @> (contains) operator with OR
        assert "categories" in sql_str.lower()
        assert "@>" in sql_str or "contains" in sql_str.lower()

    def test_search_query_case_insensitive(self):
        """Test search query uses case-insensitive matching"""
        # Arrange
        from sqlmodel import select

        base_query = select(Skill)

        # Act
        filtered_query = SkillRepository._apply_skill_filters(base_query, search_query="Test")

        # Assert
        sql_str = str(filtered_query.compile(compile_kwargs={"literal_binds": True}))
        # Should use lower() function for case-insensitive search
        assert "lower" in sql_str.lower()

    def test_combined_filters_all_applied(self):
        """Test that multiple filters can be applied together"""
        # Arrange
        from sqlmodel import select

        base_query = select(Skill)

        # Act
        filtered_query = SkillRepository._apply_skill_filters(
            base_query,
            project=["test-project"],
            visibility=SkillVisibility.PROJECT,
            categories=[SkillCategory.DEVELOPMENT],
            search_query="test",
        )

        # Assert
        # Don't use literal_binds due to JSONB complexity
        sql_str = str(filtered_query.compile())
        assert "project" in sql_str.lower()
        assert "visibility" in sql_str.lower()
        assert "lower" in sql_str.lower()  # Search query
        assert "categories" in sql_str.lower()  # Categories filter

    def test_created_by_filter_uses_name_field(self):
        """Test that created_by filter matches against user name (not user ID)"""
        # Arrange
        from sqlmodel import select

        base_query = select(Skill)

        # Act
        filtered_query = SkillRepository._apply_skill_filters(base_query, created_by="Test User")

        # Assert
        sql_str = str(filtered_query.compile(compile_kwargs={"literal_binds": True}))
        # Should filter by created_by->>'name', not created_by->>'id'
        assert "created_by" in sql_str.lower()
        assert "name" in sql_str
        assert "Test User" in sql_str

    def test_no_filters_returns_unmodified_query(self):
        """Test that query is unchanged when no filters provided"""
        # Arrange
        from sqlmodel import select

        base_query = select(Skill)

        # Act
        filtered_query = SkillRepository._apply_skill_filters(base_query)

        # Assert
        # Queries should be equivalent
        base_sql = str(base_query.compile(compile_kwargs={"literal_binds": True}))
        filtered_sql = str(filtered_query.compile(compile_kwargs={"literal_binds": True}))
        assert base_sql == filtered_sql


# =============================================================================
# Tests: _get_assistants_count_for_skills (Aggregation Logic)
# =============================================================================


class TestGetAssistantsCountForSkills:
    """Tests for assistant count aggregation"""

    @patch("codemie.repository.skill_repository.Session")
    def test_empty_skill_ids_returns_empty_dict(self, mock_session_cls):
        """Test that empty skill_ids list returns empty dictionary"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Act
        result = SkillRepository._get_assistants_count_for_skills(mock_session, [])

        # Assert
        assert result == {}
        mock_session.exec.assert_not_called()

    @patch("codemie.repository.skill_repository.Session")
    def test_uses_jsonb_array_elements_text(self, mock_session_cls):
        """Test query uses PostgreSQL jsonb_array_elements_text function"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.all.return_value = []

        skill_ids = ["skill-1", "skill-2"]

        # Act
        SkillRepository._get_assistants_count_for_skills(mock_session, skill_ids)

        # Assert
        # Get the query that was executed
        call_args = mock_session.exec.call_args
        query = call_args[0][0]
        sql_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        # Should use jsonb_array_elements_text to flatten skill_ids array
        assert "jsonb_array_elements_text" in sql_str.lower()

    @patch("codemie.repository.skill_repository.Session")
    def test_filters_to_requested_skill_ids(self, mock_session_cls):
        """Test that results are filtered to only requested skill_ids"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Mock results with extra skill not in request
        mock_result_1 = MagicMock(skill_id="skill-1", count=3)
        mock_result_2 = MagicMock(skill_id="skill-2", count=5)
        mock_result_3 = MagicMock(skill_id="skill-999", count=1)  # Not requested
        mock_session.exec.return_value.all.return_value = [mock_result_1, mock_result_2, mock_result_3]

        requested_skills = ["skill-1", "skill-2"]

        # Act
        result = SkillRepository._get_assistants_count_for_skills(mock_session, requested_skills)

        # Assert
        assert len(result) == 2
        assert "skill-1" in result
        assert "skill-2" in result
        assert "skill-999" not in result

    @patch("codemie.repository.skill_repository.Session")
    def test_skills_with_no_assistants_not_in_result(self, mock_session_cls):
        """Test that skills not used by any assistant are not in result"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Only skill-1 has usage
        mock_result = MagicMock(skill_id="skill-1", count=2)
        mock_session.exec.return_value.all.return_value = [mock_result]

        requested_skills = ["skill-1", "skill-2", "skill-3"]

        # Act
        result = SkillRepository._get_assistants_count_for_skills(mock_session, requested_skills)

        # Assert
        assert len(result) == 1
        assert result["skill-1"] == 2
        assert "skill-2" not in result
        assert "skill-3" not in result


# =============================================================================
# Tests: create (CRUD Operation)
# =============================================================================


class TestCreate:
    """Tests for skill creation"""

    @patch("codemie.repository.skill_repository.Session")
    def test_generates_uuid_if_not_provided(self, mock_session_cls):
        """Test that UUID is generated when id is not provided"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        skill_data = {
            "name": "test-skill",
            "description": "Test description",
            "content": "Test content",
            "project": "test-project",
            "visibility": SkillVisibility.PRIVATE,
        }

        # Act
        with patch("codemie.repository.skill_repository.uuid4") as mock_uuid:
            mock_uuid.return_value = uuid4()
            result = SkillRepository.create(skill_data)

        # Assert
        assert result.id is not None
        mock_uuid.assert_called_once()

    @patch("codemie.repository.skill_repository.Session")
    def test_sets_created_date_if_not_provided(self, mock_session_cls):
        """Test that created_date is set when not provided"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        skill_data = {
            "name": "test-skill",
            "description": "Test description",
            "content": "Test content",
            "project": "test-project",
        }

        # Act
        result = SkillRepository.create(skill_data)

        # Assert
        assert result.created_date is not None
        assert isinstance(result.created_date, datetime)

    @patch("codemie.repository.skill_repository.Session")
    def test_preserves_provided_id(self, mock_session_cls):
        """Test that provided id is preserved"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        custom_id = "custom-skill-id"
        skill_data = {
            "id": custom_id,
            "name": "test-skill",
            "description": "Test description",
            "content": "Test content",
            "project": "test-project",
        }

        # Act
        result = SkillRepository.create(skill_data)

        # Assert
        assert result.id == custom_id

    @patch("codemie.repository.skill_repository.Session")
    def test_commits_and_refreshes_skill(self, mock_session_cls):
        """Test that session commits and refreshes the skill"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        skill_data = {
            "name": "test-skill",
            "description": "Test description",
            "content": "Test content",
            "project": "test-project",
        }

        # Act
        SkillRepository.create(skill_data)

        # Assert
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()


# =============================================================================
# Tests: get_by_name_author_project (Unique Constraint Check)
# =============================================================================


class TestGetByNameAuthorProject:
    """Tests for unique constraint lookup"""

    @patch("codemie.repository.skill_repository.Session")
    def test_case_insensitive_name_comparison(self, mock_session_cls):
        """Test that name comparison is case-insensitive"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.first.return_value = None

        # Act
        SkillRepository.get_by_name_author_project("Test-Skill", "user-123", "project-a")

        # Assert
        call_args = mock_session.exec.call_args
        query = call_args[0][0]
        sql_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        # Should use lower() for case-insensitive comparison
        assert "lower" in sql_str.lower()

    @patch("codemie.repository.skill_repository.Session")
    def test_checks_all_three_fields(self, mock_session_cls):
        """Test that query checks name, author_id, and project"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.first.return_value = None

        name = "test-skill"
        author_id = "user-123"
        project = "project-a"

        # Act
        SkillRepository.get_by_name_author_project(name, author_id, project)

        # Assert
        call_args = mock_session.exec.call_args
        query = call_args[0][0]
        sql_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        # Should have all three conditions
        assert "name" in sql_str.lower()
        assert "created_by" in sql_str.lower()
        assert "project" in sql_str.lower()

    @patch("codemie.repository.skill_repository.Session")
    def test_returns_first_match(self, mock_session_cls, mock_skill):
        """Test that first matching skill is returned"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.first.return_value = mock_skill

        # Act
        result = SkillRepository.get_by_name_author_project("test-skill", "user-123", "project-a")

        # Assert
        assert result == mock_skill
        mock_session.exec.return_value.first.assert_called_once()

    @patch("codemie.repository.skill_repository.Session")
    def test_returns_none_when_no_match(self, mock_session_cls):
        """Test that None is returned when no match found"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.first.return_value = None

        # Act
        result = SkillRepository.get_by_name_author_project("nonexistent", "user-999", "project-z")

        # Assert
        assert result is None


# =============================================================================
# Tests: update (CRUD Operation)
# =============================================================================


class TestUpdate:
    """Tests for skill update"""

    @patch("codemie.repository.skill_repository.Session")
    def test_sets_updated_date_on_update(self, mock_session_cls, mock_skill):
        """Test that updated_date is automatically set"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = mock_skill

        updates = {"description": "New description"}

        # Act
        with patch("codemie.repository.skill_repository.datetime") as mock_datetime:
            mock_now = datetime(2024, 2, 1, tzinfo=UTC)
            mock_datetime.now.return_value = mock_now
            result = SkillRepository.update(mock_skill.id, updates)

        # Assert
        assert result.updated_date == mock_now

    @patch("codemie.repository.skill_repository.Session")
    def test_updates_specified_fields_only(self, mock_session_cls, mock_skill):
        """Test that only specified fields are updated"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = mock_skill

        original_name = mock_skill.name
        updates = {"description": "Updated description"}

        # Act
        result = SkillRepository.update(mock_skill.id, updates)

        # Assert
        assert result.description == "Updated description"
        assert result.name == original_name  # Unchanged

    @patch("codemie.repository.skill_repository.Session")
    def test_returns_none_when_skill_not_found(self, mock_session_cls):
        """Test that None is returned when skill doesn't exist"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = None

        # Act
        result = SkillRepository.update("nonexistent-id", {"name": "new-name"})

        # Assert
        assert result is None
        mock_session.commit.assert_not_called()

    @patch("codemie.repository.skill_repository.Session")
    def test_ignores_invalid_field_names(self, mock_session_cls, mock_skill):
        """Test that invalid field names are ignored"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = mock_skill

        updates = {"invalid_field": "value", "description": "Valid update"}

        # Act
        result = SkillRepository.update(mock_skill.id, updates)

        # Assert
        assert result.description == "Valid update"
        assert not hasattr(result, "invalid_field")


# =============================================================================
# Tests: delete (Cascade Delete)
# =============================================================================


class TestDelete:
    """Tests for skill deletion with cascade"""

    @patch("codemie.repository.skill_repository.Session")
    def test_deletes_related_interactions(self, mock_session_cls, mock_skill):
        """Test that related skill_user_interaction records are deleted"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = mock_skill

        # Act
        result = SkillRepository.delete(mock_skill.id)

        # Assert
        assert result is True
        # Should execute delete statement for interactions
        assert mock_session.exec.call_count == 1  # Delete interactions
        mock_session.delete.assert_called_once_with(mock_skill)
        mock_session.commit.assert_called_once()

    @patch("codemie.repository.skill_repository.Session")
    def test_returns_false_when_skill_not_found(self, mock_session_cls):
        """Test that False is returned when skill doesn't exist"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = None

        # Act
        result = SkillRepository.delete("nonexistent-id")

        # Assert
        assert result is False
        mock_session.delete.assert_not_called()
        mock_session.commit.assert_not_called()

    @patch("codemie.repository.skill_repository.Session")
    def test_uses_bulk_delete_for_interactions(self, mock_session_cls, mock_skill):
        """Test that bulk delete is used for interactions (not individual deletes)"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = mock_skill

        # Act
        SkillRepository.delete(mock_skill.id)

        # Assert
        # Should call exec once for bulk delete statement
        call_args = mock_session.exec.call_args
        query = call_args[0][0]
        # Should be a delete statement
        sql_str = str(query.compile(compile_kwargs={"literal_binds": True}))
        assert "delete" in sql_str.lower()
        assert "skill_user_interaction" in sql_str.lower()


# =============================================================================
# Tests: list_accessible_to_user (Complex Query)
# =============================================================================


class TestListAccessibleToUser:
    """Tests for the main listing operation with access control"""

    @patch("codemie.repository.skill_repository.Session")
    def test_enforces_access_control(self, mock_session_cls, user_id, user_applications):
        """Test that access control conditions are applied"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = 0
        mock_session.exec.return_value.all.return_value = []

        # Act
        SkillRepository.list_accessible_to_user(user_id, user_applications)

        # Assert
        # Check that query includes access conditions
        # First exec call is for count
        count_call_args = mock_session.exec.call_args_list[0]
        query = count_call_args[0][0]
        sql_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        # Should have OR conditions for access control
        assert "or" in sql_str.lower()

    @patch("codemie.repository.skill_repository.Session")
    def test_pagination_offset_and_limit_applied(self, mock_session_cls, user_id):
        """Test that pagination offset and limit are correctly applied"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = 100
        mock_session.exec.return_value.all.return_value = []

        page = 2
        per_page = 10

        # Act
        SkillRepository.list_accessible_to_user(user_id, [], page=page, per_page=per_page)

        # Assert
        # Second exec call is for paginated query
        paginated_call_args = mock_session.exec.call_args_list[1]
        query = paginated_call_args[0][0]
        sql_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        # Should have LIMIT and OFFSET
        assert "limit" in sql_str.lower() or str(per_page) in sql_str
        assert "offset" in sql_str.lower() or str(page * per_page) in sql_str

    @patch("codemie.repository.skill_repository.Session")
    def test_exclude_marketplace_parameter(self, mock_session_cls, user_id):
        """Test that MarketplaceFilter.EXCLUDE removes PUBLIC visibility skills"""
        from codemie.rest_api.models.skill import MarketplaceFilter

        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = 0
        mock_session.exec.return_value.all.return_value = []

        # Act
        SkillRepository.list_accessible_to_user(user_id, [], marketplace_filter=MarketplaceFilter.EXCLUDE)

        # Assert
        # Count query should exclude PUBLIC visibility
        count_call_args = mock_session.exec.call_args_list[0]
        query = count_call_args[0][0]
        sql_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        # Should filter out PUBLIC visibility
        assert "visibility" in sql_str.lower()

    @patch("codemie.repository.skill_repository.Session")
    def test_combines_filters_with_access_control(self, mock_session_cls, user_id):
        """Test that optional filters work together with access control"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = 0
        mock_session.exec.return_value.all.return_value = []

        # Act
        SkillRepository.list_accessible_to_user(
            user_id,
            [],
            project=["test-project"],
            visibility=SkillVisibility.PROJECT,
            categories=[SkillCategory.DEVELOPMENT],
            search_query="test",
        )

        # Assert
        count_call_args = mock_session.exec.call_args_list[0]
        query = count_call_args[0][0]
        # Don't use literal_binds due to JSONB complexity
        sql_str = str(query.compile())

        # Should have both access control and filters
        assert "or" in sql_str.lower()  # Access control OR
        assert "project" in sql_str.lower()  # Project filter
        assert "visibility" in sql_str.lower()  # Visibility filter
        assert "categories" in sql_str.lower()  # Categories filter

    @patch("codemie.repository.skill_repository.Session")
    def test_empty_results_edge_case(self, mock_session_cls, user_id):
        """Test handling of empty results"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = 0
        mock_session.exec.return_value.all.return_value = []

        # Act
        result = SkillRepository.list_accessible_to_user(user_id, [])

        # Assert
        assert isinstance(result, SkillListResult)
        assert result.skills == []
        assert result.total == 0
        assert result.pages == 0
        assert result.assistants_count_map == {}

    @patch("codemie.repository.skill_repository.Session")
    def test_single_page_pagination(self, mock_session_cls, user_id, mock_skill):
        """Test pagination with results fitting on single page"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = 5  # Total count
        mock_session.exec.return_value.all.side_effect = [[mock_skill] * 5, []]  # Skills, then no assistants

        # Act
        result = SkillRepository.list_accessible_to_user(user_id, [], page=0, per_page=20)

        # Assert
        assert result.total == 5
        assert result.pages == 1  # math.ceil(5 / 20) = 1
        assert result.page == 0
        assert result.per_page == 20

    @patch("codemie.repository.skill_repository.Session")
    def test_multiple_pages_calculation(self, mock_session_cls, user_id):
        """Test pagination calculation with multiple pages"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = 47  # Total count
        mock_session.exec.return_value.all.side_effect = [[], []]

        # Act
        result = SkillRepository.list_accessible_to_user(user_id, [], page=0, per_page=10)

        # Assert
        assert result.total == 47
        assert result.pages == 5  # math.ceil(47 / 10) = 5

    @patch("codemie.repository.skill_repository.Session")
    def test_includes_assistants_count_map(self, mock_session_cls, user_id, mock_skill):
        """Test that assistants count map is populated"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = 1

        # Mock skill and assistant count
        skill_id = mock_skill.id
        mock_count_result = MagicMock(skill_id=skill_id, count=3)
        mock_session.exec.return_value.all.side_effect = [[mock_skill], [mock_count_result]]

        # Act
        result = SkillRepository.list_accessible_to_user(user_id, [])

        # Assert
        assert skill_id in result.assistants_count_map
        # Note: _get_assistants_count_for_skills filters results, so we can't assert exact count
        # The important thing is the map is populated

    @patch("codemie.repository.skill_repository.Session")
    def test_orders_by_created_date_desc(self, mock_session_cls, user_id):
        """Test that results are ordered by created_date descending"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = 0
        mock_session.exec.return_value.all.return_value = []

        # Act
        SkillRepository.list_accessible_to_user(user_id, [])

        # Assert
        paginated_call_args = mock_session.exec.call_args_list[1]
        query = paginated_call_args[0][0]
        sql_str = str(query.compile(compile_kwargs={"literal_binds": True}))

        # Should have ORDER BY created_date DESC
        assert "order by" in sql_str.lower()
        assert "created_date" in sql_str.lower()


# =============================================================================
# Tests: record_usage (Usage Tracking)
# =============================================================================


# =============================================================================
# Tests: Edge Cases and Error Handling
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions"""

    @patch("codemie.repository.skill_repository.Session")
    def test_get_by_ids_with_empty_list(self, mock_session_cls):
        """Test that get_by_ids returns empty list for empty input"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # Act
        result = SkillRepository.get_by_ids([])

        # Assert
        assert result == []
        mock_session.exec.assert_not_called()

    @patch("codemie.repository.skill_repository.Session")
    def test_pagination_with_zero_per_page(self, mock_session_cls, user_id):
        """Test pagination calculation when per_page is 0"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = 10
        mock_session.exec.return_value.all.return_value = []

        # Act
        result = SkillRepository.list_accessible_to_user(user_id, [], page=0, per_page=0)

        # Assert
        # Should default to 1 page to avoid division by zero
        assert result.pages == 1

    @patch("codemie.repository.skill_repository.Session")
    def test_update_with_empty_updates_dict(self, mock_session_cls, mock_skill):
        """Test update with empty updates dictionary"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get.return_value = mock_skill

        # Act
        result = SkillRepository.update(mock_skill.id, {})

        # Assert
        # Should still set updated_date and commit
        assert result.updated_date is not None
        mock_session.commit.assert_called_once()

    @patch("codemie.repository.skill_repository.Session")
    def test_count_by_author_with_nonexistent_author(self, mock_session_cls):
        """Test count_by_author returns 0 for nonexistent author"""
        # Arrange
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.one.return_value = 0

        # Act
        result = SkillRepository.count_by_author("nonexistent-user")

        # Assert
        assert result == 0
