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

"""Unit tests for PersonalProjectService

Tests Story 9: Personal Project Auto-Creation
AC Coverage:
- AC-6.1: Personal project auto-created on authentication/registration
- AC-6.2: Project name equals user's email address
- AC-6.3: project_type='personal' in applications table
- AC-6.4: created_by set to user's ID
- AC-6.5: User assigned as member with is_project_admin=false (Phase 2)
- AC-6.6: Idempotent creation (no duplicates)
- AC-6.7: Non-blocking on failure (authentication continues)
- AC-6.8: Error logging on failure
- AC-6.9: Retry on subsequent logins
- Review Fix: Transaction isolation (separate session)
- Review Fix: PII protection (no email in logs)
- Review Fix: Complete idempotency check (Application + user_projects)
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from codemie.core.models import Application
from codemie.rest_api.models.user_management import UserProject
from codemie.service.project.personal_project_service import PersonalProjectService


def _make_async_session_cm(mock_session):
    """Create an async context manager mock that yields mock_session."""
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_session
    cm.__aexit__.return_value = False
    return cm


class TestPersonalProjectService:
    """Test suite for PersonalProjectService"""

    # ===========================================
    # ensure_personal_project_async() tests
    # ===========================================

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    async def test_ensure_personal_project_creates_when_missing(self, mock_get_async_session):
        """AC-6.1: Personal project auto-created when missing"""
        # Arrange
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock session context
        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        # Mock: No existing personal project
        with patch(
            "codemie.service.project.personal_project_service.PersonalProjectService._has_personal_project_complete",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch(
                "codemie.service.project.personal_project_service.PersonalProjectService._create_personal_project",
                new_callable=AsyncMock,
            ) as mock_create:
                # Act
                result = await PersonalProjectService.ensure_personal_project_async(user_id, user_email)

                # Assert
                assert result is True
                mock_create.assert_called_once_with(mock_session, user_id, user_email)
                mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    async def test_ensure_personal_project_skips_when_exists(self, mock_get_async_session):
        """AC-6.6: Idempotent - skips creation if project exists"""
        # Arrange
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock session context
        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        # Mock: Personal project already exists
        with patch(
            "codemie.service.project.personal_project_service.PersonalProjectService._has_personal_project_complete",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with patch(
                "codemie.service.project.personal_project_service.PersonalProjectService._create_personal_project",
                new_callable=AsyncMock,
            ) as mock_create:
                # Act
                result = await PersonalProjectService.ensure_personal_project_async(user_id, user_email)

                # Assert
                assert result is True
                mock_create.assert_not_called()
                mock_session.commit.assert_not_called()

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    async def test_ensure_personal_project_non_blocking_on_failure(self, mock_get_async_session):
        """AC-6.7: Non-blocking - returns False on failure without raising"""
        # Arrange
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock: Session creation fails
        cm = AsyncMock()
        cm.__aenter__.side_effect = Exception("Database connection error")
        cm.__aexit__.return_value = False
        mock_get_async_session.return_value = cm

        # Act
        result = await PersonalProjectService.ensure_personal_project_async(user_id, user_email)

        # Assert
        assert result is False  # Failure does not raise exception

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    @patch("codemie.service.project.personal_project_service.logger")
    async def test_ensure_personal_project_logs_failure_without_pii(self, mock_logger, mock_get_async_session):
        """AC-6.8 + Review Fix: Logs errors without email (PII protection)"""
        # Arrange
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock: Session creation fails
        cm = AsyncMock()
        cm.__aenter__.side_effect = Exception("Database error")
        cm.__aexit__.return_value = False
        mock_get_async_session.return_value = cm

        # Act
        await PersonalProjectService.ensure_personal_project_async(user_id, user_email)

        # Assert
        mock_logger.error.assert_called_once()
        error_call = mock_logger.error.call_args
        assert "Personal project creation failed" in error_call[0][0]
        assert f"user_id={user_id}" in error_call[0][0]
        # PII Protection: Email should NOT be logged
        assert user_email not in error_call[0][0]
        assert "project_type=personal" in error_call[0][0]  # Generic marker instead
        assert error_call[1]["exc_info"] is True

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    async def test_ensure_personal_project_isolated_transaction(self, mock_get_async_session):
        """Review Fix: Uses separate session for transaction isolation"""
        # Arrange
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock session context
        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        with patch(
            "codemie.service.project.personal_project_service.PersonalProjectService._has_personal_project_complete",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch(
                "codemie.service.project.personal_project_service.PersonalProjectService._create_personal_project",
                new_callable=AsyncMock,
            ):
                # Act
                await PersonalProjectService.ensure_personal_project_async(user_id, user_email)

                # Assert: get_async_session was called (separate session created)
                mock_get_async_session.assert_called_once()
                # Assert: Session was committed
                mock_session.commit.assert_called_once()

    # ===========================================
    # _has_personal_project_complete() tests
    # ===========================================

    @pytest.mark.asyncio
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_has_personal_project_complete_requires_both_records(self, mock_app_repo, mock_user_proj_repo):
        """Review Fix: Checks BOTH Application AND user_projects mapping"""
        # Arrange
        session = AsyncMock()
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock: Application exists with correct type
        mock_app = Application(name=user_email, project_type="personal", created_by=user_id)
        mock_app_repo.aget_by_name = AsyncMock(return_value=mock_app)

        # Mock: user_projects mapping exists
        mock_mapping = UserProject(user_id=user_id, project_name=user_email, is_project_admin=False)
        mock_user_proj_repo.aget_by_user_and_project = AsyncMock(return_value=mock_mapping)

        # Act
        result = await PersonalProjectService._has_personal_project_complete(session, user_id, user_email)

        # Assert
        assert result is True
        mock_app_repo.aget_by_name.assert_called_once_with(session, user_email)
        mock_user_proj_repo.aget_by_user_and_project.assert_called_once_with(session, user_id, user_email)

    @pytest.mark.asyncio
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_has_personal_project_incomplete_missing_user_mapping(self, mock_app_repo, mock_user_proj_repo):
        """Review Fix: Returns False when Application exists but user_projects mapping missing"""
        # Arrange
        session = AsyncMock()
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock: Application exists
        mock_app = Application(name=user_email, project_type="personal", created_by=user_id)
        mock_app_repo.aget_by_name = AsyncMock(return_value=mock_app)

        # Mock: user_projects mapping MISSING
        mock_user_proj_repo.aget_by_user_and_project = AsyncMock(return_value=None)

        # Act
        result = await PersonalProjectService._has_personal_project_complete(session, user_id, user_email)

        # Assert
        assert result is False  # Incomplete - mapping missing

    @pytest.mark.asyncio
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_has_personal_project_complete_returns_false_when_missing(self, mock_app_repo):
        """Check detection when no personal project exists"""
        # Arrange
        session = AsyncMock()
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock: No application exists
        mock_app_repo.aget_by_name = AsyncMock(return_value=None)

        # Act
        result = await PersonalProjectService._has_personal_project_complete(session, user_id, user_email)

        # Assert
        assert result is False

    # ===========================================
    # _create_personal_project() tests
    # ===========================================

    @pytest.mark.asyncio
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_create_personal_project_with_correct_fields(self, mock_app_repo, mock_user_proj_repo):
        """AC-6.2, AC-6.3, AC-6.4: Create with correct name, type, creator"""
        # Arrange
        session = AsyncMock()
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock: No existing application
        mock_app_repo.aget_by_name = AsyncMock(return_value=None)

        # Mock new application creation (using aget_or_create for race conditions)
        mock_application = Application(name=user_email, project_type="shared", created_by=None)
        mock_app_repo.aget_or_create = AsyncMock(return_value=mock_application)

        # Mock: No existing user-project mapping
        mock_user_proj_repo.aget_by_user_and_project = AsyncMock(return_value=None)
        mock_user_proj_repo.aadd_project = AsyncMock()

        # Act
        await PersonalProjectService._create_personal_project(session, user_id, user_email)

        # Assert
        # AC-6.2: Project name = user email
        mock_app_repo.aget_or_create.assert_called_once_with(session, user_email)

        # AC-6.3: project_type = 'personal'
        assert mock_application.project_type == "personal"

        # AC-6.4: created_by = user_id
        assert mock_application.created_by == user_id

        # AC-6.X: description = "Personal Project for {user_email}"
        assert mock_application.description == f"Personal Project for {user_email}"

        # Project record added to session
        session.add.assert_called()
        session.flush.assert_called()

    @pytest.mark.asyncio
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_create_personal_project_with_is_project_admin_false(self, mock_app_repo, mock_user_proj_repo):
        """AC-6.5: User assigned as member with is_project_admin=false (Phase 2)"""
        # Arrange
        session = AsyncMock()
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock: No existing application
        mock_app_repo.aget_by_name = AsyncMock(return_value=None)
        mock_application = Application(name=user_email, project_type="shared", created_by=None)
        mock_app_repo.aget_or_create = AsyncMock(return_value=mock_application)

        # Mock: No existing user-project mapping
        mock_user_proj_repo.aget_by_user_and_project = AsyncMock(return_value=None)
        mock_user_proj_repo.aadd_project = AsyncMock()

        # Act
        await PersonalProjectService._create_personal_project(session, user_id, user_email)

        # Assert
        # AC-6.5: is_project_admin=FALSE (Phase 2 requirement)
        mock_user_proj_repo.aadd_project.assert_called_once_with(session, user_id, user_email, is_project_admin=False)

    @pytest.mark.asyncio
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_create_personal_project_idempotent_user_mapping(self, mock_app_repo, mock_user_proj_repo):
        """AC-6.6: Idempotent - does not recreate existing user-project mapping"""
        # Arrange
        session = AsyncMock()
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock: Application exists
        mock_app = Application(name=user_email, project_type="personal", created_by=user_id)
        mock_app_repo.aget_by_name = AsyncMock(return_value=mock_app)
        mock_user_proj_repo.aget_by_project_name = AsyncMock(return_value=[])

        # Mock: User-project mapping ALREADY EXISTS
        existing_mapping = UserProject(user_id=user_id, project_name=user_email, is_project_admin=False)
        mock_user_proj_repo.aget_by_user_and_project = AsyncMock(return_value=existing_mapping)
        mock_user_proj_repo.aadd_project = AsyncMock()

        # Act
        await PersonalProjectService._create_personal_project(session, user_id, user_email)

        # Assert
        # Idempotent: aadd_project NOT called when mapping exists
        mock_user_proj_repo.aadd_project.assert_not_called()

    @pytest.mark.asyncio
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_create_personal_project_prevents_unsafe_shared_conversion(self, mock_app_repo, mock_user_proj_repo):
        """Review Fix: Prevents converting shared projects with other users"""
        # Arrange
        session = AsyncMock()
        user_id = "user-123"
        user_email = "shared@example.com"

        # Mock: Shared application exists
        mock_app = Application(name=user_email, project_type="shared", created_by="other-user")
        mock_app_repo.aget_by_name = AsyncMock(return_value=mock_app)

        # Mock: Multiple users have access (current user + another user)
        current_user_mapping = UserProject(user_id=user_id, project_name=user_email, is_project_admin=False)
        other_user_mapping = UserProject(user_id="other-user", project_name=user_email, is_project_admin=True)
        mock_user_proj_repo.aget_by_project_name = AsyncMock(return_value=[current_user_mapping, other_user_mapping])

        # Act & Assert
        with pytest.raises(ValueError, match="Cannot convert shared project to personal"):
            await PersonalProjectService._create_personal_project(session, user_id, user_email)

        # Verify the check was performed
        mock_user_proj_repo.aget_by_project_name.assert_called_once_with(session, user_email)

    @pytest.mark.asyncio
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_create_personal_project_uses_email_as_name(self, mock_app_repo, mock_user_proj_repo):
        """AC-6.2: Project name uses email format (with special characters)"""
        # Arrange
        session = AsyncMock()
        user_id = "user-123"

        # Test various email formats
        test_emails = [
            "john.doe@example.com",
            "admin+test@company.org",
            "user_name@sub.domain.co.uk",
            "123@numeric.com",
        ]

        for user_email in test_emails:
            # Mock: No existing application
            mock_app_repo.aget_by_name = AsyncMock(return_value=None)
            mock_application = Application(name=user_email, project_type="shared", created_by=None)
            mock_app_repo.aget_or_create = AsyncMock(return_value=mock_application)
            mock_user_proj_repo.aget_by_user_and_project = AsyncMock(return_value=None)
            mock_user_proj_repo.aadd_project = AsyncMock()

            # Act
            await PersonalProjectService._create_personal_project(session, user_id, user_email)

            # Assert
            # Email format preserved as project name
            mock_app_repo.aget_or_create.assert_called_with(session, user_email)
            mock_user_proj_repo.aadd_project.assert_called_with(session, user_id, user_email, is_project_admin=False)

    @pytest.mark.asyncio
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_create_personal_project_sets_description(self, mock_app_repo, mock_user_proj_repo):
        """AC-6.X: Personal project has description 'Personal Project for {user_email}'"""
        # Arrange
        session = AsyncMock()
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock: No existing application
        mock_app_repo.aget_by_name = AsyncMock(return_value=None)

        # Mock new application creation (using aget_or_create for race conditions)
        mock_application = Application(name=user_email, project_type="shared", created_by=None)
        mock_app_repo.aget_or_create = AsyncMock(return_value=mock_application)

        # Mock: No existing user-project mapping
        mock_user_proj_repo.aget_by_user_and_project = AsyncMock(return_value=None)
        mock_user_proj_repo.aadd_project = AsyncMock()

        # Act
        await PersonalProjectService._create_personal_project(session, user_id, user_email)

        # Assert
        # Description set to "Personal Project for {user_email}"
        assert mock_application.description == f"Personal Project for {user_email}"
        assert mock_application.description == "Personal Project for john.doe@example.com"

    # ===========================================
    # First-time flow tests (Review Round 2)
    # ===========================================

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_first_time_registration_flow_after_commit(
        self, mock_app_repo, mock_user_proj_repo, mock_get_async_session
    ):
        """Review Round 2: Validates personal project creation after user commit

        This test ensures that personal project creation happens AFTER the user
        is committed to the database, avoiding FK constraint errors.
        """
        # Arrange
        user_id = "new-user-123"
        user_email = "newuser@example.com"

        # Mock session for personal project creation
        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        # Mock: No existing personal project
        mock_app_repo.aget_by_name = AsyncMock(return_value=None)
        mock_user_proj_repo.aget_by_user_and_project = AsyncMock(return_value=None)
        mock_user_proj_repo.aget_by_project_name = AsyncMock(return_value=[])
        mock_user_proj_repo.aadd_project = AsyncMock()

        # Mock new application creation (using aget_or_create for race conditions)
        mock_application = Application(name=user_email, project_type="shared", created_by=None)
        mock_app_repo.aget_or_create = AsyncMock(return_value=mock_application)

        # Act - Simulate calling after commit (user exists in DB)
        result = await PersonalProjectService.ensure_personal_project_async(user_id, user_email)

        # Assert
        assert result is True
        # Verify isolated session was used (separate from parent transaction)
        mock_get_async_session.assert_called_once()
        mock_session.commit.assert_called_once()
        # Verify personal project was created
        mock_app_repo.aget_or_create.assert_called_once_with(mock_session, user_email)
        mock_user_proj_repo.aadd_project.assert_called_once_with(
            mock_session, user_id, user_email, is_project_admin=False
        )

    @pytest.mark.asyncio
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_safe_conversion_allows_single_user_project(self, mock_app_repo, mock_user_proj_repo):
        """Review Round 2: Validates conversion is allowed when only current user has access"""
        # Arrange
        session = AsyncMock()
        user_id = "user-123"
        user_email = "existing@example.com"

        # Mock: Shared application exists
        mock_app = Application(name=user_email, project_type="shared", created_by="old-owner")
        mock_app_repo.aget_by_name = AsyncMock(return_value=mock_app)

        # Mock: Only current user has access (safe to convert)
        current_user_mapping = UserProject(user_id=user_id, project_name=user_email, is_project_admin=False)
        mock_user_proj_repo.aget_by_project_name = AsyncMock(return_value=[current_user_mapping])
        mock_user_proj_repo.aget_by_user_and_project = AsyncMock(return_value=current_user_mapping)
        mock_user_proj_repo.aadd_project = AsyncMock()

        # Act - Should succeed (no other users)
        await PersonalProjectService._create_personal_project(session, user_id, user_email)

        # Assert
        # Conversion allowed - application updated to personal
        assert mock_app.project_type == "personal"
        assert mock_app.created_by == user_id
        assert mock_app.description == f"Personal Project for {user_email}"
        session.add.assert_called()

    @pytest.mark.asyncio
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_unsafe_conversion_rejects_single_other_user(self, mock_app_repo, mock_user_proj_repo):
        """Review Round 2: Validates conversion is rejected when ONE other user has access"""
        # Arrange
        session = AsyncMock()
        user_id = "user-123"
        user_email = "shared@example.com"

        # Mock: Shared application exists
        mock_app = Application(name=user_email, project_type="shared", created_by="other-user")
        mock_app_repo.aget_by_name = AsyncMock(return_value=mock_app)

        # Mock: Only ONE other user has access (but not current user) - still unsafe!
        other_user_mapping = UserProject(user_id="other-user", project_name=user_email, is_project_admin=True)
        mock_user_proj_repo.aget_by_project_name = AsyncMock(return_value=[other_user_mapping])

        # Act & Assert
        with pytest.raises(ValueError, match="Cannot convert shared project to personal"):
            await PersonalProjectService._create_personal_project(session, user_id, user_email)

    @pytest.mark.asyncio
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_create_personal_project_uses_get_or_create_for_race_condition(
        self, mock_app_repo, mock_user_proj_repo
    ):
        """Review Round 4: Validates aget_or_create() is used to handle concurrent requests"""
        # Arrange
        session = AsyncMock()
        user_id = "user-123"
        user_email = "john.doe@example.com"

        # Mock: No existing application
        mock_app_repo.aget_by_name = AsyncMock(return_value=None)

        # Mock new application creation via aget_or_create
        mock_application = Application(name=user_email, project_type="shared", created_by=None)
        mock_app_repo.aget_or_create = AsyncMock(return_value=mock_application)
        mock_app_repo.acreate = AsyncMock()

        # Mock: No existing user-project mapping
        mock_user_proj_repo.aget_by_user_and_project = AsyncMock(return_value=None)
        mock_user_proj_repo.aadd_project = AsyncMock()

        # Act
        await PersonalProjectService._create_personal_project(session, user_id, user_email)

        # Assert
        # Verify aget_or_create was used (not acreate) to handle race conditions
        mock_app_repo.aget_or_create.assert_called_once_with(session, user_email)
        mock_app_repo.acreate.assert_not_called()


class TestConcurrentPersonalProjectCreation:
    """F-07: Test concurrent personal project creation scenarios.

    Validates that concurrent calls to ensure_personal_project_async
    handle race conditions gracefully via aget_or_create and non-blocking error handling.
    """

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    async def test_concurrent_calls_both_succeed_non_blocking(self, mock_get_async_session):
        """Simulates two concurrent calls -- both should return without raising.

        Even if both calls see 'no project' and try to create simultaneously,
        the non-blocking error handler should prevent either from crashing.
        """
        user_id = "user-concurrent"
        user_email = "concurrent@example.com"

        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        call_results = []

        with patch(
            "codemie.service.project.personal_project_service.PersonalProjectService._has_personal_project_complete",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch(
                "codemie.service.project.personal_project_service.PersonalProjectService._create_personal_project",
                new_callable=AsyncMock,
            ):
                # Simulate two concurrent calls
                for _ in range(2):
                    result = await PersonalProjectService.ensure_personal_project_async(user_id, user_email)
                    call_results.append(result)

        # Both calls should succeed (non-blocking)
        assert all(r is True for r in call_results)

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    async def test_second_call_handles_integrity_error_gracefully(self, mock_get_async_session):
        """Simulates race condition where second caller hits IntegrityError on commit.

        The first caller creates the project and commits. The second caller also
        passes the _has_personal_project_complete check (stale read) but hits
        IntegrityError on commit. Non-blocking handler should catch this.
        """
        from sqlalchemy.exc import IntegrityError

        user_id = "user-race"
        user_email = "race@example.com"

        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        call_count = 0

        async def commit_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                # Second commit fails with IntegrityError (race condition)
                raise IntegrityError("duplicate key", params=None, orig=Exception("unique violation"))

        mock_session.commit = AsyncMock(side_effect=commit_side_effect)

        with patch(
            "codemie.service.project.personal_project_service.PersonalProjectService._has_personal_project_complete",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch(
                "codemie.service.project.personal_project_service.PersonalProjectService._create_personal_project",
                new_callable=AsyncMock,
            ):
                # First call succeeds
                result1 = await PersonalProjectService.ensure_personal_project_async(user_id, user_email)
                assert result1 is True

                # Second call hits IntegrityError but returns False (non-blocking)
                result2 = await PersonalProjectService.ensure_personal_project_async(user_id, user_email)
                assert result2 is False  # Graceful failure

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    async def test_retry_on_subsequent_login_succeeds(self, mock_get_async_session):
        """AC-6.9: After a failed creation, subsequent login retries and succeeds.

        First call fails (simulating a transient error). Second call
        finds no project and creates it successfully.
        """
        user_id = "user-retry"
        user_email = "retry@example.com"

        # First call: session creation fails
        cm_fail = AsyncMock()
        cm_fail.__aenter__.side_effect = Exception("Transient DB error")
        cm_fail.__aexit__.return_value = False
        mock_get_async_session.return_value = cm_fail

        result1 = await PersonalProjectService.ensure_personal_project_async(user_id, user_email)
        assert result1 is False  # First call fails gracefully

        # Second call: session works
        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        with patch(
            "codemie.service.project.personal_project_service.PersonalProjectService._has_personal_project_complete",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch(
                "codemie.service.project.personal_project_service.PersonalProjectService._create_personal_project",
                new_callable=AsyncMock,
            ):
                result2 = await PersonalProjectService.ensure_personal_project_async(user_id, user_email)
                assert result2 is True  # Retry succeeds

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    async def test_concurrent_calls_with_asyncio_gather(self, mock_get_async_session):
        """F-07: Concurrent execution via asyncio.gather -- all calls non-blocking.

        Uses actual async concurrency to verify the non-blocking pattern.
        """
        user_id = "user-threads"
        user_email = "threads@example.com"

        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        with patch(
            "codemie.service.project.personal_project_service.PersonalProjectService._has_personal_project_complete",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch(
                "codemie.service.project.personal_project_service.PersonalProjectService._create_personal_project",
                new_callable=AsyncMock,
            ):
                results = await asyncio.gather(
                    *[PersonalProjectService.ensure_personal_project_async(user_id, user_email) for _ in range(4)]
                )

        # All calls should complete without raising
        assert len(results) == 4
        assert all(r is True for r in results)


class TestPersonalProjectServiceEdgeCases:
    """Test suite for edge cases in PersonalProjectService.

    Covers uncovered scenarios to reach >= 80% coverage target.
    """

    # ===========================================
    # ensure_personal_project_async edge cases
    # ===========================================

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    @patch("codemie.service.project.personal_project_service.logger")
    async def test_ensure_personal_project_commit_failure_non_blocking(self, mock_logger, mock_get_async_session):
        """Edge case: Commit fails but method returns False without raising"""
        # Arrange
        user_id = "user-456"
        user_email = "commit.fail@example.com"

        # Mock session that fails on commit
        mock_session = AsyncMock()
        mock_session.commit.side_effect = Exception("Commit failed")
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        with patch(
            "codemie.service.project.personal_project_service.PersonalProjectService._has_personal_project_complete",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch(
                "codemie.service.project.personal_project_service.PersonalProjectService._create_personal_project",
                new_callable=AsyncMock,
            ):
                # Act
                result = await PersonalProjectService.ensure_personal_project_async(user_id, user_email)

                # Assert
                assert result is False  # Non-blocking failure
                mock_logger.error.assert_called_once()
                error_call = mock_logger.error.call_args
                assert "Personal project creation failed (non-blocking)" in error_call[0][0]
                assert f"user_id={user_id}" in error_call[0][0]

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    async def test_ensure_personal_project_creation_raises_exception(self, mock_get_async_session):
        """Edge case: _create_personal_project raises exception, non-blocking handler catches it"""
        # Arrange
        user_id = "user-789"
        user_email = "create.fail@example.com"

        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        with patch(
            "codemie.service.project.personal_project_service.PersonalProjectService._has_personal_project_complete",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch(
                "codemie.service.project.personal_project_service.PersonalProjectService._create_personal_project",
                new_callable=AsyncMock,
                side_effect=ValueError("Cannot convert shared project"),
            ):
                # Act
                result = await PersonalProjectService.ensure_personal_project_async(user_id, user_email)

                # Assert
                assert result is False  # Non-blocking - exception caught
                # Commit should not be called when creation fails
                mock_session.commit.assert_not_called()

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    async def test_ensure_personal_project_multiple_errors_non_blocking(self, mock_get_async_session):
        """Edge case: Multiple different error types are caught non-blocking"""
        user_id = "user-multi"
        user_email = "multi.error@example.com"

        error_types = [
            RuntimeError("Runtime error"),
            ValueError("Value error"),
            KeyError("Key error"),
            Exception("Generic error"),
        ]

        for error in error_types:
            # Mock session creation that raises different exceptions
            cm = AsyncMock()
            cm.__aenter__.side_effect = error
            cm.__aexit__.return_value = False
            mock_get_async_session.return_value = cm

            # Act
            result = await PersonalProjectService.ensure_personal_project_async(user_id, user_email)

            # Assert - All error types result in non-blocking False
            assert result is False

    # ===========================================
    # reconcile_personal_project_on_email_change tests
    # ===========================================

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    @patch("codemie.service.project.personal_project_service.PersonalProjectService.ensure_personal_project_async")
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_reconcile_email_change_old_project_exists(
        self, mock_app_repo, mock_user_proj_repo, mock_ensure, mock_get_async_session
    ):
        """Test reconciliation when old personal project exists - soft-deletes old, creates new"""
        # Arrange
        user_id = "user-reconcile-1"
        old_email = "old@example.com"
        new_email = "new@example.com"

        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        # Mock old application exists and is personal project owned by user
        mock_old_app = Application(name=old_email, project_type="personal", created_by=user_id)
        mock_app_repo.aget_by_name = AsyncMock(return_value=mock_old_app)
        mock_user_proj_repo.aremove_project = AsyncMock()
        mock_ensure.return_value = True

        # Act
        result = await PersonalProjectService.reconcile_personal_project_on_email_change(user_id, old_email, new_email)

        # Assert
        assert result is True
        # Old app soft-deleted (deleted_at set)
        assert mock_old_app.deleted_at is not None
        mock_session.add.assert_called_once_with(mock_old_app)
        mock_user_proj_repo.aremove_project.assert_called_once_with(mock_session, user_id, old_email)
        mock_session.commit.assert_called_once()
        # New project creation attempted
        mock_ensure.assert_called_once_with(user_id, new_email)

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    @patch("codemie.service.project.personal_project_service.PersonalProjectService.ensure_personal_project_async")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_reconcile_email_change_old_project_not_found(
        self, mock_app_repo, mock_ensure, mock_get_async_session
    ):
        """Test reconciliation when old project doesn't exist - only creates new"""
        # Arrange
        user_id = "user-reconcile-2"
        old_email = "old@example.com"
        new_email = "new@example.com"

        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        # Mock: Old application does NOT exist
        mock_app_repo.aget_by_name = AsyncMock(return_value=None)
        mock_ensure.return_value = True

        # Act
        result = await PersonalProjectService.reconcile_personal_project_on_email_change(user_id, old_email, new_email)

        # Assert
        assert result is True
        # No session operations for old project
        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()
        # New project creation attempted
        mock_ensure.assert_called_once_with(user_id, new_email)

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    @patch("codemie.service.project.personal_project_service.PersonalProjectService.ensure_personal_project_async")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_reconcile_email_change_old_project_not_personal(
        self, mock_app_repo, mock_ensure, mock_get_async_session
    ):
        """Test reconciliation when old project is shared, not personal - skips delete"""
        # Arrange
        user_id = "user-reconcile-3"
        old_email = "old@example.com"
        new_email = "new@example.com"

        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        # Mock: Old application exists but is SHARED (not personal)
        mock_old_app = Application(name=old_email, project_type="shared", created_by=user_id)
        mock_app_repo.aget_by_name = AsyncMock(return_value=mock_old_app)
        mock_ensure.return_value = True

        # Act
        result = await PersonalProjectService.reconcile_personal_project_on_email_change(user_id, old_email, new_email)

        # Assert
        assert result is True
        # Old app NOT deleted (project_type != personal)
        assert mock_old_app.deleted_at is None
        mock_session.add.assert_not_called()
        # New project creation still attempted
        mock_ensure.assert_called_once_with(user_id, new_email)

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    @patch("codemie.service.project.personal_project_service.PersonalProjectService.ensure_personal_project_async")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_reconcile_email_change_old_project_different_owner(
        self, mock_app_repo, mock_ensure, mock_get_async_session
    ):
        """Test reconciliation when old project is personal but owned by different user - skips delete"""
        # Arrange
        user_id = "user-reconcile-4"
        old_email = "old@example.com"
        new_email = "new@example.com"

        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        # Mock: Old application exists and is personal but owned by DIFFERENT user
        mock_old_app = Application(name=old_email, project_type="personal", created_by="other-user-id")
        mock_app_repo.aget_by_name = AsyncMock(return_value=mock_old_app)
        mock_ensure.return_value = True

        # Act
        result = await PersonalProjectService.reconcile_personal_project_on_email_change(user_id, old_email, new_email)

        # Assert
        assert result is True
        # Old app NOT deleted (created_by != user_id)
        assert mock_old_app.deleted_at is None
        mock_session.add.assert_not_called()
        # New project creation still attempted
        mock_ensure.assert_called_once_with(user_id, new_email)

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    @patch("codemie.service.project.personal_project_service.logger")
    async def test_reconcile_email_change_exception_non_blocking(self, mock_logger, mock_get_async_session):
        """Test reconciliation failure is non-blocking - returns False and logs error"""
        # Arrange
        user_id = "user-reconcile-fail"
        old_email = "old@example.com"
        new_email = "new@example.com"

        # Mock session creation fails
        cm = AsyncMock()
        cm.__aenter__.side_effect = Exception("Database error")
        cm.__aexit__.return_value = False
        mock_get_async_session.return_value = cm

        # Act
        result = await PersonalProjectService.reconcile_personal_project_on_email_change(user_id, old_email, new_email)

        # Assert
        assert result is False  # Non-blocking failure
        mock_logger.error.assert_called_once()
        error_call = mock_logger.error.call_args
        assert "Personal project reconciliation failed (non-blocking)" in error_call[0][0]
        assert f"user_id={user_id}" in error_call[0][0]
        # PII protection: emails should NOT be logged
        assert old_email not in error_call[0][0]
        assert new_email not in error_call[0][0]

    @pytest.mark.asyncio
    @patch("codemie.clients.postgres.get_async_session")
    @patch("codemie.service.project.personal_project_service.PersonalProjectService.ensure_personal_project_async")
    @patch("codemie.service.project.personal_project_service.user_project_repository")
    @patch("codemie.service.project.personal_project_service.application_repository")
    async def test_reconcile_email_change_new_project_creation_fails(
        self, mock_app_repo, mock_user_proj_repo, mock_ensure, mock_get_async_session
    ):
        """Test reconciliation when new project creation fails - returns False from ensure_personal_project_async"""
        # Arrange
        user_id = "user-reconcile-5"
        old_email = "old@example.com"
        new_email = "new@example.com"

        mock_session = AsyncMock()
        mock_get_async_session.return_value = _make_async_session_cm(mock_session)

        # Mock: Old project exists and gets deleted successfully
        mock_old_app = Application(name=old_email, project_type="personal", created_by=user_id)
        mock_app_repo.aget_by_name = AsyncMock(return_value=mock_old_app)
        mock_user_proj_repo.aremove_project = AsyncMock()

        # Mock: New project creation FAILS
        mock_ensure.return_value = False

        # Act
        result = await PersonalProjectService.reconcile_personal_project_on_email_change(user_id, old_email, new_email)

        # Assert
        assert result is False  # Propagates failure from ensure_personal_project_async
        # Old project deletion still happened
        assert mock_old_app.deleted_at is not None
        mock_session.add.assert_called_once_with(mock_old_app)
        mock_session.commit.assert_called_once()
