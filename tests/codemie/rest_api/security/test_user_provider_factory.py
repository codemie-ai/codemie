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

from unittest.mock import patch

from codemie.rest_api.security.user_providers.factory import get_user_provider
from codemie.rest_api.security.user_providers.legacy_jwt import LegacyJwtUserProvider
from codemie.rest_api.security.user_providers.persistent import PersistentUserProvider


class TestUserProviderFactory:
    """Test suite for user provider factory - provider selection logic"""

    @patch("codemie.rest_api.security.user_providers.factory.config")
    def test_get_user_provider_returns_persistent_when_enabled(self, mock_config):
        """Test that PersistentUserProvider is returned when ENABLE_USER_MANAGEMENT=True"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = True

        # Act
        provider = get_user_provider()

        # Assert
        assert isinstance(provider, PersistentUserProvider)

    @patch("codemie.rest_api.security.user_providers.factory.config")
    def test_get_user_provider_returns_legacy_when_disabled(self, mock_config):
        """Test that LegacyJwtUserProvider is returned when ENABLE_USER_MANAGEMENT=False"""
        # Arrange
        mock_config.ENABLE_USER_MANAGEMENT = False

        # Act
        provider = get_user_provider()

        # Assert
        assert isinstance(provider, LegacyJwtUserProvider)
