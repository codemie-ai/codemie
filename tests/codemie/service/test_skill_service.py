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
Unit tests for SkillService.

Tests business logic for skill management including:
- Access control validation
- CRUD operations
- Frontmatter parsing
- Import/export operations
- Assistant-skill associations
- Marketplace operations
"""

import base64
from datetime import datetime, UTC
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import status

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.repository.skill_repository import SkillRepository, SkillListResult
from codemie.rest_api.models.skill import (
    MarketplaceFilter,
    Skill,
    SkillCategory,
    SkillCreateRequest,
    SkillSortBy,
    SkillUpdateRequest,
    SkillVisibility,
)
from codemie.rest_api.security.user import User
from codemie.service.skill_service import SkillService, SkillErrors


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_repository():
    """Mock SkillRepository for all tests"""
    return MagicMock(spec=SkillRepository)


@pytest.fixture
def owner_user():
    """User who owns skills"""
    return User(
        id="owner-123",
        username="owner",
        name="Owner User",
        project_names=["project-a", "project-b"],
        admin_project_names=[],
    )


@pytest.fixture
def other_user():
    """Different user without ownership"""
    return User(
        id="other-456",
        username="other",
        name="Other User",
        project_names=["project-b", "project-c"],
        admin_project_names=[],
    )


@pytest.fixture
def admin_user():
    """Admin user with elevated privileges"""
    user = User(
        id="admin-789",
        username="admin",
        name="Admin User",
        project_names=["project-a"],
        admin_project_names=[],
    )
    # Admin check happens via config.ENV == "local" or roles
    return user


@pytest.fixture
def project_manager_user():
    """Project manager user with elevated privileges for their project"""
    return User(
        id="manager-999",
        username="manager",
        name="Project Manager",
        project_names=["project-a", "project-b"],
        admin_project_names=["project-a"],  # Manager of project-a
    )


@pytest.fixture
def sample_skill(owner_user):
    """Sample skill owned by owner_user"""
    return Skill(
        id=str(uuid4()),
        name="test-skill",
        description="Test skill description",
        content="# Test Skill\n\nTest content with sufficient length to pass validation." * 10,
        project="project-a",
        visibility=SkillVisibility.PRIVATE,
        categories=["development"],
        created_by=CreatedByUser(
            id=owner_user.id,
            name=owner_user.name,
            username=owner_user.username,
        ),
        created_date=datetime.now(UTC),
        updated_date=None,
        unique_likes_count=0,
        unique_dislikes_count=0,
    )


def create_assistant_mock(created_by: CreatedByUser, skill_ids: list[str] = None, project: str = "project-a"):
    """
    Create a proper assistant mock that implements the Owned interface.

    This is required because the Ability framework checks if objects inherit from Owned.
    """
    from codemie.core.ability import Owned, Role

    class MockAssistant(Owned):
        def __init__(self, created_by, skill_ids, project):
            self.created_by = created_by
            self.skill_ids = skill_ids or []
            self.project = project
            self.name = "Test Assistant"
            self.updated_date = None
            self._update_called = False

        def is_owned_by(self, user: User) -> bool:
            return self.created_by and self.created_by.id == user.id

        def is_managed_by(self, user: User) -> bool:
            return self.project in user.applications_admin

        def is_shared_with(self, user: User) -> bool:
            return self.project in user.applications

        def update(self, validate=True):
            self._update_called = True

    # Register permissions for MockAssistant (same as Assistant)
    if "MockAssistant" not in Ability.PERMISSIONS:
        Ability.PERMISSIONS["MockAssistant"] = {
            Action.READ: [Role.SHARED_WITH, Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.WRITE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
            Action.DELETE: [Role.OWNED_BY, Role.MANAGED_BY, Role.ADMIN],
        }

    return MockAssistant(created_by, skill_ids, project)


@pytest.fixture
def public_skill():
    """Public skill accessible to all"""
    return Skill(
        id=str(uuid4()),
        name="public-skill",
        description="Public skill description",
        content="Public content " * 20,
        project="project-a",
        visibility=SkillVisibility.PUBLIC,
        categories=["documentation"],
        created_by=CreatedByUser(id="creator-999", name="Creator", username="creator"),
        created_date=datetime.now(UTC),
        updated_date=None,
        unique_likes_count=5,
        unique_dislikes_count=1,
    )


@pytest.fixture
def project_skill(other_user):
    """Project-level skill"""
    return Skill(
        id=str(uuid4()),
        name="project-skill",
        description="Project skill description",
        content="Project content " * 20,
        project="project-b",
        visibility=SkillVisibility.PROJECT,
        categories=["testing"],
        created_by=CreatedByUser(
            id=other_user.id,
            name=other_user.name,
            username=other_user.username,
        ),
        created_date=datetime.now(UTC),
        updated_date=None,
        unique_likes_count=0,
        unique_dislikes_count=0,
    )


# =============================================================================
# Access Control Tests
# =============================================================================


class TestAccessControl:
    """Test access control logic using Ability framework"""

    def test_owner_has_full_access(self, owner_user, sample_skill):
        # Act & Assert
        ability = Ability(owner_user)
        assert ability.can(Action.READ, sample_skill) is True
        assert ability.can(Action.WRITE, sample_skill) is True
        assert ability.can(Action.DELETE, sample_skill) is True

    def test_admin_has_full_access(self, sample_skill):
        # Act & Assert
        with patch("codemie.rest_api.security.user.config.ENV", "local"):  # Makes user admin
            admin_user = User(id="admin-123", username="admin", project_names=["project-x"], admin_project_names=[])
            ability = Ability(admin_user)
            assert ability.can(Action.READ, sample_skill) is True
            assert ability.can(Action.WRITE, sample_skill) is True
            assert ability.can(Action.DELETE, sample_skill) is True

    def test_project_manager_has_full_access_in_their_project(self, project_manager_user, sample_skill):
        # sample_skill is in project-a, manager manages project-a
        # Act & Assert
        ability = Ability(project_manager_user)
        assert ability.can(Action.READ, sample_skill) is True
        assert ability.can(Action.WRITE, sample_skill) is True
        assert ability.can(Action.DELETE, sample_skill) is True

    def test_public_visibility_allows_read_access(self, other_user, public_skill):
        # Act & Assert
        ability = Ability(other_user)
        assert ability.can(Action.READ, public_skill) is True

    def test_public_visibility_denies_write_access(self, other_user, public_skill):
        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            ability = Ability(other_user)
            assert ability.can(Action.WRITE, public_skill) is False
            assert ability.can(Action.DELETE, public_skill) is False

    def test_project_visibility_allows_read_with_project_access(self, owner_user, project_skill):
        # project_skill is in project-b, owner_user has access to project-b
        # Act & Assert
        ability = Ability(owner_user)
        assert ability.can(Action.READ, project_skill) is True

    def test_project_visibility_denies_read_without_project_access(self, project_skill):
        # Create user without project-b access
        user_no_access = User(
            id="no-access",
            username="noAccess",
            project_names=["project-z"],
            admin_project_names=[],
        )
        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            ability = Ability(user_no_access)
            assert ability.can(Action.READ, project_skill) is False

    def test_project_visibility_denies_write_for_non_owner_non_manager(self, owner_user, project_skill):
        # owner_user has project access but is not owner or manager of project-b
        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            ability = Ability(owner_user)
            assert ability.can(Action.WRITE, project_skill) is False

    def test_private_visibility_denies_access_for_non_owner(self, other_user, sample_skill):
        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            ability = Ability(other_user)
            assert ability.can(Action.READ, sample_skill) is False
            assert ability.can(Action.WRITE, sample_skill) is False
            assert ability.can(Action.DELETE, sample_skill) is False

    def test_missing_created_by_denies_access(self, other_user):
        # Skill without created_by
        skill_no_owner = Skill(
            id=str(uuid4()),
            name="no-owner-skill",
            description="No owner",
            content="Content " * 20,
            project="project-a",
            visibility=SkillVisibility.PRIVATE,
            categories=[],
            created_by=None,
            created_date=datetime.now(UTC),
        )
        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            ability = Ability(other_user)
            assert ability.can(Action.READ, skill_no_owner) is False

    def test_user_not_in_applications_denies_project_access(self):
        # User with no applications
        user_no_apps = User(id="no-apps", username="noApps", project_names=[], admin_project_names=[])
        skill = Skill(
            id=str(uuid4()),
            name="skill",
            description="Skill",
            content="Content " * 20,
            project="project-a",
            visibility=SkillVisibility.PROJECT,
            categories=[],
            created_by=CreatedByUser(id="other", name="Other", username="other"),
            created_date=datetime.now(UTC),
        )
        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            ability = Ability(user_no_apps)
            assert ability.can(Action.READ, skill) is False

    def test_get_user_abilities_for_owner(self, owner_user, sample_skill):
        # Act
        abilities = SkillService.get_user_abilities(owner_user, sample_skill)

        # Assert
        assert "read" in abilities
        assert "write" in abilities
        assert "delete" in abilities
        assert len(abilities) == 3

    def test_get_user_abilities_for_public_non_owner(self, other_user, public_skill):
        # Act - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            abilities = SkillService.get_user_abilities(other_user, public_skill)

            # Assert
            assert "read" in abilities
            assert "write" not in abilities
            assert "delete" not in abilities
            assert len(abilities) == 1

    def test_get_user_abilities_for_no_access(self, other_user, sample_skill):
        # Act - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            abilities = SkillService.get_user_abilities(other_user, sample_skill)

            # Assert
            assert len(abilities) == 0

    def test_get_user_abilities_for_project_manager(self, project_manager_user, sample_skill):
        # Act
        abilities = SkillService.get_user_abilities(project_manager_user, sample_skill)

        # Assert - manager should have full access
        assert "read" in abilities
        assert "write" in abilities
        assert "delete" in abilities
        assert len(abilities) == 3

    def test_raise_if_no_access_raises_exception(self, other_user, sample_skill):
        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SkillService._raise_if_no_access(other_user, sample_skill, Action.READ)

            assert exc_info.value.code == status.HTTP_403_FORBIDDEN
            assert exc_info.value.message == SkillErrors.PERMISSION_DENIED
            assert "test-skill" in exc_info.value.details

    def test_raise_if_no_access_does_not_raise_for_owner(self, owner_user, sample_skill):
        # Act & Assert - should not raise
        SkillService._raise_if_no_access(owner_user, sample_skill, Action.READ)
        SkillService._raise_if_no_access(owner_user, sample_skill, Action.WRITE)
        SkillService._raise_if_no_access(owner_user, sample_skill, Action.DELETE)


# =============================================================================
# Frontmatter Parsing Tests
# =============================================================================


class TestFrontmatterParsing:
    """Test YAML frontmatter parsing"""

    def test_parse_valid_frontmatter(self):
        # Arrange
        content = """---
name: test-skill
description: A test skill
---

# Skill Content
This is the actual skill content."""

        # Act
        result = SkillService._parse_frontmatter(content)

        # Assert
        assert result.name == "test-skill"
        assert result.description == "A test skill"
        assert "# Skill Content" in result.content
        assert "This is the actual skill content." in result.content
        assert "---" not in result.content

    def test_parse_frontmatter_missing_start_delimiter(self):
        # Arrange
        content = """name: test-skill
description: A test skill
---

Content"""

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            SkillService._parse_frontmatter(content)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert exc_info.value.message == SkillErrors.INVALID_SKILL_FORMAT
        assert exc_info.value.details == SkillErrors.INVALID_FRONTMATTER_START

    def test_parse_frontmatter_missing_end_delimiter(self):
        # Arrange
        content = """---
name: test-skill
description: A test skill

Content without closing delimiter"""

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            SkillService._parse_frontmatter(content)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert exc_info.value.details == SkillErrors.INVALID_FRONTMATTER_END

    def test_parse_frontmatter_invalid_yaml(self):
        # Arrange
        content = """---
name: test-skill
description: [unclosed list
---

Content"""

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            SkillService._parse_frontmatter(content)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert "YAML" in exc_info.value.details or "parse" in exc_info.value.details.lower()

    def test_parse_frontmatter_empty(self):
        # Arrange
        content = """---
---

Content"""

        # Act
        result = SkillService._parse_frontmatter(content)

        # Assert
        assert result.name is None
        assert result.description is None
        assert result.content == "Content"

    def test_parse_frontmatter_with_extra_fields(self):
        # Arrange
        content = """---
name: test-skill
description: A test skill
author: John Doe
version: 1.0.0
---

Content"""

        # Act
        result = SkillService._parse_frontmatter(content)

        # Assert - only name and description are extracted
        assert result.name == "test-skill"
        assert result.description == "A test skill"
        assert result.content == "Content"


# =============================================================================
# Create Skill Tests
# =============================================================================


class TestCreateSkill:
    """Test skill creation"""

    def test_create_skill_success(self, mock_repository, owner_user, sample_skill):
        # Arrange
        request = SkillCreateRequest(
            name="new-skill",
            description="New skill description",
            content="New skill content " * 20,
            project="project-a",
            visibility=SkillVisibility.PRIVATE,
            categories=[SkillCategory.DEVELOPMENT],
        )
        mock_repository.get_by_name_author_project.return_value = None
        mock_repository.create.return_value = sample_skill

        # Act
        with patch.object(SkillRepository, "get_by_name_author_project", mock_repository.get_by_name_author_project):
            with patch.object(SkillRepository, "create", mock_repository.create):
                result = SkillService.create_skill(request, owner_user)

        # Assert
        mock_repository.get_by_name_author_project.assert_called_once_with(
            name="new-skill",
            author_id=owner_user.id,
            project="project-a",
        )
        mock_repository.create.assert_called_once()
        create_call_args = mock_repository.create.call_args[0][0]
        assert create_call_args["name"] == "new-skill"
        assert create_call_args["description"] == "New skill description"
        assert create_call_args["project"] == "project-a"
        assert create_call_args["visibility"] == SkillVisibility.PRIVATE
        assert create_call_args["categories"] == ["development"]
        assert create_call_args["created_by"].id == owner_user.id
        assert result is not None

    def test_create_skill_duplicate_name(self, mock_repository, owner_user, sample_skill):
        # Arrange
        request = SkillCreateRequest(
            name="test-skill",
            description="Duplicate name",
            content="Content " * 20,
            project="project-a",
            visibility=SkillVisibility.PRIVATE,
            categories=[],
        )
        mock_repository.get_by_name_author_project.return_value = sample_skill

        # Act & Assert
        with patch.object(SkillRepository, "get_by_name_author_project", mock_repository.get_by_name_author_project):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SkillService.create_skill(request, owner_user)

        assert exc_info.value.code == status.HTTP_409_CONFLICT
        assert "already exists" in exc_info.value.message.lower()

    def test_create_skill_created_by_populated(self, mock_repository, owner_user, sample_skill):
        # Arrange
        request = SkillCreateRequest(
            name="new-skill",
            description="New skill description",
            content="Content " * 20,
            project="project-a",
            visibility=SkillVisibility.PRIVATE,
            categories=[],
        )
        mock_repository.get_by_name_author_project.return_value = None
        mock_repository.create.return_value = sample_skill

        # Act
        with patch.object(SkillRepository, "get_by_name_author_project", mock_repository.get_by_name_author_project):
            with patch.object(SkillRepository, "create", mock_repository.create):
                with patch.object(SkillRepository, "count_assistants_using_skill", return_value=0):
                    SkillService.create_skill(request, owner_user)

        # Assert
        create_call_args = mock_repository.create.call_args[0][0]
        assert create_call_args["created_by"].id == owner_user.id
        assert create_call_args["created_by"].name == owner_user.name
        assert create_call_args["created_by"].username == owner_user.username

    def test_create_skill_categories_converted_to_values(self, mock_repository, owner_user, sample_skill):
        # Arrange
        request = SkillCreateRequest(
            name="new-skill",
            description="New skill description",
            content="Content " * 20,
            project="project-a",
            visibility=SkillVisibility.PRIVATE,
            categories=[SkillCategory.DEVELOPMENT, SkillCategory.TESTING],
        )
        mock_repository.get_by_name_author_project.return_value = None
        mock_repository.create.return_value = sample_skill

        # Act
        with patch.object(SkillRepository, "get_by_name_author_project", mock_repository.get_by_name_author_project):
            with patch.object(SkillRepository, "create", mock_repository.create):
                with patch.object(SkillRepository, "count_assistants_using_skill", return_value=0):
                    SkillService.create_skill(request, owner_user)

        # Assert
        create_call_args = mock_repository.create.call_args[0][0]
        assert create_call_args["categories"] == ["development", "testing"]


# =============================================================================
# Update Skill Tests
# =============================================================================


class TestUpdateSkill:
    """Test skill updates"""

    def test_update_skill_success(self, mock_repository, owner_user, sample_skill):
        # Arrange
        updated_skill = Skill(**sample_skill.model_dump())
        updated_skill.description = "Updated description"
        request = SkillUpdateRequest(description="Updated description")
        mock_repository.get_by_id.return_value = sample_skill
        mock_repository.update.return_value = updated_skill
        mock_repository.count_assistants_using_skill.return_value = 2

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with patch.object(SkillRepository, "update", mock_repository.update):
                with patch.object(
                    SkillRepository, "count_assistants_using_skill", mock_repository.count_assistants_using_skill
                ):
                    result = SkillService.update_skill(sample_skill.id, request, owner_user)

        # Assert
        mock_repository.update.assert_called_once_with(sample_skill.id, {"description": "Updated description"})
        assert result.description == "Updated description"

    def test_update_skill_not_found(self, owner_user):
        # Arrange
        request = SkillUpdateRequest(description="Updated description")

        # Act & Assert
        with patch.object(SkillRepository, "get_by_id", return_value=None):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SkillService.update_skill("nonexistent-id", request, owner_user)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND
        assert exc_info.value.message == SkillErrors.MSG_SKILL_NOT_FOUND

    def test_update_skill_permission_denied(self, other_user, sample_skill):
        # Arrange
        request = SkillUpdateRequest(description="Updated description")

        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            with patch.object(SkillRepository, "get_by_id", return_value=sample_skill):
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    SkillService.update_skill(sample_skill.id, request, other_user)

            assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_update_skill_duplicate_name(self, mock_repository, owner_user, sample_skill):
        # Arrange
        existing_skill = Skill(
            id="other-id",
            name="existing-skill",
            description="Existing",
            content="Content " * 20,
            project="project-a",
            visibility=SkillVisibility.PRIVATE,
            categories=[],
            created_by=CreatedByUser(id=owner_user.id, name=owner_user.name, username=owner_user.username),
            created_date=datetime.now(UTC),
        )
        request = SkillUpdateRequest(name="existing-skill")
        mock_repository.get_by_id.return_value = sample_skill
        mock_repository.get_by_name_author_project.return_value = existing_skill

        # Act & Assert
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with patch.object(
                SkillRepository, "get_by_name_author_project", mock_repository.get_by_name_author_project
            ):
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    SkillService.update_skill(sample_skill.id, request, owner_user)

        assert exc_info.value.code == status.HTTP_409_CONFLICT

    def test_update_skill_no_changes(self, mock_repository, owner_user, sample_skill):
        # Arrange
        request = SkillUpdateRequest()
        mock_repository.get_by_id.return_value = sample_skill
        mock_repository.count_assistants_using_skill.return_value = 0

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with patch.object(
                SkillRepository, "count_assistants_using_skill", mock_repository.count_assistants_using_skill
            ):
                result = SkillService.update_skill(sample_skill.id, request, owner_user)

        # Assert
        mock_repository.update.assert_not_called()
        assert result.id == sample_skill.id

    def test_update_skill_multiple_fields(self, mock_repository, owner_user, sample_skill):
        # Arrange
        updated_skill = Skill(**sample_skill.model_dump())
        updated_skill.name = "updated-name"
        updated_skill.description = "Updated description"
        updated_skill.visibility = SkillVisibility.PUBLIC
        request = SkillUpdateRequest(
            name="updated-name", description="Updated description", visibility=SkillVisibility.PUBLIC
        )
        mock_repository.get_by_id.return_value = sample_skill
        mock_repository.get_by_name_author_project.return_value = None
        mock_repository.update.return_value = updated_skill
        mock_repository.count_assistants_using_skill.return_value = 0

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with patch.object(
                SkillRepository, "get_by_name_author_project", mock_repository.get_by_name_author_project
            ):
                with patch.object(SkillRepository, "update", mock_repository.update):
                    with patch.object(
                        SkillRepository, "count_assistants_using_skill", mock_repository.count_assistants_using_skill
                    ):
                        _ = SkillService.update_skill(sample_skill.id, request, owner_user)

        # Assert
        update_call_args = mock_repository.update.call_args[0][1]
        assert update_call_args["name"] == "updated-name"
        assert update_call_args["description"] == "Updated description"
        assert update_call_args["visibility"] == SkillVisibility.PUBLIC


# =============================================================================
# Delete Skill Tests
# =============================================================================


class TestDeleteSkill:
    """Test skill deletion"""

    def test_delete_skill_success(self, mock_repository, owner_user, sample_skill):
        # Arrange
        mock_repository.get_by_id.return_value = sample_skill
        mock_repository.delete.return_value = None

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with patch.object(SkillRepository, "delete", mock_repository.delete):
                with patch.object(SkillRepository, "remove_skill_from_all_assistants") as mock_remove:
                    SkillService.delete_skill(sample_skill.id, owner_user)

        # Assert
        mock_remove.assert_called_once_with(sample_skill.id)
        mock_repository.delete.assert_called_once_with(sample_skill.id)

    def test_delete_skill_not_found(self, mock_repository, owner_user):
        # Arrange
        mock_repository.get_by_id.return_value = None

        # Act & Assert
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SkillService.delete_skill("nonexistent-id", owner_user)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND

    def test_delete_skill_permission_denied(self, mock_repository, other_user, sample_skill):
        # Arrange
        mock_repository.get_by_id.return_value = sample_skill

        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    SkillService.delete_skill(sample_skill.id, other_user)

            assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    @patch("codemie.repository.skill_repository.Session")
    def test_remove_skill_from_all_assistants(self, mock_session_class):
        # Arrange
        from codemie.rest_api.models.assistant import Assistant

        skill_id = "skill-to-remove"
        assistant1 = MagicMock(spec=Assistant)
        assistant1.skill_ids = [skill_id, "other-skill"]
        assistant2 = MagicMock(spec=Assistant)
        assistant2.skill_ids = [skill_id]

        mock_session = MagicMock()
        mock_session_class.return_value.__enter__.return_value = mock_session
        mock_session.exec.return_value.all.return_value = [assistant1, assistant2]

        with patch.object(Skill, "get_engine"):
            # Act
            SkillRepository.remove_skill_from_all_assistants(skill_id)

        # Assert
        assert assistant1.skill_ids == ["other-skill"]
        assert assistant2.skill_ids == []
        assert mock_session.add.call_count == 2
        mock_session.commit.assert_called_once()


# =============================================================================
# Import Skill Tests
# =============================================================================


class TestImportSkill:
    """Test skill import from markdown"""

    def test_import_skill_valid_markdown(self, mock_repository, owner_user, sample_skill):
        # Arrange
        markdown = """---
name: imported-skill
description: Imported skill description
---

# Imported Skill
This is the imported skill content with sufficient length to pass validation requirements."""
        encoded = base64.b64encode(markdown.encode("utf-8")).decode("utf-8")
        mock_repository.get_by_name_author_project.return_value = None
        mock_repository.create.return_value = sample_skill

        # Act
        with patch.object(SkillRepository, "get_by_name_author_project", mock_repository.get_by_name_author_project):
            with patch.object(SkillRepository, "create", mock_repository.create):
                result = SkillService.import_skill(
                    encoded, "skill.md", "project-a", owner_user, SkillVisibility.PRIVATE
                )

        # Assert
        assert result is not None
        create_call_args = mock_repository.create.call_args[0][0]
        assert create_call_args["name"] == "imported-skill"
        assert create_call_args["description"] == "Imported skill description"

    def test_import_skill_invalid_base64(self, owner_user):
        # Arrange
        invalid_encoded = "not-valid-base64!!!"

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            SkillService.import_skill(invalid_encoded, "skill.md", "project-a", owner_user, SkillVisibility.PRIVATE)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert "decode" in exc_info.value.details.lower()

    def test_import_skill_missing_name(self, owner_user):
        # Arrange
        markdown = """---
description: Missing name field
---

Content with sufficient length to pass validation requirements for the skill content field."""
        encoded = base64.b64encode(markdown.encode("utf-8")).decode("utf-8")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            SkillService.import_skill(encoded, "skill.md", "project-a", owner_user, SkillVisibility.PRIVATE)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert SkillErrors.MISSING_FRONTMATTER_FIELDS in exc_info.value.details

    def test_import_skill_invalid_name_format(self, owner_user):
        # Arrange
        markdown = """---
name: Invalid_Name_With_Underscores
description: Invalid name format
---

Content with sufficient length to pass validation requirements for the skill content field and more."""
        encoded = base64.b64encode(markdown.encode("utf-8")).decode("utf-8")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            SkillService.import_skill(encoded, "skill.md", "project-a", owner_user, SkillVisibility.PRIVATE)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert "kebab-case" in exc_info.value.details.lower() or "not valid" in exc_info.value.details.lower()

    def test_import_skill_content_too_short(self, owner_user):
        # Arrange
        markdown = """---
name: valid-skill
description: Valid description
---

Short"""
        encoded = base64.b64encode(markdown.encode("utf-8")).decode("utf-8")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            SkillService.import_skill(encoded, "skill.md", "project-a", owner_user, SkillVisibility.PRIVATE)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert "too short" in exc_info.value.details.lower()


# =============================================================================
# Export Skill Tests
# =============================================================================


class TestExportSkill:
    """Test skill export to markdown"""

    def test_export_skill_success(self, mock_repository, owner_user, sample_skill):
        # Arrange
        mock_repository.get_by_id.return_value = sample_skill

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            result = SkillService.export_skill(sample_skill.id, owner_user)

        # Assert
        assert result.content.startswith("---\n")
        assert f"name: {sample_skill.name}" in result.content
        assert f"description: {sample_skill.description}" in result.content
        assert sample_skill.content in result.content
        assert result.filename == f"{sample_skill.name}.md"

    def test_export_skill_not_found(self, mock_repository, owner_user):
        # Arrange
        mock_repository.get_by_id.return_value = None

        # Act & Assert
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SkillService.export_skill("nonexistent-id", owner_user)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND

    def test_export_skill_permission_denied(self, mock_repository, other_user, sample_skill):
        # Arrange
        mock_repository.get_by_id.return_value = sample_skill

        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    SkillService.export_skill(sample_skill.id, other_user)

            assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_export_skill_correct_format(self, mock_repository, owner_user, sample_skill):
        # Arrange
        mock_repository.get_by_id.return_value = sample_skill

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            result = SkillService.export_skill(sample_skill.id, owner_user)

        # Assert - verify format can be parsed back
        lines = result.content.split("\n")
        assert lines[0] == "---"
        assert any(f"name: {sample_skill.name}" in line for line in lines)
        assert any(f"description: {sample_skill.description}" in line for line in lines)
        # Find closing delimiter
        closing_index = None
        for i, line in enumerate(lines[1:], start=1):
            if line == "---":
                closing_index = i
                break
        assert closing_index is not None
        assert sample_skill.content in "\n".join(lines[closing_index + 1 :])


# =============================================================================
# Get Skill Tests
# =============================================================================


class TestGetSkill:
    """Test skill retrieval operations"""

    def test_get_skill_by_id_success(self, mock_repository, owner_user, sample_skill):
        # Arrange
        mock_repository.get_by_id.return_value = sample_skill
        mock_repository.count_assistants_using_skill.return_value = 3

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with patch.object(
                SkillRepository, "count_assistants_using_skill", mock_repository.count_assistants_using_skill
            ):
                result = SkillService.get_skill_by_id(sample_skill.id, owner_user)

        # Assert
        assert result.id == sample_skill.id
        assert result.assistants_count == 3
        assert "read" in result.user_abilities
        assert "write" in result.user_abilities
        assert "delete" in result.user_abilities

    def test_get_skill_by_id_not_found(self, mock_repository, owner_user):
        # Arrange
        mock_repository.get_by_id.return_value = None

        # Act & Assert
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SkillService.get_skill_by_id("nonexistent-id", owner_user)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND

    def test_get_skill_by_id_permission_denied(self, mock_repository, other_user, sample_skill):
        # Arrange
        mock_repository.get_by_id.return_value = sample_skill

        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    SkillService.get_skill_by_id(sample_skill.id, other_user)

            assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_get_skills_by_ids_filters_by_access(
        self, mock_repository, owner_user, sample_skill, public_skill, project_skill
    ):
        # Arrange - owner can access own skill and public skill, but not private project_skill
        mock_repository.get_by_ids.return_value = [sample_skill, public_skill, project_skill]

        # Act
        with patch.object(SkillRepository, "get_by_ids", mock_repository.get_by_ids):
            result = SkillService.get_skills_by_ids([sample_skill.id, public_skill.id, project_skill.id], owner_user)

        # Assert - should include sample_skill (owned), public_skill (public), project_skill (has project access)
        assert len(result) == 3
        result_ids = [s.id for s in result]
        assert sample_skill.id in result_ids
        assert public_skill.id in result_ids
        assert project_skill.id in result_ids

    def test_get_skills_by_ids_empty_list(self, owner_user):
        # Act
        result = SkillService.get_skills_by_ids([], owner_user)

        # Assert
        assert result == []


# =============================================================================
# List Skills Tests
# =============================================================================


class TestListSkills:
    """Test skill listing with pagination and filters"""

    def test_list_skills_basic(self, mock_repository, owner_user, sample_skill):
        # Arrange
        list_result = SkillListResult(
            skills=[sample_skill],
            assistants_count_map={sample_skill.id: 2},
            page=0,
            per_page=20,
            total=1,
            pages=1,
        )
        mock_repository.list_accessible_to_user.return_value = list_result

        # Act
        with patch.object(SkillRepository, "list_accessible_to_user", mock_repository.list_accessible_to_user):
            result = SkillService.list_skills(owner_user)

        # Assert
        assert result.total == 1
        assert len(result.skills) == 1
        assert result.skills[0].id == sample_skill.id
        assert result.skills[0].assistants_count == 2

    def test_list_skills_with_filters(self, mock_repository, owner_user):
        # Arrange
        list_result = SkillListResult(skills=[], assistants_count_map={}, page=0, per_page=20, total=0, pages=0)
        mock_repository.list_accessible_to_user.return_value = list_result

        # Act
        with patch.object(SkillRepository, "list_accessible_to_user", mock_repository.list_accessible_to_user):
            SkillService.list_skills(
                owner_user,
                project=["project-a"],
                visibility=SkillVisibility.PUBLIC,
                categories=[SkillCategory.DEVELOPMENT],
                search_query="test",
                page=1,
                per_page=10,
            )

        # Assert - note: user.is_admin returns True in local environment
        mock_repository.list_accessible_to_user.assert_called_once_with(
            user_id=owner_user.id,
            user_applications=owner_user.project_names,
            user_is_global_admin=owner_user.is_admin,
            user_admin_projects=owner_user.admin_project_names,
            project=["project-a"],
            visibility=SkillVisibility.PUBLIC,
            categories=[SkillCategory.DEVELOPMENT],
            search_query="test",
            created_by=None,
            page=1,
            per_page=10,
            marketplace_filter=MarketplaceFilter.DEFAULT,
            sort_by=SkillSortBy.CREATED_DATE,
        )

    def test_list_skills_with_multi_project_filter(self, mock_repository, owner_user):
        # Arrange
        list_result = SkillListResult(skills=[], assistants_count_map={}, page=0, per_page=20, total=0, pages=0)
        mock_repository.list_accessible_to_user.return_value = list_result

        # Act
        with patch.object(SkillRepository, "list_accessible_to_user", mock_repository.list_accessible_to_user):
            SkillService.list_skills(
                owner_user,
                project=["project-a", "project-b"],
                page=0,
                per_page=20,
            )

        # Assert
        mock_repository.list_accessible_to_user.assert_called_once()
        call_kwargs = mock_repository.list_accessible_to_user.call_args[1]
        assert call_kwargs["project"] == ["project-a", "project-b"]

    def test_list_skills_with_created_by_filter(self, mock_repository, owner_user):
        # Arrange
        list_result = SkillListResult(skills=[], assistants_count_map={}, page=0, per_page=20, total=0, pages=0)
        mock_repository.list_accessible_to_user.return_value = list_result

        # Act
        with patch.object(SkillRepository, "list_accessible_to_user", mock_repository.list_accessible_to_user):
            SkillService.list_skills(
                owner_user,
                created_by="Owner User",
            )

        # Assert - created_by should be passed as user name
        mock_repository.list_accessible_to_user.assert_called_once()
        call_kwargs = mock_repository.list_accessible_to_user.call_args[1]
        assert call_kwargs["created_by"] == "Owner User"

    def test_list_skills_with_include_marketplace(self, mock_repository, owner_user):
        # Arrange
        list_result = SkillListResult(skills=[], assistants_count_map={}, page=0, per_page=20, total=0, pages=0)
        mock_repository.list_accessible_to_user.return_value = list_result

        # Act
        with patch.object(SkillRepository, "list_accessible_to_user", mock_repository.list_accessible_to_user):
            SkillService.list_skills(
                owner_user,
                project=["codemie"],
                marketplace_filter=MarketplaceFilter.INCLUDE,
            )

        # Assert - marketplace_filter should be passed through to repository
        mock_repository.list_accessible_to_user.assert_called_once()
        call_kwargs = mock_repository.list_accessible_to_user.call_args[1]
        assert call_kwargs["marketplace_filter"] == MarketplaceFilter.INCLUDE
        assert call_kwargs["project"] == ["codemie"]

    def test_list_skills_with_assistant_attached(self, mock_repository, owner_user, sample_skill):
        # Arrange
        list_result = SkillListResult(
            skills=[sample_skill],
            assistants_count_map={sample_skill.id: 1},
            page=0,
            per_page=20,
            total=1,
            pages=1,
        )
        mock_repository.list_accessible_to_user.return_value = list_result

        # Mock Assistant
        mock_assistant = MagicMock()
        mock_assistant.skill_ids = [sample_skill.id]

        # Act
        with patch.object(SkillRepository, "list_accessible_to_user", mock_repository.list_accessible_to_user):
            with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
                mock_assistant_class.find_by_id.return_value = mock_assistant
                result = SkillService.list_skills(owner_user, assistant_id="asst-123")

        # Assert
        assert result.skills[0].is_attached is True

    def test_list_skills_exclude_marketplace(self, mock_repository, owner_user):
        # Arrange
        list_result = SkillListResult(skills=[], assistants_count_map={}, page=0, per_page=20, total=0, pages=0)
        mock_repository.list_accessible_to_user.return_value = list_result

        # Act
        with patch.object(SkillRepository, "list_accessible_to_user", mock_repository.list_accessible_to_user):
            SkillService.list_skills(owner_user, marketplace_filter=MarketplaceFilter.EXCLUDE)

        # Assert
        call_kwargs = mock_repository.list_accessible_to_user.call_args[1]
        assert call_kwargs["marketplace_filter"] == MarketplaceFilter.EXCLUDE


# =============================================================================
# Assistant-Skill Association Tests
# =============================================================================


class TestAssistantSkillOperations:
    """Test skill attachment/detachment to assistants"""

    def test_attach_skill_to_assistant_success(self, mock_repository, owner_user, sample_skill):
        # Arrange
        mock_assistant = create_assistant_mock(
            created_by=CreatedByUser(id=owner_user.id, name=owner_user.name, username=owner_user.username), skill_ids=[]
        )
        mock_repository.get_by_id.return_value = sample_skill

        # Act
        with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
            mock_assistant_class.find_by_id.return_value = mock_assistant
            with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
                SkillService.attach_skill_to_assistant("asst-123", sample_skill.id, owner_user)

        # Assert
        assert sample_skill.id in mock_assistant.skill_ids
        assert mock_assistant._update_called

    def test_attach_skill_assistant_not_found(self, owner_user):
        # Arrange & Act & Assert
        with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
            mock_assistant_class.find_by_id.return_value = None
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SkillService.attach_skill_to_assistant("nonexistent-asst", "skill-id", owner_user)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND
        assert exc_info.value.message == SkillErrors.MSG_ASSISTANT_NOT_FOUND

    def test_attach_skill_not_owner_of_assistant(self, mock_repository, other_user, sample_skill):
        # Arrange
        mock_assistant = create_assistant_mock(
            created_by=CreatedByUser(id="different-owner", name="Different", username="different"), skill_ids=[]
        )

        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
                mock_assistant_class.find_by_id.return_value = mock_assistant
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    SkillService.attach_skill_to_assistant("asst-123", sample_skill.id, other_user)

            assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_attach_skill_admin_can_attach(self, mock_repository):
        # Arrange
        # Create a public skill so admin can read it
        admin_skill = Skill(
            id=str(uuid4()),
            name="admin-skill",
            description="Admin accessible skill",
            content="Content " * 20,
            project="project-a",
            visibility=SkillVisibility.PUBLIC,  # Public so anyone can read
            categories=[],
            created_by=CreatedByUser(id="creator", name="Creator", username="creator"),
            created_date=datetime.now(UTC),
        )
        mock_assistant = create_assistant_mock(
            created_by=CreatedByUser(id="different-owner", name="Different", username="different"), skill_ids=[]
        )
        mock_repository.get_by_id.return_value = admin_skill

        # Act
        with patch("codemie.rest_api.security.user.config.ENV", "local"):  # Makes user admin
            admin_user = User(id="admin-789", username="admin", name="Admin User", project_names=["project-a"])
            with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
                mock_assistant_class.find_by_id.return_value = mock_assistant
                with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
                    SkillService.attach_skill_to_assistant("asst-123", admin_skill.id, admin_user)

        # Assert - should succeed
        assert admin_skill.id in mock_assistant.skill_ids

    def test_attach_skill_not_found(self, mock_repository, owner_user):
        # Arrange
        mock_assistant = create_assistant_mock(
            created_by=CreatedByUser(id=owner_user.id, name=owner_user.name, username=owner_user.username)
        )
        mock_repository.get_by_id.return_value = None

        # Act & Assert
        with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
            mock_assistant_class.find_by_id.return_value = mock_assistant
            with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    SkillService.attach_skill_to_assistant("asst-123", "nonexistent-skill", owner_user)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND
        assert exc_info.value.message == SkillErrors.MSG_SKILL_NOT_FOUND

    def test_attach_skill_no_read_access(self, mock_repository, other_user, sample_skill):
        # Arrange - sample_skill is private, other_user can't access
        mock_assistant = create_assistant_mock(
            created_by=CreatedByUser(id=other_user.id, name=other_user.name, username=other_user.username), skill_ids=[]
        )
        mock_repository.get_by_id.return_value = sample_skill

        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
                mock_assistant_class.find_by_id.return_value = mock_assistant
                with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
                    with pytest.raises(ExtendedHTTPException) as exc_info:
                        SkillService.attach_skill_to_assistant("asst-123", sample_skill.id, other_user)

            assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_attach_skill_idempotent(self, mock_repository, owner_user, sample_skill):
        # Arrange - skill already attached
        mock_assistant = create_assistant_mock(
            created_by=CreatedByUser(id=owner_user.id, name=owner_user.name, username=owner_user.username),
            skill_ids=[sample_skill.id],
        )
        mock_repository.get_by_id.return_value = sample_skill

        # Act
        with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
            mock_assistant_class.find_by_id.return_value = mock_assistant
            with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
                SkillService.attach_skill_to_assistant("asst-123", sample_skill.id, owner_user)

        # Assert - should not duplicate
        assert mock_assistant.skill_ids.count(sample_skill.id) == 1
        assert not mock_assistant._update_called

    def test_detach_skill_from_assistant_success(self, owner_user, sample_skill):
        # Arrange
        mock_assistant = create_assistant_mock(
            created_by=CreatedByUser(id=owner_user.id, name=owner_user.name, username=owner_user.username),
            skill_ids=[sample_skill.id, "other-skill"],
        )

        # Act
        with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
            mock_assistant_class.find_by_id.return_value = mock_assistant
            SkillService.detach_skill_from_assistant("asst-123", sample_skill.id, owner_user)

        # Assert
        assert sample_skill.id not in mock_assistant.skill_ids
        assert "other-skill" in mock_assistant.skill_ids
        assert mock_assistant._update_called

    def test_detach_skill_assistant_not_found(self, owner_user):
        # Act & Assert
        with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
            mock_assistant_class.find_by_id.return_value = None
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SkillService.detach_skill_from_assistant("nonexistent-asst", "skill-id", owner_user)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND

    def test_detach_skill_not_owner_of_assistant(self, sample_skill):
        # Arrange
        other_user = User(id="other-456", username="other", name="Other User", project_names=["project-c"])
        mock_assistant = create_assistant_mock(
            created_by=CreatedByUser(id="different-owner", name="Different", username="different")
        )

        # Act & Assert - ensure user is not admin by patching ENV to non-local
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
                mock_assistant_class.find_by_id.return_value = mock_assistant
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    SkillService.detach_skill_from_assistant("asst-123", sample_skill.id, other_user)

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_get_skills_for_assistant_success(self, mock_repository, owner_user, sample_skill, public_skill):
        # Arrange
        mock_assistant = MagicMock()
        mock_assistant.skill_ids = [sample_skill.id, public_skill.id]
        mock_repository.get_by_ids.return_value = [sample_skill, public_skill]

        # Act
        with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
            mock_assistant_class.find_by_id.return_value = mock_assistant
            with patch.object(SkillRepository, "get_by_ids", mock_repository.get_by_ids):
                result = SkillService.get_skills_for_assistant("asst-123", owner_user)

        # Assert
        assert len(result) == 2
        assert all(skill.is_attached for skill in result)

    def test_get_skills_for_assistant_not_found(self, owner_user):
        # Act & Assert
        with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
            mock_assistant_class.find_by_id.return_value = None
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SkillService.get_skills_for_assistant("nonexistent-asst", owner_user)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND

    def test_get_skills_for_assistant_empty_skills(self, owner_user):
        # Arrange
        mock_assistant = MagicMock()
        mock_assistant.skill_ids = []

        # Act
        with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
            mock_assistant_class.find_by_id.return_value = mock_assistant
            result = SkillService.get_skills_for_assistant("asst-123", owner_user)

        # Assert
        assert result == []


# =============================================================================
# Marketplace Operations Tests
# =============================================================================


class TestMarketplaceOperations:
    """Test marketplace publish/unpublish operations"""

    def test_publish_to_marketplace_success(self, mock_repository, owner_user, sample_skill):
        # Arrange
        mock_repository.get_by_id.return_value = sample_skill
        mock_repository.update.return_value = sample_skill

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with patch.object(SkillRepository, "update", mock_repository.update):
                SkillService.publish_to_marketplace(sample_skill.id, owner_user, categories=["development"])

        # Assert
        mock_repository.update.assert_called_once_with(
            sample_skill.id, {"visibility": SkillVisibility.PUBLIC, "categories": ["development"]}
        )

    def test_publish_to_marketplace_without_categories(self, mock_repository, owner_user, sample_skill):
        # Arrange
        mock_repository.get_by_id.return_value = sample_skill
        mock_repository.update.return_value = sample_skill

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with patch.object(SkillRepository, "update", mock_repository.update):
                SkillService.publish_to_marketplace(sample_skill.id, owner_user)

        # Assert
        mock_repository.update.assert_called_once_with(sample_skill.id, {"visibility": SkillVisibility.PUBLIC})

    def test_publish_to_marketplace_skill_not_found(self, mock_repository, owner_user):
        # Arrange
        mock_repository.get_by_id.return_value = None

        # Act & Assert
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SkillService.publish_to_marketplace("nonexistent-id", owner_user)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND

    def test_publish_to_marketplace_not_owner(self, sample_skill):
        # Arrange - other_user is not owner and not admin
        other_user = User(id="other-456", username="other", name="Other User", project_names=["project-c"])
        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            with patch.object(SkillRepository, "get_by_id", return_value=sample_skill):
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    SkillService.publish_to_marketplace(sample_skill.id, other_user)

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_publish_to_marketplace_admin_can_publish(self, mock_repository, sample_skill):
        # Arrange
        mock_repository.get_by_id.return_value = sample_skill
        mock_repository.update.return_value = sample_skill

        # Act
        with patch("codemie.rest_api.security.user.config.ENV", "local"):  # Makes user admin
            admin_user = User(id="admin-789", username="admin", name="Admin User", project_names=["project-a"])
            with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
                with patch.object(SkillRepository, "update", mock_repository.update):
                    SkillService.publish_to_marketplace(sample_skill.id, admin_user)

        # Assert - should succeed
        mock_repository.update.assert_called_once()

    def test_publish_to_marketplace_already_public(self, mock_repository, owner_user):
        # Arrange - create public skill owned by owner_user
        public_skill = Skill(
            id=str(uuid4()),
            name="public-skill",
            description="Public skill description",
            content="Public content " * 20,
            project="project-a",
            visibility=SkillVisibility.PUBLIC,
            categories=["documentation"],
            created_by=CreatedByUser(id=owner_user.id, name=owner_user.name, username=owner_user.username),
            created_date=datetime.now(UTC),
            updated_date=None,
            unique_likes_count=5,
            unique_dislikes_count=1,
        )
        mock_repository.get_by_id.return_value = public_skill
        mock_repository.update.return_value = public_skill

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with patch.object(SkillRepository, "update", mock_repository.update):
                SkillService.publish_to_marketplace(public_skill.id, owner_user, categories=["development", "testing"])

        # Assert - should still update
        mock_repository.update.assert_called_once()

    def test_unpublish_from_marketplace_success(self, mock_repository, owner_user):
        # Arrange - create public skill owned by owner_user
        public_skill = Skill(
            id=str(uuid4()),
            name="public-skill",
            description="Public skill description",
            content="Public content " * 20,
            project="project-a",
            visibility=SkillVisibility.PUBLIC,
            categories=["documentation"],
            created_by=CreatedByUser(id=owner_user.id, name=owner_user.name, username=owner_user.username),
            created_date=datetime.now(UTC),
            updated_date=None,
            unique_likes_count=5,
            unique_dislikes_count=1,
        )
        mock_repository.get_by_id.return_value = public_skill
        mock_repository.update.return_value = public_skill

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with patch.object(SkillRepository, "update", mock_repository.update):
                SkillService.unpublish_from_marketplace(public_skill.id, owner_user)

        # Assert
        mock_repository.update.assert_called_once_with(public_skill.id, {"visibility": SkillVisibility.PRIVATE})

    def test_unpublish_from_marketplace_not_found(self, mock_repository, owner_user):
        # Arrange
        mock_repository.get_by_id.return_value = None

        # Act & Assert
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SkillService.unpublish_from_marketplace("nonexistent-id", owner_user)

        assert exc_info.value.code == status.HTTP_404_NOT_FOUND

    def test_unpublish_from_marketplace_not_owner(self, public_skill):
        # Arrange - other_user is not owner and not admin
        other_user = User(id="other-456", username="other", name="Other User", project_names=["project-c"])
        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            with patch.object(SkillRepository, "get_by_id", return_value=public_skill):
                with pytest.raises(ExtendedHTTPException) as exc_info:
                    SkillService.unpublish_from_marketplace(public_skill.id, other_user)

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_unpublish_from_marketplace_admin_can_unpublish(self, mock_repository, public_skill):
        # Arrange
        mock_repository.get_by_id.return_value = public_skill
        mock_repository.update.return_value = public_skill

        # Act
        with patch("codemie.rest_api.security.user.config.ENV", "local"):  # Makes user admin
            admin_user = User(id="admin-789", username="admin", name="Admin User", project_names=["project-a"])
            with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
                with patch.object(SkillRepository, "update", mock_repository.update):
                    SkillService.unpublish_from_marketplace(public_skill.id, admin_user)

        # Assert - should succeed
        mock_repository.update.assert_called_once()


# =============================================================================
# Edge Cases and Error Handling Tests
# =============================================================================


class TestEdgeCases:
    """Test edge cases and error handling"""

    def test_skill_with_none_created_by_owner_access(self):
        # Arrange
        skill_no_owner = Skill(
            id=str(uuid4()),
            name="no-owner",
            description="No owner",
            content="Content " * 20,
            project="project-a",
            visibility=SkillVisibility.PRIVATE,
            categories=[],
            created_by=None,
            created_date=datetime.now(UTC),
        )
        user = User(id="user-123", username="user", project_names=["project-a"], admin_project_names=[])

        # Act & Assert - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            ability = Ability(user)
            assert ability.can(Action.READ, skill_no_owner) is False
            assert ability.can(Action.WRITE, skill_no_owner) is False

    def test_update_skill_same_name_as_self_allowed(self, owner_user, sample_skill):
        # Arrange
        request = SkillUpdateRequest(name=sample_skill.name, description="Updated description text")
        updated_skill = Skill(**sample_skill.model_dump())
        updated_skill.description = "Updated description text"

        # Act
        with patch.object(SkillRepository, "get_by_id", return_value=sample_skill):
            with patch.object(SkillRepository, "update", return_value=updated_skill) as mock_update:
                with patch.object(SkillRepository, "count_assistants_using_skill", return_value=0):
                    _ = SkillService.update_skill(sample_skill.id, request, owner_user)

        # Assert - should only update description
        update_call_args = mock_update.call_args[0][1]
        assert "name" not in update_call_args  # Name unchanged, not in updates
        assert update_call_args["description"] == "Updated description text"

    def test_list_skills_assistant_not_found_no_crash(self, owner_user, sample_skill):
        # Arrange
        list_result = SkillListResult(
            skills=[sample_skill],
            assistants_count_map={},
            page=0,
            per_page=20,
            total=1,
            pages=1,
        )

        # Act
        with patch.object(SkillRepository, "list_accessible_to_user", return_value=list_result):
            with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
                mock_assistant_class.find_by_id.return_value = None
                result = SkillService.list_skills(owner_user, assistant_id="nonexistent-asst")

        # Assert - should not crash, is_attached should be False
        assert result.skills[0].is_attached is False

    def test_get_skills_for_assistant_filters_by_access(self, other_user, sample_skill, public_skill):
        # Arrange - assistant has both private and public skills
        mock_assistant = MagicMock()
        mock_assistant.skill_ids = [sample_skill.id, public_skill.id]

        # Act - ensure user is not admin
        with patch("codemie.rest_api.security.user.config.ENV", "production"):
            with patch("codemie.rest_api.models.assistant.Assistant") as mock_assistant_class:
                mock_assistant_class.find_by_id.return_value = mock_assistant
                with patch.object(SkillRepository, "get_by_ids", return_value=[sample_skill, public_skill]):
                    result = SkillService.get_skills_for_assistant("asst-123", other_user)

            # Assert - should only return public_skill (other_user can't access sample_skill)
            assert len(result) == 1
            assert result[0].id == public_skill.id


# =============================================================================
# Singleton Instance Test
# =============================================================================


def test_singleton_instance():
    """Test that skill_service singleton is properly instantiated"""
    from codemie.service.skill_service import skill_service

    assert isinstance(skill_service, SkillService)


# =============================================================================
# Skill Toolkits Tests
# =============================================================================


class TestSkillToolkits:
    """Test toolkits field on skills – create, update, and response"""

    @pytest.fixture
    def toolkit_details(self):
        """Sample ToolKitDetails instance for testing"""
        from codemie.rest_api.models.assistant import ToolDetails, ToolKitDetails

        return ToolKitDetails(toolkit="git", tools=[ToolDetails(name="git_tool")], label="Git")

    def test_create_skill_with_toolkits_passes_to_repository(
        self, mock_repository, owner_user, sample_skill, toolkit_details
    ):
        # Arrange
        request = SkillCreateRequest(
            name="new-skill",
            description="New skill description",
            content="New skill content " * 20,
            project="project-a",
            visibility=SkillVisibility.PRIVATE,
            categories=[],
            toolkits=[toolkit_details],
        )
        mock_repository.get_by_name_author_project.return_value = None
        mock_repository.create.return_value = sample_skill

        # Act
        with patch.object(SkillRepository, "get_by_name_author_project", mock_repository.get_by_name_author_project):
            with patch.object(SkillRepository, "create", mock_repository.create):
                SkillService.create_skill(request, owner_user)

        # Assert
        create_call_args = mock_repository.create.call_args[0][0]
        assert create_call_args["toolkits"] == [toolkit_details]

    def test_create_skill_default_empty_toolkits(self, mock_repository, owner_user, sample_skill):
        # Arrange
        request = SkillCreateRequest(
            name="new-skill",
            description="New skill description",
            content="New skill content " * 20,
            project="project-a",
            visibility=SkillVisibility.PRIVATE,
            categories=[],
        )
        mock_repository.get_by_name_author_project.return_value = None
        mock_repository.create.return_value = sample_skill

        # Act
        with patch.object(SkillRepository, "get_by_name_author_project", mock_repository.get_by_name_author_project):
            with patch.object(SkillRepository, "create", mock_repository.create):
                SkillService.create_skill(request, owner_user)

        # Assert – toolkits defaults to empty list when not supplied
        create_call_args = mock_repository.create.call_args[0][0]
        assert create_call_args["toolkits"] == []

    def test_build_skill_updates_includes_toolkits(self, sample_skill, toolkit_details):
        # Arrange – only toolkits set in request
        request = MagicMock(spec=SkillUpdateRequest)
        request.name = None
        request.description = None
        request.content = None
        request.visibility = None
        request.categories = None
        request.toolkits = [toolkit_details]
        request.mcp_servers = None

        # Act
        updates = SkillService._build_skill_updates(request, sample_skill)

        # Assert
        assert "toolkits" in updates
        assert updates["toolkits"] == [toolkit_details]

    def test_build_skill_updates_excludes_toolkits_when_none(self, sample_skill):
        # Arrange – toolkits not provided in the update
        request = MagicMock(spec=SkillUpdateRequest)
        request.name = None
        request.description = None
        request.content = None
        request.visibility = None
        request.categories = None
        request.toolkits = None
        request.mcp_servers = None

        # Act
        updates = SkillService._build_skill_updates(request, sample_skill)

        # Assert – toolkits key absent when not set
        assert "toolkits" not in updates

    def test_build_skill_updates_includes_mcp_servers(self, sample_skill):
        # Arrange – only mcp_servers set in request
        from codemie.rest_api.models.assistant import MCPServerDetails

        mcp_server = MCPServerDetails(name="my-server")
        request = MagicMock(spec=SkillUpdateRequest)
        request.name = None
        request.description = None
        request.content = None
        request.visibility = None
        request.categories = None
        request.toolkits = None
        request.mcp_servers = [mcp_server]

        # Act
        updates = SkillService._build_skill_updates(request, sample_skill)

        # Assert
        assert "mcp_servers" in updates
        assert updates["mcp_servers"] == [mcp_server]

    def test_build_skill_updates_excludes_mcp_servers_when_none(self, sample_skill):
        # Arrange – mcp_servers not provided in the update
        request = MagicMock(spec=SkillUpdateRequest)
        request.name = None
        request.description = None
        request.content = None
        request.visibility = None
        request.categories = None
        request.toolkits = None
        request.mcp_servers = None

        # Act
        updates = SkillService._build_skill_updates(request, sample_skill)

        # Assert – mcp_servers key absent when not set
        assert "mcp_servers" not in updates

    def test_update_skill_with_toolkits(self, mock_repository, owner_user, sample_skill, toolkit_details):
        # Arrange
        updated_skill = Skill(**sample_skill.model_dump())
        request = SkillUpdateRequest(toolkits=[toolkit_details])
        mock_repository.get_by_id.return_value = sample_skill
        mock_repository.update.return_value = updated_skill
        mock_repository.count_assistants_using_skill.return_value = 0

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with patch.object(SkillRepository, "update", mock_repository.update):
                with patch.object(
                    SkillRepository, "count_assistants_using_skill", mock_repository.count_assistants_using_skill
                ):
                    SkillService.update_skill(sample_skill.id, request, owner_user)

        # Assert
        update_call_args = mock_repository.update.call_args[0][1]
        assert "toolkits" in update_call_args
        assert update_call_args["toolkits"] == [toolkit_details]

    def test_update_skill_empty_toolkits_clears_toolkits(self, mock_repository, owner_user, sample_skill):
        # Arrange – explicitly pass empty list to clear toolkits
        updated_skill = Skill(**sample_skill.model_dump())
        request = SkillUpdateRequest(toolkits=[])
        mock_repository.get_by_id.return_value = sample_skill
        mock_repository.update.return_value = updated_skill
        mock_repository.count_assistants_using_skill.return_value = 0

        # Act
        with patch.object(SkillRepository, "get_by_id", mock_repository.get_by_id):
            with patch.object(SkillRepository, "update", mock_repository.update):
                with patch.object(
                    SkillRepository, "count_assistants_using_skill", mock_repository.count_assistants_using_skill
                ):
                    SkillService.update_skill(sample_skill.id, request, owner_user)

        # Assert
        update_call_args = mock_repository.update.call_args[0][1]
        assert update_call_args["toolkits"] == []

    def test_skill_to_detail_response_includes_toolkits(self, owner_user, toolkit_details):
        # Arrange – skill with a toolkit configured
        skill = Skill(
            id=str(uuid4()),
            name="test-skill",
            description="Test skill description",
            content="# Test Skill\n\nTest content with sufficient length." * 10,
            project="project-a",
            visibility=SkillVisibility.PRIVATE,
            categories=["development"],
            toolkits=[toolkit_details],
            created_by=CreatedByUser(
                id=owner_user.id,
                name=owner_user.name,
                username=owner_user.username,
            ),
            created_date=datetime.now(UTC),
        )

        # Act
        response = skill.to_detail_response(assistants_count=0, user_abilities=["read", "write", "delete"])

        # Assert
        assert len(response.toolkits) == 1
        assert response.toolkits[0].toolkit == "git"

    def test_skill_to_detail_response_empty_toolkits_by_default(self, sample_skill):
        # Act – sample_skill has no toolkits configured
        response = sample_skill.to_detail_response(assistants_count=0, user_abilities=["read"])

        # Assert
        assert response.toolkits == []
