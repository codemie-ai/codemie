# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Unit tests for _run_keycloak_migration() in main.py"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


# ===========================================
# Tests: _run_keycloak_migration()
# ===========================================


@pytest.mark.asyncio
@patch("codemie.rest_api.main.config")
async def test_run_keycloak_migration_disabled_returns_early(mock_config):
    """Migration disabled returns early without importing coordinator"""
    # Arrange
    mock_config.KEYCLOAK_MIGRATION_ENABLED = False
    mock_config.IDP_PROVIDER = "keycloak"
    mock_config.ENABLE_USER_MANAGEMENT = True

    # Act
    from codemie.rest_api.main import _run_keycloak_migration

    await _run_keycloak_migration()

    # Assert - no exception raised, returns early


@pytest.mark.asyncio
@patch("codemie.rest_api.main.config")
async def test_run_keycloak_migration_wrong_idp_provider_returns_early(mock_config):
    """Wrong IDP provider returns early without importing coordinator"""
    # Arrange
    mock_config.KEYCLOAK_MIGRATION_ENABLED = True
    mock_config.IDP_PROVIDER = "local"  # Not keycloak
    mock_config.ENABLE_USER_MANAGEMENT = True

    # Act
    from codemie.rest_api.main import _run_keycloak_migration

    await _run_keycloak_migration()

    # Assert - no exception raised, returns early


@pytest.mark.asyncio
@patch("codemie.rest_api.main.config")
async def test_run_keycloak_migration_user_management_disabled_returns_early(mock_config):
    """User management disabled returns early without importing coordinator"""
    # Arrange
    mock_config.KEYCLOAK_MIGRATION_ENABLED = True
    mock_config.IDP_PROVIDER = "keycloak"
    mock_config.ENABLE_USER_MANAGEMENT = False

    # Act
    from codemie.rest_api.main import _run_keycloak_migration

    await _run_keycloak_migration()

    # Assert - no exception raised, returns early


@pytest.mark.asyncio
@patch("codemie.rest_api.main.logger")
@patch("codemie.rest_api.main.config")
async def test_run_keycloak_migration_all_conditions_met_runs_coordinator(mock_config, mock_logger):
    """All conditions met imports and calls run_keycloak_migration"""
    # Arrange
    mock_config.KEYCLOAK_MIGRATION_ENABLED = True
    mock_config.IDP_PROVIDER = "keycloak"
    mock_config.ENABLE_USER_MANAGEMENT = True

    mock_run = AsyncMock()

    # Act
    with patch("codemie.enterprise.migration.coordinator.run_keycloak_migration", mock_run):
        from codemie.rest_api.main import _run_keycloak_migration

        await _run_keycloak_migration()

    # Assert
    mock_run.assert_called_once()
    mock_logger.error.assert_not_called()


@pytest.mark.asyncio
@patch("codemie.rest_api.main.logger")
@patch("codemie.rest_api.main.config")
async def test_run_keycloak_migration_exception_caught_and_logged(mock_config, mock_logger):
    """Exception in coordinator is caught, logged, and does not re-raise"""
    # Arrange
    mock_config.KEYCLOAK_MIGRATION_ENABLED = True
    mock_config.IDP_PROVIDER = "keycloak"
    mock_config.ENABLE_USER_MANAGEMENT = True

    mock_run = AsyncMock(side_effect=RuntimeError("Migration failed"))

    # Act
    with patch("codemie.enterprise.migration.coordinator.run_keycloak_migration", mock_run):
        from codemie.rest_api.main import _run_keycloak_migration

        # Should not raise - non-fatal
        await _run_keycloak_migration()

    # Assert
    mock_logger.error.assert_called_once()
    log_call = mock_logger.error.call_args[0][0]
    assert "Keycloak migration failed" in log_call


@pytest.mark.asyncio
@patch("codemie.rest_api.main.logger")
@patch("codemie.rest_api.main.config")
async def test_run_keycloak_migration_import_error_caught(mock_config, mock_logger):
    """ImportError when coordinator not available is caught and logged"""
    # Arrange
    mock_config.KEYCLOAK_MIGRATION_ENABLED = True
    mock_config.IDP_PROVIDER = "keycloak"
    mock_config.ENABLE_USER_MANAGEMENT = True

    # Act
    with patch(
        "codemie.enterprise.migration.coordinator.run_keycloak_migration",
        side_effect=ImportError("codemie_enterprise.migration not found"),
    ):
        from codemie.rest_api.main import _run_keycloak_migration

        # Should not raise - non-fatal
        await _run_keycloak_migration()

    # Assert
    mock_logger.error.assert_called_once()
