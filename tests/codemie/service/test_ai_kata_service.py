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

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status

from codemie.configs import config  # noqa: F401 - used in patch.object calls
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.ai_kata import (
    AIKata,
    AIKataRequest,
    KataLevel,
    KataLink,
    KataStatus,
)
from codemie.rest_api.models.user_kata_progress import KataProgressStatus, UserKataProgress
from codemie.rest_api.security.user import User
from codemie.service.ai_kata_service import AIKataService, KataConstants
from codemie.service.permission.permission_exceptions import PermissionAccessDenied


@pytest.fixture
def mock_repository():
    """Mock kata repository."""
    from codemie.repository.ai_kata_repository import AIKataRepository

    return MagicMock(spec=AIKataRepository)


@pytest.fixture
def mock_progress_repository():
    """Mock progress repository."""
    from codemie.repository.user_kata_progress_repository import UserKataProgressRepository

    return MagicMock(spec=UserKataProgressRepository)


@pytest.fixture
def mock_interaction_repository():
    """Mock interaction repository."""
    from codemie.repository.kata_user_interaction_repository import KataUserInteractionRepository

    return MagicMock(spec=KataUserInteractionRepository)


@pytest.fixture
def kata_service(mock_repository, mock_progress_repository, mock_interaction_repository):
    """Kata service with mocked repositories."""
    # Use model_construct with explicit repository values to avoid default_factory initialization
    service = AIKataService.model_construct(
        repository=mock_repository,
        progress_repository=mock_progress_repository,
        interaction_repository=mock_interaction_repository,
    )
    return service


@pytest.fixture
def admin_user():
    """Admin user fixture."""
    return User(id="admin123", username="admin", name="Admin User", roles=["admin"])


@pytest.fixture
def regular_user():
    """Regular user fixture."""
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(id="user123", username="user", name="Regular User", is_admin=False)


@pytest.fixture
def sample_kata_request():
    """Sample kata request."""
    return AIKataRequest(
        title="Test Kata",
        description="Test kata description",
        steps="# Step 1\nContent 1\n\n# Step 2\nContent 2",
        level=KataLevel.BEGINNER,
        duration_minutes=30,
        tags=["python", "testing"],
        roles=["developer"],
        links=[KataLink(title="Docs", url="https://example.com", type="documentation")],
        references=["Reference 1"],
        image_url="https://example.com/image.png",
    )


@pytest.fixture
def sample_kata():
    """Sample kata entity."""
    return AIKata(
        id="kata123",
        title="Test Kata",
        description="Test kata description",
        steps="# Step 1\nContent 1\n\n# Step 2\nContent 2",
        level=KataLevel.BEGINNER,
        creator_id="admin123",
        creator_name="Admin User",
        creator_username="admin",
        duration_minutes=30,
        tags=["python", "testing"],
        roles=["developer"],
        links=[KataLink(title="Docs", url="https://example.com", type="documentation")],
        references=["Reference 1"],
        status=KataStatus.PUBLISHED,
        image_url="https://example.com/image.png",
        enrollment_count=10,
        date=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        update_date=datetime(2024, 1, 2, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_user_progress():
    """Sample user progress."""
    return UserKataProgress(
        id="progress123",
        user_id="user123",
        kata_id="kata123",
        status=KataProgressStatus.IN_PROGRESS,
        started_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
    )


# create_kata tests


def test_create_kata_success(kata_service, admin_user, sample_kata_request, mock_repository):
    """Test successful kata creation by admin."""
    mock_repository.create.return_value = "kata456"

    with (
        patch("codemie.service.ai_kata_service.get_valid_kata_tag_ids", return_value=["python", "testing"]),
        patch("codemie.service.ai_kata_service.get_valid_kata_role_ids", return_value=["developer"]),
    ):
        kata_id = kata_service.create_kata(sample_kata_request, admin_user)

    assert kata_id == "kata456"
    mock_repository.create.assert_called_once()
    created_kata = mock_repository.create.call_args[0][0]
    assert created_kata.title == "Test Kata"
    assert created_kata.status == KataStatus.DRAFT


def test_create_kata_permission_denied(kata_service, regular_user, sample_kata_request):
    """Test kata creation fails for non-admin user."""
    with pytest.raises(PermissionAccessDenied, match=".*administrators.*"):
        kata_service.create_kata(sample_kata_request, regular_user)


def test_create_kata_validation_error_whitespace_title(kata_service, admin_user):
    """Test kata creation with whitespace-only title."""
    invalid_request = AIKataRequest(
        title="   ",  # Whitespace only
        description="Valid description",
        steps="# Step 1\nContent",
        level=KataLevel.BEGINNER,
        duration_minutes=30,
        tags=[],
        roles=[],
    )

    with pytest.raises(ExtendedHTTPException) as exc_info:
        kata_service.create_kata(invalid_request, admin_user)

    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "title" in exc_info.value.details.lower()


def test_create_kata_validation_error_invalid_tags(kata_service, admin_user):
    """Test kata creation with invalid tags."""
    invalid_request = AIKataRequest(
        title="Valid Title",
        description="Valid description",
        steps="# Step 1\nContent",
        level=KataLevel.BEGINNER,
        duration_minutes=30,
        tags=["invalid_tag"],
        roles=[],
    )

    with patch("codemie.service.ai_kata_service.get_valid_kata_tag_ids", return_value=["python", "testing"]):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            kata_service.create_kata(invalid_request, admin_user)

        assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
        assert "tag" in exc_info.value.details.lower()


# get_kata tests


def test_get_kata_success_with_progress(
    kata_service,
    sample_kata,
    sample_user_progress,
    mock_repository,
    mock_progress_repository,
    mock_interaction_repository,
):
    """Test get kata with user progress."""
    mock_repository.get_by_id.return_value = sample_kata
    mock_progress_repository.get_user_progress.return_value = sample_user_progress
    mock_interaction_repository.get_by_kata_and_user.return_value = None  # No reaction

    result = kata_service.get_kata("kata123", user_id="user123", is_admin=False)

    assert result is not None
    assert result.id == "kata123"
    assert result.title == "Test Kata"
    assert result.user_progress.status == KataProgressStatus.IN_PROGRESS
    assert result.user_progress.user_reaction is None
    assert result.enrollment_count == 10
    # Enrolled user should see full content
    assert result.links is not None
    assert result.references is not None


def test_get_kata_success_without_progress(
    kata_service, sample_kata, mock_repository, mock_progress_repository, mock_interaction_repository
):
    """Test get kata without user progress."""
    mock_repository.get_by_id.return_value = sample_kata
    mock_progress_repository.get_user_progress.return_value = None
    mock_interaction_repository.get_by_kata_and_user.return_value = None  # No reaction

    result = kata_service.get_kata("kata123", user_id="user123", is_admin=False)

    assert result is not None
    assert result.user_progress.status == KataProgressStatus.NOT_STARTED
    assert result.user_progress.user_reaction is None
    # Not enrolled user should NOT see links/references
    assert result.links is None
    assert result.references is None


def test_get_kata_not_found(kata_service, mock_repository):
    """Test get kata when kata doesn't exist."""
    mock_repository.get_by_id.return_value = None

    result = kata_service.get_kata("nonexistent", user_id="user123")

    assert result is None


def test_get_kata_admin_sees_full_content(
    kata_service, sample_kata, mock_repository, mock_progress_repository, mock_interaction_repository
):
    """Test admin sees full content even without enrollment."""
    mock_repository.get_by_id.return_value = sample_kata
    mock_progress_repository.get_user_progress.return_value = None
    mock_interaction_repository.get_by_kata_and_user.return_value = None  # No reaction

    result = kata_service.get_kata("kata123", user_id="admin123", is_admin=True)

    assert result is not None
    # Admin should see everything
    assert result.links is not None
    assert result.references is not None


# list_katas tests


def test_list_katas_success(
    kata_service, sample_kata, mock_repository, mock_progress_repository, mock_interaction_repository
):
    """Test successful kata listing."""
    mock_repository.list_with_filters.return_value = ([sample_kata], 1)
    mock_progress_repository.bulk_get_user_progress.return_value = {}
    mock_interaction_repository.bulk_get_user_reactions.return_value = {}

    result = kata_service.list_katas(page=1, per_page=20, user_id="user123")

    assert result.pagination.total == 1
    assert result.pagination.page == 1
    assert len(result.data) == 1
    assert result.data[0].id == "kata123"


def test_list_katas_with_progress(
    kata_service,
    sample_kata,
    sample_user_progress,
    mock_repository,
    mock_progress_repository,
    mock_interaction_repository,
):
    """Test kata listing with user progress."""
    mock_repository.list_with_filters.return_value = ([sample_kata], 1)
    mock_progress_repository.bulk_get_user_progress.return_value = {"kata123": sample_user_progress}
    mock_interaction_repository.bulk_get_user_reactions.return_value = {}

    result = kata_service.list_katas(page=1, per_page=20, user_id="user123")

    assert len(result.data) == 1
    assert result.data[0].user_progress.status == KataProgressStatus.IN_PROGRESS


def test_list_katas_pagination(
    kata_service, sample_kata, mock_repository, mock_progress_repository, mock_interaction_repository
):
    """Test pagination calculation."""
    mock_repository.list_with_filters.return_value = ([sample_kata], 50)
    mock_progress_repository.bulk_get_user_progress.return_value = {}
    mock_interaction_repository.bulk_get_user_reactions.return_value = {}

    result = kata_service.list_katas(page=1, per_page=20, user_id="user123")

    assert result.pagination.total == 50
    assert result.pagination.pages == 3  # 50 items / 20 per page = 3 pages


def test_list_katas_invalid_page(kata_service, mock_repository, mock_progress_repository, mock_interaction_repository):
    """Test list with invalid page number."""
    mock_repository.list_with_filters.return_value = ([], 0)
    mock_progress_repository.bulk_get_user_progress.return_value = {}
    mock_interaction_repository.bulk_get_user_reactions.return_value = {}

    kata_service.list_katas(page=-1, per_page=20, user_id="user123")

    # Should default to page 1
    mock_repository.list_with_filters.assert_called_once()
    call_kwargs = mock_repository.list_with_filters.call_args[1]
    assert call_kwargs["page"] == 1


def test_list_katas_invalid_per_page(
    kata_service, mock_repository, mock_progress_repository, mock_interaction_repository
):
    """Test list with invalid per_page."""
    mock_repository.list_with_filters.return_value = ([], 0)
    mock_progress_repository.bulk_get_user_progress.return_value = {}
    mock_interaction_repository.bulk_get_user_reactions.return_value = {}

    kata_service.list_katas(page=1, per_page=200, user_id="user123")

    # Should default to 20
    mock_repository.list_with_filters.assert_called_once()
    call_kwargs = mock_repository.list_with_filters.call_args[1]
    assert call_kwargs["per_page"] == 20


# update_kata tests


def test_update_kata_success(kata_service, admin_user, sample_kata_request, sample_kata, mock_repository):
    """Test successful kata update by admin."""
    mock_repository.get_by_id.return_value = sample_kata
    mock_repository.update.return_value = True

    with (
        patch("codemie.service.ai_kata_service.get_valid_kata_tag_ids", return_value=["python", "testing"]),
        patch("codemie.service.ai_kata_service.get_valid_kata_role_ids", return_value=["developer"]),
    ):
        result = kata_service.update_kata("kata123", sample_kata_request, admin_user)

    assert result is True
    mock_repository.update.assert_called_once()


def test_update_kata_permission_denied(kata_service, regular_user, sample_kata_request):
    """Test kata update fails for non-admin user."""
    with pytest.raises(PermissionAccessDenied, match=".*administrators.*"):
        kata_service.update_kata("kata123", sample_kata_request, regular_user)


def test_update_kata_not_found(kata_service, admin_user, sample_kata_request, mock_repository):
    """Test update non-existent kata."""
    mock_repository.get_by_id.return_value = None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        kata_service.update_kata("nonexistent", sample_kata_request, admin_user)

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND


def test_update_kata_validation_error(kata_service, admin_user, sample_kata, mock_repository):
    """Test update with invalid data."""
    mock_repository.get_by_id.return_value = sample_kata

    invalid_request = AIKataRequest(
        title="   ",  # Whitespace only
        description="Valid description",
        steps="# Step 1\nContent",
        level=KataLevel.BEGINNER,
        duration_minutes=30,
        tags=[],
        roles=[],
    )

    with pytest.raises(ExtendedHTTPException) as exc_info:
        kata_service.update_kata("kata123", invalid_request, admin_user)

    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST


# publish_kata tests


def test_publish_kata_success(kata_service, admin_user, sample_kata, mock_repository):
    """Test successful kata publish by admin."""
    mock_repository.get_by_id.return_value = sample_kata
    mock_repository.publish.return_value = True

    result = kata_service.publish_kata("kata123", admin_user)

    assert result is True
    mock_repository.publish.assert_called_once_with("kata123")


def test_publish_kata_permission_denied(kata_service, regular_user):
    """Test kata publish fails for non-admin user."""
    with pytest.raises(PermissionAccessDenied, match=".*administrators.*"):
        kata_service.publish_kata("kata123", regular_user)


def test_publish_kata_not_found(kata_service, admin_user, mock_repository):
    """Test publish non-existent kata."""
    mock_repository.get_by_id.return_value = None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        kata_service.publish_kata("nonexistent", admin_user)

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND


# archive_kata tests


def test_archive_kata_success(kata_service, admin_user, sample_kata, mock_repository):
    """Test successful kata archive by admin."""
    mock_repository.get_by_id.return_value = sample_kata
    mock_repository.archive.return_value = True

    result = kata_service.archive_kata("kata123", admin_user)

    assert result is True
    mock_repository.archive.assert_called_once_with("kata123")


def test_archive_kata_permission_denied(kata_service, regular_user):
    """Test kata archive fails for non-admin user."""
    with pytest.raises(PermissionAccessDenied, match=".*administrators.*"):
        kata_service.archive_kata("kata123", regular_user)


def test_archive_kata_not_found(kata_service, admin_user, mock_repository):
    """Test archive non-existent kata."""
    mock_repository.get_by_id.return_value = None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        kata_service.archive_kata("nonexistent", admin_user)

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND


# delete_kata tests


def test_delete_kata_success(kata_service, admin_user, sample_kata, mock_repository):
    """Test successful kata delete by admin."""
    mock_repository.get_by_id.return_value = sample_kata
    mock_repository.delete.return_value = True

    result = kata_service.delete_kata("kata123", admin_user)

    assert result is True
    mock_repository.delete.assert_called_once_with("kata123")


def test_delete_kata_permission_denied(kata_service, regular_user):
    """Test kata delete fails for non-admin user."""
    with pytest.raises(PermissionAccessDenied, match=".*administrators.*"):
        kata_service.delete_kata("kata123", regular_user)


def test_delete_kata_not_found(kata_service, admin_user, mock_repository):
    """Test delete non-existent kata."""
    mock_repository.get_by_id.return_value = None

    with pytest.raises(ExtendedHTTPException) as exc_info:
        kata_service.delete_kata("nonexistent", admin_user)

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND


# validate_kata_content tests


def test_validate_kata_content_valid(kata_service, sample_kata_request):
    """Test validation with valid kata."""
    with (
        patch("codemie.service.ai_kata_service.get_valid_kata_tag_ids", return_value=["python", "testing"]),
        patch("codemie.service.ai_kata_service.get_valid_kata_role_ids", return_value=["developer"]),
    ):
        error = kata_service.validate_kata_content(sample_kata_request)

    assert error == ""


def test_validate_kata_content_empty_title(kata_service):
    """Test validation with empty title."""
    invalid_request = AIKataRequest(
        title="   ",
        description="Valid description",
        steps="# Step 1\nContent",
        level=KataLevel.BEGINNER,
        duration_minutes=30,
        tags=[],
        roles=[],
    )

    error = kata_service.validate_kata_content(invalid_request)

    assert "title" in error.lower()


def test_validate_kata_content_whitespace_description(kata_service):
    """Test validation with whitespace-only description."""
    invalid_request = AIKataRequest(
        title="Valid Title",
        description="   ",  # Whitespace only
        steps="# Step 1\nContent",
        level=KataLevel.BEGINNER,
        duration_minutes=30,
        tags=[],
        roles=[],
    )

    error = kata_service.validate_kata_content(invalid_request)

    assert "description" in error.lower()


def test_validate_kata_content_whitespace_steps(kata_service):
    """Test validation with whitespace-only steps."""
    invalid_request = AIKataRequest(
        title="Valid Title",
        description="Valid description",
        steps="   ",  # Whitespace only
        level=KataLevel.BEGINNER,
        duration_minutes=30,
        tags=[],
        roles=[],
    )

    error = kata_service.validate_kata_content(invalid_request)

    assert "steps" in error.lower()


def test_validate_kata_content_invalid_tag(kata_service):
    """Test validation with invalid tag."""
    invalid_request = AIKataRequest(
        title="Valid Title",
        description="Valid description",
        steps="# Step 1\nContent",
        level=KataLevel.BEGINNER,
        duration_minutes=30,
        tags=["invalid_tag"],
        roles=[],
    )

    with patch("codemie.service.ai_kata_service.get_valid_kata_tag_ids", return_value=["python", "testing"]):
        error = kata_service.validate_kata_content(invalid_request)

    assert "invalid" in error.lower()
    assert "tag" in error.lower()


def test_validate_kata_content_invalid_role(kata_service):
    """Test validation with invalid role."""
    invalid_request = AIKataRequest(
        title="Valid Title",
        description="Valid description",
        steps="# Step 1\nContent",
        level=KataLevel.BEGINNER,
        duration_minutes=30,
        tags=[],
        roles=["invalid_role"],
    )

    with patch("codemie.service.ai_kata_service.get_valid_kata_role_ids", return_value=["developer"]):
        error = kata_service.validate_kata_content(invalid_request)

    assert "invalid" in error.lower()
    assert "role" in error.lower()


def test_validate_kata_content_invalid_link_title(kata_service):
    """Test validation with invalid link title."""
    invalid_request = AIKataRequest(
        title="Valid Title",
        description="Valid description",
        steps="# Step 1\nContent",
        level=KataLevel.BEGINNER,
        duration_minutes=30,
        tags=[],
        roles=[],
        links=[KataLink(title="", url="https://example.com", type="docs")],
    )

    error = kata_service.validate_kata_content(invalid_request)

    assert "link" in error.lower()
    assert "title" in error.lower()


def test_validate_kata_content_invalid_link_url(kata_service):
    """Test validation with invalid link URL."""
    invalid_request = AIKataRequest(
        title="Valid Title",
        description="Valid description",
        steps="# Step 1\nContent",
        level=KataLevel.BEGINNER,
        duration_minutes=30,
        tags=[],
        roles=[],
        links=[KataLink(title="Docs", url="", type="docs")],
    )

    error = kata_service.validate_kata_content(invalid_request)

    assert "link" in error.lower()
    assert "url" in error.lower()


# filter_steps_for_user tests


def test_filter_steps_enrolled_user(kata_service):
    """Test filter returns full steps for enrolled user."""
    steps = "# Step 1\nContent 1\n\n# Step 2\nContent 2\n\n# Step 3\nContent 3"

    result = kata_service.filter_steps_for_user(steps, is_enrolled=True)

    assert result == steps


def test_filter_steps_not_enrolled_user(kata_service):
    """Test filter returns preview for non-enrolled user."""
    steps = "# Step 1\nContent 1\n\n# Step 2\nContent 2\n\n# Step 3\nContent 3\n\n# Step 4\nContent 4"

    result = kata_service.filter_steps_for_user(steps, is_enrolled=False)

    assert "# Step 1" in result
    assert "# Step 2" in result
    assert "# Step 3" in result
    assert "# Step 4" not in result
    assert "Enroll to see remaining steps" in result


def test_filter_steps_few_steps(kata_service):
    """Test filter with fewer steps than preview count."""
    steps = "# Step 1\nContent 1\n\n# Step 2\nContent 2"

    result = kata_service.filter_steps_for_user(steps, is_enrolled=False)

    assert "# Step 1" in result
    assert "# Step 2" in result
    assert "Enroll" in result


def test_filter_steps_no_structure(kata_service):
    """Test filter with no clear step structure."""
    steps = "This is just plain text without step headers. " * 50

    result = kata_service.filter_steps_for_user(steps, is_enrolled=False)

    assert len(result) <= KataConstants.PREVIEW_TEXT_LENGTH + 100  # +100 for message
    assert "Enroll" in result


def test_filter_steps_various_header_formats(kata_service):
    """Test filter recognizes various step header formats."""
    steps = "# Step 1\nContent\n\n## Step 2\nContent\n\n### Step 3\nContent\n\n#### Step 4\nContent"

    result = kata_service.filter_steps_for_user(steps, is_enrolled=False)

    assert "# Step 1" in result
    assert "## Step 2" in result
    assert "### Step 3" in result
    assert "#### Step 4" not in result
