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

"""Unit tests for user management router pagination validation (Story 7).

Tests per_page validation: only 10, 20, 50, 100 allowed (default 20).
"""

import pytest
from unittest.mock import patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.user_management_router import list_users
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user():
    """Mock authenticated user"""
    return User(
        id="test-user",
        email="test@example.com",
        username="test",
        name="Test User",
        is_super_admin=True,
        is_active=True,
    )


@pytest.fixture
def mock_admin_access():
    """Mock admin_access_only dependency"""
    return None


class TestPerPageValidation:
    """Test per_page parameter validation (Story 7)"""

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_per_page_10_allowed(self, mock_service, mock_config, mock_user, mock_admin_access):
        """AC: per_page=10 is valid"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.list_users_with_flow.return_value = {
            "data": [],
            "pagination": {"total": 0, "page": 0, "per_page": 10},
        }

        result = list_users(
            page=0,
            per_page=10,
            search=None,
            is_active=None,
            project_name=None,
            user_type=None,
            user=mock_user,
            _=mock_admin_access,
        )

        assert result is not None
        mock_service.list_users_with_flow.assert_called_once()

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_per_page_20_allowed(self, mock_service, mock_config, mock_user, mock_admin_access):
        """AC: per_page=20 is valid (default)"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.list_users_with_flow.return_value = {
            "data": [],
            "pagination": {"total": 0, "page": 0, "per_page": 20},
        }

        result = list_users(
            page=0,
            per_page=20,
            search=None,
            is_active=None,
            project_name=None,
            user_type=None,
            user=mock_user,
            _=mock_admin_access,
        )

        assert result is not None

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_per_page_50_allowed(self, mock_service, mock_config, mock_user, mock_admin_access):
        """AC: per_page=50 is valid"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.list_users_with_flow.return_value = {
            "data": [],
            "pagination": {"total": 0, "page": 0, "per_page": 50},
        }

        result = list_users(
            page=0,
            per_page=50,
            search=None,
            is_active=None,
            project_name=None,
            user_type=None,
            user=mock_user,
            _=mock_admin_access,
        )

        assert result is not None

    @patch("codemie.rest_api.routers.user_management_router.config")
    @patch("codemie.rest_api.routers.user_management_router.user_management_service")
    def test_per_page_100_allowed(self, mock_service, mock_config, mock_user, mock_admin_access):
        """AC: per_page=100 is valid"""
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_service.list_users_with_flow.return_value = {
            "data": [],
            "pagination": {"total": 0, "page": 0, "per_page": 100},
        }

        result = list_users(
            page=0,
            per_page=100,
            search=None,
            is_active=None,
            project_name=None,
            user_type=None,
            user=mock_user,
            _=mock_admin_access,
        )

        assert result is not None

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_per_page_1_rejected(self, mock_config, mock_user, mock_admin_access):
        """AC: per_page=1 is invalid (too small)"""
        mock_config.ENABLE_USER_MANAGEMENT = True

        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_users(
                page=0,
                per_page=1,
                search=None,
                is_active=None,
                project_name=None,
                user_type=None,
                user=mock_user,
                _=mock_admin_access,
            )

        assert exc_info.value.code == 400
        assert "per_page must be one of: 10, 20, 50, 100" in exc_info.value.message

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_per_page_9_rejected(self, mock_config, mock_user, mock_admin_access):
        """AC: per_page=9 is invalid"""
        mock_config.ENABLE_USER_MANAGEMENT = True

        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_users(
                page=0,
                per_page=9,
                search=None,
                is_active=None,
                project_name=None,
                user_type=None,
                user=mock_user,
                _=mock_admin_access,
            )

        assert exc_info.value.code == 400

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_per_page_15_rejected(self, mock_config, mock_user, mock_admin_access):
        """AC: per_page=15 is invalid (not in allowed list)"""
        mock_config.ENABLE_USER_MANAGEMENT = True

        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_users(
                page=0,
                per_page=15,
                search=None,
                is_active=None,
                project_name=None,
                user_type=None,
                user=mock_user,
                _=mock_admin_access,
            )

        assert exc_info.value.code == 400

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_per_page_101_rejected(self, mock_config, mock_user, mock_admin_access):
        """AC: per_page=101 is invalid (too large)"""
        mock_config.ENABLE_USER_MANAGEMENT = True

        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_users(
                page=0,
                per_page=101,
                search=None,
                is_active=None,
                project_name=None,
                user_type=None,
                user=mock_user,
                _=mock_admin_access,
            )

        assert exc_info.value.code == 400

    @patch("codemie.rest_api.routers.user_management_router.config")
    def test_per_page_25_rejected(self, mock_config, mock_user, mock_admin_access):
        """AC: per_page=25 is invalid (not in allowed list)"""
        mock_config.ENABLE_USER_MANAGEMENT = True

        with pytest.raises(ExtendedHTTPException) as exc_info:
            list_users(
                page=0,
                per_page=25,
                search=None,
                is_active=None,
                project_name=None,
                user_type=None,
                user=mock_user,
                _=mock_admin_access,
            )

        assert exc_info.value.code == 400
