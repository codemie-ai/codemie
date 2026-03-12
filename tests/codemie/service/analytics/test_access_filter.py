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

"""Unit tests for AccessFilter - role-based access control for analytics queries.

This is a CRITICAL SECURITY component that determines which projects users can access.
Tests ensure proper enforcement of access boundaries between plain users and project admins.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codemie.rest_api.security.user import User
from codemie.service.analytics.access_filter import AccessFilter, ProjectAccessContext


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def mock_plain_user() -> User:
    """Create a mock plain user with only regular applications."""
    user = MagicMock(spec=User)
    user.id = "test-user-plain"
    user.project_names = ["project-a", "project-b"]
    user.admin_project_names = None
    user.is_admin = False
    return user


@pytest.fixture
def mock_admin_user() -> User:
    """Create a mock project admin with both applications and admin applications."""
    user = MagicMock(spec=User)
    user.id = "test-user-admin"
    user.project_names = ["project-a", "project-b"]
    user.admin_project_names = ["project-c", "project-d"]
    user.is_admin = False
    return user


@pytest.fixture
def mock_mixed_user() -> User:
    """User with both plain and admin roles."""
    user = MagicMock(spec=User)
    user.id = "test-user-mixed"
    user.project_names = ["project-a", "project-b"]
    user.admin_project_names = ["project-c", "project-d"]
    user.is_admin = False
    return user


# ============================================================================
# TEST CLASS: AccessFilter
# ============================================================================


class TestAccessFilter:
    """Test suite for AccessFilter - role-based access control logic."""

    # ========================================================================
    # 1. PLAIN USER ACCESS TESTS
    # ========================================================================

    def test_get_accessible_projects_plain_user_with_applications(self):
        """Verify plain users can only access projects in their applications list.

        CRITICAL: Plain users should NOT see projects outside their applications.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-1"
        user.project_names = ["project-a", "project-b"]
        user.admin_project_names = None
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert set(projects) == {"project-a", "project-b"}
        assert len(projects) == 2

    def test_get_accessible_projects_plain_user_with_empty_applications(self):
        """Verify plain users with no applications get empty list.

        CRITICAL: Users with empty applications should have NO access.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-2"
        user.project_names = []
        user.admin_project_names = None
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert projects == []

    def test_get_accessible_projects_plain_user_with_none_applications(self):
        """Verify handling when applications is None.

        CRITICAL: None should be treated as no access, not as error.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-3"
        user.project_names = None
        user.admin_project_names = None
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert projects == []

    # ========================================================================
    # 2. PROJECT ADMIN ACCESS TESTS
    # ========================================================================

    def test_get_accessible_projects_admin_combines_applications_and_admin(self):
        """Verify project admins see both regular and admin projects.

        CRITICAL: Admins need visibility into their admin projects for management.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-4"
        user.project_names = ["project-a", "project-b"]
        user.admin_project_names = ["project-c", "project-d"]
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert set(projects) == {"project-a", "project-b", "project-c", "project-d"}
        assert len(projects) == 4

    def test_get_accessible_projects_admin_deduplicates_overlapping_projects(self):
        """Verify duplicate projects are removed when user is both member and admin.

        CRITICAL: Prevents duplicate data in queries and correct filtering logic.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-5"
        user.project_names = ["project-a", "project-b"]
        user.admin_project_names = ["project-b", "project-c"]  # project-b overlaps
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert set(projects) == {"project-a", "project-b", "project-c"}
        assert len(projects) == 3  # Deduplication worked
        assert projects.count("project-b") == 1  # project-b appears only once

    def test_get_accessible_projects_admin_only_applications_admin(self):
        """Verify admins with only admin projects (no regular applications).

        Use case: Pure admin users who manage projects but aren't members.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-6"
        user.project_names = []
        user.admin_project_names = ["admin-project-1", "admin-project-2"]
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert set(projects) == {"admin-project-1", "admin-project-2"}
        assert len(projects) == 2

    def test_get_accessible_projects_admin_with_none_applications(self):
        """Verify handling when applications is None but applications_admin has values.

        CRITICAL: Ensures None applications doesn't break admin project access.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-7"
        user.project_names = None
        user.admin_project_names = ["admin-project"]
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert set(projects) == {"admin-project"}
        assert len(projects) == 1

    # ========================================================================
    # 3. EDGE CASES
    # ========================================================================

    def test_get_accessible_projects_both_none(self):
        """Verify behavior when both applications and applications_admin are None.

        CRITICAL: Should return empty list, not raise error or return all projects.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-8"
        user.project_names = None
        user.admin_project_names = None
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert projects == []

    def test_get_accessible_projects_both_empty(self):
        """Verify behavior when both are empty lists.

        CRITICAL: Should return empty list, no access granted.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-9"
        user.project_names = []
        user.admin_project_names = []
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert projects == []

    def test_get_accessible_projects_applications_admin_is_none_explicitly(self):
        """Verify handling when applications_admin is explicitly None (not just falsy).

        Edge case: Ensure None is handled same as empty list for applications_admin.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-10"
        user.project_names = ["project-x"]
        user.admin_project_names = None
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert set(projects) == {"project-x"}
        assert len(projects) == 1

    def test_get_accessible_projects_applications_admin_is_empty_list(self):
        """Verify handling when applications_admin is empty list.

        Edge case: Ensure empty list is handled correctly for applications_admin.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-11"
        user.project_names = ["project-y"]
        user.admin_project_names = []
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert set(projects) == {"project-y"}
        assert len(projects) == 1

    # ========================================================================
    # 4. SECURITY-CRITICAL SCENARIOS
    # ========================================================================

    def test_get_accessible_projects_complete_overlap_deduplication(self):
        """Verify complete overlap scenario - all applications are also admin.

        CRITICAL: Tests deduplication when all projects overlap.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-12"
        user.project_names = ["project-1", "project-2", "project-3"]
        user.admin_project_names = ["project-1", "project-2", "project-3"]
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert set(projects) == {"project-1", "project-2", "project-3"}
        assert len(projects) == 3  # All duplicates removed

    def test_get_accessible_projects_single_application(self):
        """Verify single project access for plain user.

        Common case: User assigned to single project.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-13"
        user.project_names = ["solo-project"]
        user.admin_project_names = None
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        assert projects == ["solo-project"]
        assert len(projects) == 1

    def test_get_accessible_projects_large_project_list(self):
        """Verify handling of users with many projects.

        Performance edge case: Ensure deduplication works with large lists.
        """
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-14"
        # Generate 50 application projects and 30 admin projects with 10 overlaps
        user.project_names = [f"proj-{i}" for i in range(50)]
        user.admin_project_names = [f"proj-{i}" for i in range(40, 70)]  # proj-40 to proj-49 overlap
        access_filter = AccessFilter(user)

        # Act
        projects = access_filter.get_accessible_projects()

        # Assert
        # Total unique: 0-39 (40 projects) + 40-49 (10 overlap) + 50-69 (20 unique) = 70 unique
        assert len(projects) == 70
        assert len(set(projects)) == 70  # All unique, no duplicates

    # ========================================================================
    # 5. INITIALIZATION TESTS
    # ========================================================================

    def test_init_stores_user_reference(self):
        """Verify AccessFilter correctly stores user reference."""
        # Arrange
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "test-user-15"

        # Act
        access_filter = AccessFilter(user)

        # Assert
        assert access_filter._user is user

    def test_init_with_fixture_users(self, mock_plain_user, mock_admin_user):
        """Verify initialization works with fixture users."""
        # Arrange & Act
        plain_filter = AccessFilter(mock_plain_user)
        admin_filter = AccessFilter(mock_admin_user)

        # Assert
        assert plain_filter._user is mock_plain_user
        assert admin_filter._user is mock_admin_user

    # ========================================================================
    # 6. PROJECT ACCESS CONTEXT TESTS (NEW API)
    # ========================================================================

    # =========================================================================
    # 6.1. ROLE SEGMENTATION TESTS (5 tests)
    # =========================================================================

    def test_get_project_access_context_plain_user_only(self):
        """Verify plain users get correct project categorization."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "user-1"
        user.project_names = ["proj-a", "proj-b"]
        user.admin_project_names = None

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        assert isinstance(ctx, ProjectAccessContext)
        assert ctx.user_id == "user-1"
        assert set(ctx.plain_user_projects) == {"proj-a", "proj-b"}
        assert ctx.admin_projects == []

    def test_get_project_access_context_admin_user_only(self):
        """Verify admin users get correct project categorization."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "user-2"
        user.project_names = None
        user.admin_project_names = ["proj-c"]

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        assert ctx.user_id == "user-2"
        assert ctx.plain_user_projects == []
        assert ctx.admin_projects == ["proj-c"]

    def test_get_project_access_context_mixed_no_overlap(self):
        """Verify mixed role with no overlapping projects."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "user-3"
        user.project_names = ["proj-a", "proj-b"]
        user.admin_project_names = ["proj-c", "proj-d"]

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        assert set(ctx.plain_user_projects) == {"proj-a", "proj-b"}
        assert set(ctx.admin_projects) == {"proj-c", "proj-d"}

    def test_get_project_access_context_mixed_with_overlap(self):
        """CRITICAL: Verify overlapping projects appear in BOTH lists (Union)."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "user-4"
        user.project_names = ["proj-a", "proj-b"]
        user.admin_project_names = ["proj-b", "proj-c"]  # proj-b overlaps

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        # proj-b should appear in BOTH lists (Union strategy)
        assert set(ctx.plain_user_projects) == {"proj-a", "proj-b"}
        assert set(ctx.admin_projects) == {"proj-b", "proj-c"}
        assert "proj-b" in ctx.plain_user_projects
        assert "proj-b" in ctx.admin_projects

    def test_get_project_access_context_empty_both(self):
        """CRITICAL: Verify empty lists for no access."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "user-5"
        user.project_names = None
        user.admin_project_names = None

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        assert ctx.plain_user_projects == []
        assert ctx.admin_projects == []

    # =========================================================================
    # 6.2. ERROR HANDLING TESTS (3 tests)
    # =========================================================================

    def test_get_project_access_context_missing_id_raises(self):
        """CRITICAL: Verify missing user ID raises ValueError."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = None
        user.project_names = ["proj-a"]
        user.admin_project_names = None

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)

        with pytest.raises(ValueError, match="User ID is required"):
            access_filter.get_project_access_context()

    def test_get_project_access_context_empty_string_id_raises(self):
        """Verify empty string user ID raises ValueError."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = ""
        user.project_names = ["proj-a"]
        user.admin_project_names = None

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)

        with pytest.raises(ValueError, match="User ID is required"):
            access_filter.get_project_access_context()

    def test_get_project_access_context_with_valid_id(self):
        """Verify valid user ID is captured in context."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "valid-user-id"
        user.project_names = ["proj-a"]
        user.admin_project_names = None

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        assert ctx.user_id == "valid-user-id"

    # =========================================================================
    # 6.3. BACKWARD COMPATIBILITY TESTS (2 tests)
    # =========================================================================

    def test_get_accessible_projects_still_works_deprecated(self):
        """Verify deprecated method returns merged list for backward compat."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "user-9"
        user.project_names = ["proj-a"]
        user.admin_project_names = ["proj-b"]

        access_filter = AccessFilter(user)
        projects = access_filter.get_accessible_projects()

        assert set(projects) == {"proj-a", "proj-b"}

    def test_get_accessible_projects_logs_deprecation_warning(self):
        """Verify deprecated method logs warning.

        Note: This test verifies the method executes without error.
        The actual deprecation warning is logged via a custom logger format
        which is validated in integration tests.
        """
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "user-10"
        user.project_names = ["proj-a"]
        user.admin_project_names = None

        access_filter = AccessFilter(user)

        # Method should execute successfully (deprecation warning logged to custom handler)
        projects = access_filter.get_accessible_projects()
        assert projects == ["proj-a"]

    # =========================================================================
    # 6.4. EDGE CASES (5 tests)
    # =========================================================================

    def test_get_project_access_context_large_project_lists(self):
        """Verify handling of users with many projects."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "user-11"
        user.project_names = [f"proj-{i}" for i in range(100)]
        user.admin_project_names = [f"admin-{i}" for i in range(50)]

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        assert len(ctx.plain_user_projects) == 100
        assert len(ctx.admin_projects) == 50

    def test_get_project_access_context_complete_overlap(self):
        """Verify complete overlap - all plain projects are also admin."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "user-12"
        user.project_names = ["proj-1", "proj-2"]
        user.admin_project_names = ["proj-1", "proj-2"]

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        assert set(ctx.plain_user_projects) == {"proj-1", "proj-2"}
        assert set(ctx.admin_projects) == {"proj-1", "proj-2"}

    def test_get_project_access_context_single_project(self):
        """Verify single project access."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "user-13"
        user.project_names = ["only-proj"]
        user.admin_project_names = None

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        assert ctx.plain_user_projects == ["only-proj"]
        assert ctx.admin_projects == []

    def test_get_project_access_context_empty_list_vs_none(self):
        """Verify empty list and None are handled identically."""
        user_none = MagicMock(spec=User)
        user_none.is_admin = False
        user_none.id = "user-14"
        user_none.project_names = None
        user_none.admin_project_names = None

        user_empty = MagicMock(spec=User)
        user_empty.is_admin = False
        user_empty.id = "user-15"
        user_empty.project_names = []
        user_empty.admin_project_names = []

        # Clear cache before tests
        AccessFilter._context_cache.clear()

        ctx_none = AccessFilter(user_none).get_project_access_context()

        AccessFilter._context_cache.clear()

        ctx_empty = AccessFilter(user_empty).get_project_access_context()

        assert ctx_none.plain_user_projects == ctx_empty.plain_user_projects == []
        assert ctx_none.admin_projects == ctx_empty.admin_projects == []

    def test_get_project_access_context_preserves_order(self):
        """Verify project list order is preserved."""
        user = MagicMock(spec=User)
        user.is_admin = False
        user.id = "user-16"
        user.project_names = ["proj-z", "proj-a", "proj-m"]
        user.admin_project_names = ["admin-y", "admin-b"]

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        # Order should match input
        assert ctx.plain_user_projects == ["proj-z", "proj-a", "proj-m"]
        assert ctx.admin_projects == ["admin-y", "admin-b"]

    # =========================================================================
    # 6.5. SUPER ADMIN TESTS (3 tests)
    # =========================================================================

    def test_get_project_access_context_super_admin_with_empty_lists(self):
        """CRITICAL: Verify super admin gets unrestricted access even with empty project lists."""
        user = MagicMock(spec=User)
        user.is_admin = True  # Super admin
        user.id = "super-admin-1"
        user.project_names = []
        user.admin_project_names = []

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        assert ctx.user_id == "super-admin-1"
        assert ctx.is_super_admin is True
        assert ctx.plain_user_projects == []
        assert ctx.admin_projects == []

    def test_get_project_access_context_super_admin_with_none_lists(self):
        """CRITICAL: Verify super admin gets unrestricted access with None project lists."""
        user = MagicMock(spec=User)
        user.is_admin = True  # Super admin
        user.id = "super-admin-2"
        user.project_names = None
        user.admin_project_names = None

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        assert ctx.user_id == "super-admin-2"
        assert ctx.is_super_admin is True
        # Project lists are empty but is_super_admin flag grants full access
        assert ctx.plain_user_projects == []
        assert ctx.admin_projects == []

    def test_get_project_access_context_super_admin_with_existing_projects(self):
        """Verify super admin flag is set even when they have project assignments."""
        user = MagicMock(spec=User)
        user.is_admin = True  # Super admin
        user.id = "super-admin-3"
        user.project_names = ["proj-a"]
        user.admin_project_names = ["proj-b"]

        # Clear cache before test
        AccessFilter._context_cache.clear()

        access_filter = AccessFilter(user)
        ctx = access_filter.get_project_access_context()

        # Super admin flag should be set (project lists ignored by query builder)
        assert ctx.user_id == "super-admin-3"
        assert ctx.is_super_admin is True
