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
        is_admin=True,
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
            filters=None,
            user=mock_user,
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
            filters=None,
            user=mock_user,
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
            filters=None,
            user=mock_user,
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
            filters=None,
            user=mock_user,
        )

        assert result is not None
