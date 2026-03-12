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
Unit tests for Skills REST API router.

Tests API endpoints and request parsing logic.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.skill import MarketplaceFilter, SkillCategory, SkillScopeFilter, SkillVisibility
from codemie.rest_api.routers.skill import (
    _parse_categories,
    _parse_filters,
    _parse_scope_filter,
    _parse_visibility,
)
from codemie.rest_api.security.user import User


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_user():
    """Create a mock user with applications"""
    user = MagicMock(spec=User)
    user.id = "user-123"
    user.name = "Test User"
    user.username = "testuser"
    user.project_names = ["project-a", "project-b"]
    return user


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestParseFilters:
    """Test _parse_filters helper function"""

    def test_parse_valid_json(self):
        # Arrange
        filters_json = json.dumps({"scope": "project-a", "visibility": "private"})

        # Act
        result = _parse_filters(filters_json)

        # Assert
        assert result == {"scope": "project-a", "visibility": "private"}

    def test_parse_empty_string(self):
        # Act
        result = _parse_filters("")

        # Assert
        assert result == {}

    def test_parse_none(self):
        # Act
        result = _parse_filters(None)

        # Assert
        assert result == {}

    def test_parse_invalid_json_raises_exception(self):
        # Arrange
        invalid_json = "{invalid json"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            _parse_filters(invalid_json)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert "Invalid filters" in exc_info.value.message


class TestParseScopeFilter:
    """Test _parse_scope_filter helper function"""

    def test_marketplace_scope(self, mock_user):
        # Act
        project, marketplace_filter, visibility = _parse_scope_filter(SkillScopeFilter.MARKETPLACE.value, mock_user)

        # Assert
        assert project is None
        assert marketplace_filter == MarketplaceFilter.DEFAULT
        assert visibility == SkillVisibility.PUBLIC.value

    def test_project_scope(self, mock_user):
        # Act
        project, marketplace_filter, visibility = _parse_scope_filter(SkillScopeFilter.PROJECT.value, mock_user)

        # Assert
        assert project is None
        assert marketplace_filter == MarketplaceFilter.EXCLUDE
        assert visibility is None

    def test_project_with_marketplace_scope(self, mock_user):
        # Act
        project, marketplace_filter, visibility = _parse_scope_filter(
            SkillScopeFilter.PROJECT_WITH_MARKETPLACE.value, mock_user
        )

        # Assert
        assert project is None
        assert marketplace_filter == MarketplaceFilter.INCLUDE
        assert visibility is None

    def test_specific_project_name(self, mock_user):
        # Act
        project, marketplace_filter, visibility = _parse_scope_filter("project-a", mock_user)

        # Assert
        assert project == "project-a"
        assert marketplace_filter == MarketplaceFilter.DEFAULT
        assert visibility is None

    def test_project_user_not_in(self, mock_user):
        # Act - user doesn't have access to project-c
        project, marketplace_filter, visibility = _parse_scope_filter("project-c", mock_user)

        # Assert - should not filter by project
        assert project is None
        assert marketplace_filter == MarketplaceFilter.DEFAULT
        assert visibility is None

    def test_none_scope(self, mock_user):
        # Act
        project, marketplace_filter, visibility = _parse_scope_filter(None, mock_user)

        # Assert
        assert project is None
        assert marketplace_filter == MarketplaceFilter.DEFAULT
        assert visibility is None


class TestParseVisibility:
    """Test _parse_visibility helper function"""

    def test_parse_private(self):
        # Act
        result = _parse_visibility("private")

        # Assert
        assert result == SkillVisibility.PRIVATE

    def test_parse_project(self):
        # Act
        result = _parse_visibility("project")

        # Assert
        assert result == SkillVisibility.PROJECT

    def test_parse_public(self):
        # Act
        result = _parse_visibility("public")

        # Assert
        assert result == SkillVisibility.PUBLIC

    def test_parse_none(self):
        # Act
        result = _parse_visibility(None)

        # Assert
        assert result is None

    def test_parse_empty_string(self):
        # Act
        result = _parse_visibility("")

        # Assert
        assert result is None

    def test_parse_invalid_raises_exception(self):
        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            _parse_visibility("invalid-visibility")

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert "Invalid visibility value" in exc_info.value.message


class TestParseCategories:
    """Test _parse_categories helper function"""

    def test_parse_valid_categories(self):
        # Arrange
        categories = ["development", "testing"]

        # Act
        result = _parse_categories(categories)

        # Assert
        assert len(result) == 2
        assert SkillCategory.DEVELOPMENT in result
        assert SkillCategory.TESTING in result

    def test_parse_single_category(self):
        # Arrange
        categories = ["documentation"]

        # Act
        result = _parse_categories(categories)

        # Assert
        assert len(result) == 1
        assert result[0] == SkillCategory.DOCUMENTATION

    def test_parse_none(self):
        # Act
        result = _parse_categories(None)

        # Assert
        assert result is None

    def test_parse_empty_list(self):
        # Act
        result = _parse_categories([])

        # Assert
        assert result is None

    def test_parse_non_list_raises_exception(self):
        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            _parse_categories("not-a-list")

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert "Invalid categories format" in exc_info.value.message

    def test_parse_invalid_category_raises_exception(self):
        # Arrange
        categories = ["development", "invalid-category"]

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            _parse_categories(categories)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert "Invalid category value" in exc_info.value.message


# =============================================================================
# Endpoint Integration Tests (Key Paths)
# =============================================================================


class TestSkillEndpoints:
    """Test key skill endpoints"""

    @patch("codemie.rest_api.routers.skill.SkillService.list_skills")
    def test_list_skills_with_filters(self, mock_list_skills, mock_user):
        """Test list_skills endpoint with filter parsing"""
        from codemie.rest_api.routers.skill import list_skills

        # Arrange
        filters_json = json.dumps({"scope": "marketplace", "categories": ["development"], "search": "test"})

        mock_list_skills.return_value = MagicMock(skills=[], total=0, page=0, per_page=20, pages=0)

        # Act
        list_skills(user=mock_user, filters=filters_json, assistant_id=None, page=0, per_page=20)

        # Assert
        mock_list_skills.assert_called_once()
        call_kwargs = mock_list_skills.call_args[1]
        assert call_kwargs["user"] == mock_user
        assert call_kwargs["visibility"] == SkillVisibility.PUBLIC
        assert call_kwargs["categories"] == [SkillCategory.DEVELOPMENT]
        assert call_kwargs["search_query"] == "test"
        assert call_kwargs["page"] == 0
        assert call_kwargs["per_page"] == 20

    @patch("codemie.rest_api.routers.skill.SkillService.list_skills")
    def test_list_skills_with_created_by_filter(self, mock_list_skills, mock_user):
        """Test list_skills endpoint with created_by filter"""
        from codemie.rest_api.routers.skill import list_skills

        # Arrange
        filters_json = json.dumps({"created_by": "Test User", "scope": "marketplace", "visibility": "public"})

        mock_list_skills.return_value = MagicMock(skills=[], total=0, page=0, per_page=20, pages=0)

        # Act
        list_skills(user=mock_user, filters=filters_json, assistant_id=None, page=0, per_page=20)

        # Assert
        mock_list_skills.assert_called_once()
        call_kwargs = mock_list_skills.call_args[1]
        assert call_kwargs["user"] == mock_user
        assert call_kwargs["created_by"] == "Test User"
        assert call_kwargs["visibility"] == SkillVisibility.PUBLIC
        assert call_kwargs["page"] == 0
        assert call_kwargs["per_page"] == 20

    @patch("codemie.rest_api.routers.skill.SkillService.list_skills")
    def test_list_skills_with_project_with_marketplace_scope(self, mock_list_skills, mock_user):
        """Test list_skills endpoint with project_with_marketplace scope passes include_marketplace"""
        from codemie.rest_api.routers.skill import list_skills

        # Arrange
        filters_json = json.dumps({"scope": "project_with_marketplace", "project": ["codemie"]})

        mock_list_skills.return_value = MagicMock(skills=[], total=0, page=0, per_page=20, pages=0)

        # Act
        list_skills(user=mock_user, filters=filters_json, assistant_id=None, page=0, per_page=20)

        # Assert
        mock_list_skills.assert_called_once()
        call_kwargs = mock_list_skills.call_args[1]
        assert call_kwargs["marketplace_filter"] == MarketplaceFilter.INCLUDE
        assert call_kwargs["project"] == ["codemie"]

    def test_list_skills_project_with_marketplace_requires_project(self, mock_user):
        """Test that project_with_marketplace scope without project filter raises 400"""
        from codemie.rest_api.routers.skill import list_skills

        # Arrange
        filters_json = json.dumps({"scope": "project_with_marketplace"})

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_skills(user=mock_user, filters=filters_json, assistant_id=None, page=0, per_page=20)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert "project" in exc_info.value.details.lower()

    @patch("codemie.rest_api.routers.skill.SkillService.list_skills")
    def test_list_skills_with_multi_project_filter(self, mock_list_skills, mock_user):
        """Test list_skills endpoint with multiple projects in filter (array)"""
        from codemie.rest_api.routers.skill import list_skills

        # Arrange
        filters_json = json.dumps({"project": ["demo", "codemie"], "search": ""})

        mock_list_skills.return_value = MagicMock(skills=[], total=0, page=0, per_page=20, pages=0)

        # Act
        list_skills(user=mock_user, filters=filters_json, assistant_id=None, page=0, per_page=20)

        # Assert
        mock_list_skills.assert_called_once()
        call_kwargs = mock_list_skills.call_args[1]
        assert call_kwargs["project"] == ["demo", "codemie"]

    @patch("codemie.rest_api.routers.skill.SkillService.list_skills")
    def test_list_skills_with_single_project_filter_string(self, mock_list_skills, mock_user):
        """Test list_skills endpoint with single project string in filter (normalized to list)"""
        from codemie.rest_api.routers.skill import list_skills

        # Arrange
        filters_json = json.dumps({"project": "demo"})

        mock_list_skills.return_value = MagicMock(skills=[], total=0, page=0, per_page=20, pages=0)

        # Act
        list_skills(user=mock_user, filters=filters_json, assistant_id=None, page=0, per_page=20)

        # Assert
        mock_list_skills.assert_called_once()
        call_kwargs = mock_list_skills.call_args[1]
        assert call_kwargs["project"] == ["demo"]

    @patch("codemie.rest_api.routers.skill.SkillService.list_skills")
    def test_list_skills_scope_derived_project_normalized_to_list(self, mock_list_skills, mock_user):
        """Test that scope-derived project is also normalized to a list"""
        from codemie.rest_api.routers.skill import list_skills

        # Arrange - scope is a specific project name the user has access to
        filters_json = json.dumps({"scope": "project-a"})

        mock_list_skills.return_value = MagicMock(skills=[], total=0, page=0, per_page=20, pages=0)

        # Act
        list_skills(user=mock_user, filters=filters_json, assistant_id=None, page=0, per_page=20)

        # Assert
        mock_list_skills.assert_called_once()
        call_kwargs = mock_list_skills.call_args[1]
        assert call_kwargs["project"] == ["project-a"]

    @patch("codemie.rest_api.routers.skill.SkillService.list_skills")
    def test_list_skills_multi_project_filter_filters_empty_strings(self, mock_list_skills, mock_user):
        """Test that empty strings are filtered from project array"""
        from codemie.rest_api.routers.skill import list_skills

        # Arrange
        filters_json = json.dumps({"project": ["demo", "", "codemie"]})

        mock_list_skills.return_value = MagicMock(skills=[], total=0, page=0, per_page=20, pages=0)

        # Act
        list_skills(user=mock_user, filters=filters_json, assistant_id=None, page=0, per_page=20)

        # Assert
        mock_list_skills.assert_called_once()
        call_kwargs = mock_list_skills.call_args[1]
        assert call_kwargs["project"] == ["demo", "codemie"]

    @patch("codemie.rest_api.routers.skill.SkillService.list_skills")
    def test_list_skills_no_project_filter_passes_none(self, mock_list_skills, mock_user):
        """Test that when no project filter is provided, None is passed"""
        from codemie.rest_api.routers.skill import list_skills

        # Arrange
        filters_json = json.dumps({"search": "test"})

        mock_list_skills.return_value = MagicMock(skills=[], total=0, page=0, per_page=20, pages=0)

        # Act
        list_skills(user=mock_user, filters=filters_json, assistant_id=None, page=0, per_page=20)

        # Assert
        mock_list_skills.assert_called_once()
        call_kwargs = mock_list_skills.call_args[1]
        assert call_kwargs["project"] is None

    @patch("codemie.rest_api.routers.skill.SkillService.get_skill_by_id")
    def test_get_skill_by_id_success(self, mock_get_skill, mock_user):
        """Test get_skill_by_id endpoint"""
        from codemie.rest_api.routers.skill import get_skill_by_id

        # Arrange
        skill_id = "skill-123"
        mock_skill = MagicMock()
        mock_skill.id = skill_id
        mock_skill.name = "test-skill"
        mock_get_skill.return_value = mock_skill

        # Act
        result = get_skill_by_id(skill_id=skill_id, user=mock_user)

        # Assert
        mock_get_skill.assert_called_once_with(skill_id, mock_user)
        assert result.id == skill_id

    @patch("codemie.rest_api.routers.skill.SkillService.create_skill")
    def test_create_skill_success(self, mock_create_skill, mock_user):
        """Test create_skill endpoint"""
        from codemie.rest_api.routers.skill import create_skill
        from codemie.rest_api.models.skill import SkillCreateRequest

        # Arrange
        request = SkillCreateRequest(
            name="new-skill",
            description="New skill description that meets minimum length requirement",
            content="Content " * 20,
            project="project-a",
            visibility=SkillVisibility.PRIVATE,
            categories=[SkillCategory.DEVELOPMENT],
        )
        mock_skill = MagicMock()
        mock_skill.id = "new-skill-id"
        mock_create_skill.return_value = mock_skill

        # Act
        result = create_skill(request=request, user=mock_user)

        # Assert
        mock_create_skill.assert_called_once_with(request, mock_user)
        assert result.id == "new-skill-id"

    @patch("codemie.rest_api.routers.skill.SkillService.delete_skill")
    def test_delete_skill_success(self, mock_delete_skill, mock_user):
        """Test delete_skill endpoint"""
        from codemie.rest_api.routers.skill import delete_skill

        # Arrange
        skill_id = "skill-123"

        # Act
        result = delete_skill(skill_id=skill_id, user=mock_user)

        # Assert
        mock_delete_skill.assert_called_once_with(skill_id, mock_user)
        assert result is None

    @patch("codemie.rest_api.routers.skill.SkillService.publish_to_marketplace")
    def test_publish_to_marketplace_with_categories(self, mock_publish, mock_user):
        """Test publish_skill_to_marketplace endpoint with categories"""
        from codemie.rest_api.routers.skill import publish_skill_to_marketplace
        from codemie.rest_api.models.skill import PublishToMarketplaceRequest

        # Arrange
        skill_id = "skill-123"
        request = PublishToMarketplaceRequest(categories=["development", "testing"])

        # Act
        result = publish_skill_to_marketplace(skill_id=skill_id, request=request, user=mock_user)

        # Assert
        mock_publish.assert_called_once_with(skill_id, mock_user, ["development", "testing"])
        assert "published" in result.message.lower()

    @patch("codemie.rest_api.routers.skill.SkillService.publish_to_marketplace")
    def test_publish_to_marketplace_without_categories(self, mock_publish, mock_user):
        """Test publish_skill_to_marketplace endpoint without categories"""
        from codemie.rest_api.routers.skill import publish_skill_to_marketplace

        # Arrange
        skill_id = "skill-123"

        # Act
        result = publish_skill_to_marketplace(skill_id=skill_id, request=None, user=mock_user)

        # Assert
        mock_publish.assert_called_once_with(skill_id, mock_user, None)
        assert "published" in result.message.lower()

    @patch("codemie.rest_api.routers.skill.SkillService.unpublish_from_marketplace")
    def test_unpublish_from_marketplace(self, mock_unpublish, mock_user):
        """Test unpublish_skill_from_marketplace endpoint"""
        from codemie.rest_api.routers.skill import unpublish_skill_from_marketplace

        # Arrange
        skill_id = "skill-123"

        # Act
        result = unpublish_skill_from_marketplace(skill_id=skill_id, user=mock_user)

        # Assert
        mock_unpublish.assert_called_once_with(skill_id, mock_user)
        assert "unpublished" in result.message.lower()

    @patch("codemie.rest_api.routers.skill.SkillService.attach_skill_to_assistant")
    def test_attach_skill_to_assistant(self, mock_attach, mock_user):
        """Test attach_skill_to_assistant endpoint"""
        from codemie.rest_api.routers.skill import attach_skill_to_assistant
        from codemie.rest_api.models.skill import SkillAttachRequest

        # Arrange
        assistant_id = "asst-123"
        request = SkillAttachRequest(skill_id="skill-123")

        # Act
        result = attach_skill_to_assistant(assistant_id=assistant_id, request=request, user=mock_user)

        # Assert
        mock_attach.assert_called_once_with(assistant_id, "skill-123", mock_user)
        assert "attached" in result.message.lower()

    @patch("codemie.rest_api.routers.skill.SkillService.detach_skill_from_assistant")
    def test_detach_skill_from_assistant(self, mock_detach, mock_user):
        """Test detach_skill_from_assistant endpoint"""
        from codemie.rest_api.routers.skill import detach_skill_from_assistant

        # Arrange
        assistant_id = "asst-123"
        skill_id = "skill-123"

        # Act
        result = detach_skill_from_assistant(assistant_id=assistant_id, skill_id=skill_id, user=mock_user)

        # Assert
        mock_detach.assert_called_once_with(assistant_id, skill_id, mock_user)
        assert "detached" in result.message.lower()
