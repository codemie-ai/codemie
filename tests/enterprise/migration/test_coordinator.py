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

"""Unit tests for CodemieMigrationDeps and run_keycloak_migration()"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.enterprise.migration.coordinator import CodemieMigrationDeps
from codemie.rest_api.models.dynamic_config import ConfigValueType


# ===========================================
# Tests: Migration __init__ module
# ===========================================


def test_migration_init_importable():
    """Test that migration __init__ module is importable with correct __all__"""
    # Arrange & Act
    from codemie.enterprise import migration

    # Assert
    assert hasattr(migration, "__all__")
    assert migration.__all__ == []


# ===========================================
# Tests: CodemieMigrationDeps.get_session()
# ===========================================


@patch("codemie.enterprise.migration.coordinator.get_async_session")
def test_get_session_returns_context_manager(mock_get_async_session):
    """Get session returns async session context manager"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_cm = MagicMock()
    mock_get_async_session.return_value = mock_cm

    # Act
    result = deps.get_session()

    # Assert
    assert result == mock_cm
    mock_get_async_session.assert_called_once()


# ===========================================
# Tests: CodemieMigrationDeps.advisory_lock()
# ===========================================


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.PostgresClient.get_async_engine")
async def test_advisory_lock_acquired(mock_get_engine):
    """Advisory lock yields True when lock acquired"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_get_engine.return_value = mock_engine
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    # Mock pg_try_advisory_lock returning True (lock acquired)
    mock_result = MagicMock()
    mock_result.scalar.return_value = True
    mock_conn.execute = AsyncMock(return_value=mock_result)

    # Act
    async with deps.advisory_lock() as acquired:
        # Assert
        assert acquired is True

    # Verify unlock was called
    assert mock_conn.execute.call_count == 2  # lock + unlock


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.PostgresClient.get_async_engine")
async def test_advisory_lock_not_acquired(mock_get_engine):
    """Advisory lock yields False when lock not acquired (non-leader)"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_get_engine.return_value = mock_engine
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    # Mock pg_try_advisory_lock returning False (lock not acquired)
    mock_result = MagicMock()
    mock_result.scalar.return_value = False
    mock_conn.execute = AsyncMock(return_value=mock_result)

    # Act
    async with deps.advisory_lock() as acquired:
        # Assert
        assert acquired is False

    # Verify unlock was NOT called (lock not acquired)
    assert mock_conn.execute.call_count == 1  # only lock attempt


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.PostgresClient.get_async_engine")
async def test_advisory_lock_ensures_unlock_on_exit(mock_get_engine):
    """Advisory lock ensures unlock is called on exit even if exception raised"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_get_engine.return_value = mock_engine
    mock_engine.connect.return_value.__aenter__.return_value = mock_conn

    # Mock lock acquired
    mock_result = MagicMock()
    mock_result.scalar.return_value = True
    mock_conn.execute = AsyncMock(return_value=mock_result)

    # Act & Assert
    with pytest.raises(RuntimeError):
        async with deps.advisory_lock() as acquired:
            assert acquired is True
            raise RuntimeError("Simulated error")

    # Verify unlock was called despite exception
    assert mock_conn.execute.call_count == 2  # lock + unlock


# ===========================================
# Tests: CodemieMigrationDeps.read_config()
# ===========================================


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.DynamicConfigService.aget_by_key")
async def test_read_config_found_returns_value(mock_aget_by_key):
    """Read config returns value when record found"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_config = MagicMock()
    mock_config.value = "test_value"
    mock_aget_by_key.return_value = mock_config

    # Act
    result = await deps.read_config("TEST_KEY")

    # Assert
    assert result == "test_value"
    mock_aget_by_key.assert_called_once_with("TEST_KEY")


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.DynamicConfigService.aget_by_key")
async def test_read_config_not_found_returns_none(mock_aget_by_key):
    """Read config returns None when record not found"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_aget_by_key.return_value = None

    # Act
    result = await deps.read_config("MISSING_KEY")

    # Assert
    assert result is None
    mock_aget_by_key.assert_called_once_with("MISSING_KEY")


# ===========================================
# Tests: CodemieMigrationDeps.write_config()
# ===========================================


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.DynamicConfigService.aset")
async def test_write_config_delegates_to_service(mock_aset):
    """Write config delegates to DynamicConfigService.aset correctly"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_config = MagicMock()
    mock_aset.return_value = mock_config

    # Act
    await deps.write_config("TEST_KEY", "test_value")

    # Assert
    mock_aset.assert_called_once_with(
        "TEST_KEY", "test_value", ConfigValueType.STRING, None, "keycloak_migration_service"
    )


# ===========================================
# Tests: CodemieMigrationDeps.get_user_by_id()
# ===========================================


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.user_repository")
async def test_get_user_by_id_delegates_to_repository(mock_repo):
    """Get user by ID delegates to user_repository"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_session = MagicMock()
    mock_user = MagicMock()
    mock_repo.aget_by_id = AsyncMock(return_value=mock_user)

    # Act
    result = await deps.get_user_by_id(mock_session, "user-123")

    # Assert
    assert result == mock_user
    mock_repo.aget_by_id.assert_called_once_with(mock_session, "user-123")


# ===========================================
# Tests: CodemieMigrationDeps.get_user_by_email()
# ===========================================


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.user_repository")
async def test_get_user_by_email_delegates_to_repository(mock_repo):
    """Get user by email delegates to user_repository"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_session = MagicMock()
    mock_user = MagicMock()
    mock_repo.aget_by_email = AsyncMock(return_value=mock_user)

    # Act
    result = await deps.get_user_by_email(mock_session, "user@example.com")

    # Assert
    assert result == mock_user
    mock_repo.aget_by_email.assert_called_once_with(mock_session, "user@example.com")


# ===========================================
# Tests: CodemieMigrationDeps.create_user()
# ===========================================


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.user_repository")
async def test_create_user_constructs_userdb_and_calls_acreate(mock_repo):
    """Create user constructs UserDB and calls acreate"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_session = MagicMock()
    mock_repo.acreate = AsyncMock()

    user_data = {
        "id": "user-123",
        "username": "testuser",
        "email": "test@example.com",
        "name": "Test User",
        "auth_source": "keycloak",
        "is_active": True,
        "is_admin": False,
        "email_verified": True,
    }

    # Act
    await deps.create_user(mock_session, user_data)

    # Assert
    mock_repo.acreate.assert_called_once()
    call_args = mock_repo.acreate.call_args
    assert call_args[0][0] == mock_session
    created_user = call_args[0][1]
    assert created_user.id == "user-123"
    assert created_user.email == "test@example.com"


# ===========================================
# Tests: CodemieMigrationDeps.add_user_project()
# ===========================================


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.user_project_repository")
async def test_add_user_project_delegates_correctly(mock_repo):
    """Add user project delegates to user_project_repository"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_session = MagicMock()
    mock_repo.aadd_project = AsyncMock()

    # Act
    await deps.add_user_project(mock_session, "user-123", "project-1", is_admin=True)

    # Assert
    mock_repo.aadd_project.assert_called_once_with(mock_session, "user-123", "project-1", True)


# ===========================================
# Tests: CodemieMigrationDeps.add_user_kb()
# ===========================================


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.user_kb_repository")
async def test_add_user_kb_delegates_correctly(mock_repo):
    """Add user KB delegates to user_kb_repository"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_session = MagicMock()
    mock_repo.aadd_kb = AsyncMock()

    # Act
    await deps.add_user_kb(mock_session, "user-123", "kb-1")

    # Assert
    mock_repo.aadd_kb.assert_called_once_with(mock_session, "user-123", "kb-1")


# ===========================================
# Tests: CodemieMigrationDeps.get_or_create_application()
# ===========================================


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.application_repository")
async def test_get_or_create_application_delegates_correctly(mock_repo):
    """Get or create application delegates to application_repository"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_session = MagicMock()
    mock_app = MagicMock()
    mock_repo.aget_or_create = AsyncMock(return_value=mock_app)

    # Act
    result = await deps.get_or_create_application(mock_session, "codemie")

    # Assert
    assert result == mock_app
    mock_repo.aget_or_create.assert_called_once_with(mock_session, "codemie")


# ===========================================
# Tests: CodemieMigrationDeps.ensure_personal_project()
# ===========================================


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.personal_project_service")
async def test_ensure_personal_project_delegates_correctly(mock_service):
    """Ensure personal project delegates to personal_project_service"""
    # Arrange
    deps = CodemieMigrationDeps()
    mock_service.ensure_personal_project_async = AsyncMock()

    # Act
    await deps.ensure_personal_project("user-123", "user@example.com")

    # Assert
    mock_service.ensure_personal_project_async.assert_called_once_with("user-123", "user@example.com")


# ===========================================
# Tests: run_keycloak_migration()
# ===========================================


@pytest.mark.asyncio
@patch("codemie.enterprise.migration.coordinator.config")
@patch("codemie.enterprise.migration.coordinator.CodemieMigrationDeps")
async def test_run_keycloak_migration_imports_and_calls_coordinator(mock_deps_cls, mock_config):
    """Run keycloak migration imports coordinator and calls run()"""
    # Arrange
    mock_config.KEYCLOAK_ADMIN_URL = "http://keycloak"
    mock_config.KEYCLOAK_ADMIN_REALM = "master"
    mock_config.KEYCLOAK_ADMIN_CLIENT_ID = "admin-cli"
    mock_config.KEYCLOAK_ADMIN_CLIENT_SECRET = "secret"
    mock_config.KEYCLOAK_MIGRATION_BATCH_SIZE = 100
    mock_config.KEYCLOAK_MIGRATION_LOCK_TIMEOUT_MINUTES = 30
    mock_config.KEYCLOAK_MIGRATION_WAIT_INTERVAL_SECONDS = 10
    mock_config.ADMIN_USER_ID = "admin-id"
    mock_config.ADMIN_ROLE_NAME = "ADMIN"
    mock_config.USER_PROJECT_LIMIT = 3

    mock_deps = MagicMock()
    mock_deps_cls.return_value = mock_deps

    # Mock the enterprise imports
    mock_coordinator = MagicMock()
    mock_coordinator.run = AsyncMock()

    with patch.dict(
        "sys.modules",
        {
            "codemie_enterprise.migration": MagicMock(
                KeycloakMigrationCoordinator=lambda config, deps: mock_coordinator, MigrationConfig=MagicMock()
            )
        },
    ):
        # Act
        from codemie.enterprise.migration.coordinator import run_keycloak_migration

        await run_keycloak_migration()

        # Assert
        mock_coordinator.run.assert_called_once()
