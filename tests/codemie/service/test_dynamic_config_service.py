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

"""Unit tests for DynamicConfigService"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.dynamic_config import ConfigValueType, DynamicConfig
from codemie.rest_api.security.user import User
from codemie.service.dynamic_config_service import DynamicConfigService


# ===========================================
# Fixtures
# ===========================================


@pytest.fixture
def mock_user():
    """Super admin user fixture"""
    return User(
        id="admin-123",
        name="Admin User",
        username="admin",
        email="admin@example.com",
        roles=[],
        project_names=[],
        admin_project_names=[],
        picture="",
        knowledge_bases=[],
        user_type="regular",
        is_super_admin=True,
        auth_token=None,
    )


@pytest.fixture
def mock_regular_user():
    """Regular user fixture (not admin)"""
    return User(
        id="user-123",
        name="Regular User",
        username="user",
        email="user@example.com",
        roles=[],
        project_names=[],
        admin_project_names=[],
        picture="",
        knowledge_bases=[],
        user_type="regular",
        is_super_admin=False,
        auth_token=None,
    )


@pytest.fixture
def sample_config():
    """Sample DynamicConfig fixture"""
    now = datetime.now(UTC).replace(tzinfo=None)
    return DynamicConfig(
        id="config-123",
        key="MAX_RETRIES",
        value="5",
        value_type=ConfigValueType.INT,
        description="Maximum retry attempts",
        date=now,
        update_date=now,
        updated_by="admin-123",
    )


# ===========================================
# Tests: convert_value()
# ===========================================


def test_convert_value_string():
    """Convert value with STRING type returns string unchanged"""
    # Arrange & Act
    result = DynamicConfigService.convert_value("test value", ConfigValueType.STRING)

    # Assert
    assert result == "test value"
    assert isinstance(result, str)


def test_convert_value_int_valid():
    """Convert value with INT type returns integer"""
    # Arrange & Act
    result = DynamicConfigService.convert_value("42", ConfigValueType.INT)

    # Assert
    assert result == 42
    assert isinstance(result, int)


def test_convert_value_int_invalid():
    """Convert value with INT type raises exception for invalid integer"""
    # Arrange & Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService.convert_value("not_a_number", ConfigValueType.INT)

    assert exc_info.value.code == 400
    assert "Invalid int value" in exc_info.value.message


def test_convert_value_float_valid():
    """Convert value with FLOAT type returns float"""
    # Arrange & Act
    result = DynamicConfigService.convert_value("3.14", ConfigValueType.FLOAT)

    # Assert
    assert result == 3.14
    assert isinstance(result, float)


def test_convert_value_float_invalid():
    """Convert value with FLOAT type raises exception for invalid float"""
    # Arrange & Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService.convert_value("not_a_float", ConfigValueType.FLOAT)

    assert exc_info.value.code == 400
    assert "Invalid float value" in exc_info.value.message


@pytest.mark.parametrize(
    "value_str,expected",
    [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("t", True),
        ("T", True),
        ("yes", True),
        ("Yes", True),
        ("YES", True),
        ("y", True),
        ("Y", True),
        ("on", True),
        ("On", True),
        ("ON", True),
        ("1", True),
    ],
)
def test_convert_value_bool_truthy_variations(value_str, expected):
    """Convert value with BOOL type handles all truthy variations"""
    # Arrange & Act
    result = DynamicConfigService.convert_value(value_str, ConfigValueType.BOOL)

    # Assert
    assert result is expected
    assert isinstance(result, bool)


@pytest.mark.parametrize(
    "value_str,expected",
    [
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("f", False),
        ("F", False),
        ("no", False),
        ("No", False),
        ("NO", False),
        ("n", False),
        ("N", False),
        ("off", False),
        ("Off", False),
        ("OFF", False),
        ("0", False),
    ],
)
def test_convert_value_bool_falsy_variations(value_str, expected):
    """Convert value with BOOL type handles all falsy variations"""
    # Arrange & Act
    result = DynamicConfigService.convert_value(value_str, ConfigValueType.BOOL)

    # Assert
    assert result is expected
    assert isinstance(result, bool)


def test_convert_value_bool_invalid():
    """Convert value with BOOL type raises exception for invalid boolean"""
    # Arrange & Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService.convert_value("maybe", ConfigValueType.BOOL)

    assert exc_info.value.code == 400
    assert "Invalid boolean value" in exc_info.value.message
    assert "maybe" in exc_info.value.details


# ===========================================
# Tests: _validate_key_format()
# ===========================================


@pytest.mark.parametrize(
    "valid_key",
    [
        "MAX_RETRIES",
        "FEATURE_X_ENABLED",
        "API_TIMEOUT",
        "DEBUG_MODE",
        "CACHE_TTL_SECONDS",
        "A",
        "A1",
        "A_B_C_1_2_3",
    ],
)
def test_validate_key_format_valid(valid_key):
    """Validate key format accepts valid UPPER_SNAKE_CASE keys"""
    # Arrange & Act & Assert - should not raise
    DynamicConfigService._validate_key_format(valid_key)


def test_validate_key_format_lowercase():
    """Validate key format rejects lowercase keys"""
    # Arrange & Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService._validate_key_format("max_retries")

    assert exc_info.value.code == 400
    assert "Invalid key format" in exc_info.value.message
    assert "UPPER_SNAKE_CASE" in exc_info.value.details


def test_validate_key_format_starts_with_number():
    """Validate key format rejects keys starting with number"""
    # Arrange & Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService._validate_key_format("1_CONFIG")

    assert exc_info.value.code == 400
    assert "Invalid key format" in exc_info.value.message


def test_validate_key_format_has_spaces():
    """Validate key format rejects keys with spaces"""
    # Arrange & Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService._validate_key_format("MAX RETRIES")

    assert exc_info.value.code == 400
    assert "Invalid key format" in exc_info.value.message


def test_validate_key_format_has_dash():
    """Validate key format rejects keys with dashes"""
    # Arrange & Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService._validate_key_format("MAX-RETRIES")

    assert exc_info.value.code == 400
    assert "Invalid key format" in exc_info.value.message


# ===========================================
# Tests: _process_get_result()
# ===========================================


def test_process_get_result_found_with_conversion(sample_config):
    """Process get result converts value when config found"""
    # Arrange & Act
    result = DynamicConfigService._process_get_result("MAX_RETRIES", sample_config, None)

    # Assert
    assert result == 5
    assert isinstance(result, int)


def test_process_get_result_not_found_with_default():
    """Process get result returns default when config not found"""
    # Arrange & Act
    result = DynamicConfigService._process_get_result("MISSING_KEY", None, "default_value")

    # Assert
    assert result == "default_value"


def test_process_get_result_not_found_without_default():
    """Process get result raises 404 when config not found and no default"""
    # Arrange & Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService._process_get_result("MISSING_KEY", None, None)

    assert exc_info.value.code == 404
    assert "Config not found" in exc_info.value.message
    assert "MISSING_KEY" in exc_info.value.details


# ===========================================
# Tests: _prepare_set()
# ===========================================


def test_prepare_set_valid():
    """Prepare set validates key and converts value to string"""
    # Arrange & Act
    result = DynamicConfigService._prepare_set("MAX_RETRIES", 10, ConfigValueType.INT)

    # Assert
    assert result == "10"


def test_prepare_set_invalid_key():
    """Prepare set raises exception for invalid key format"""
    # Arrange & Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService._prepare_set("invalid_key", 10, ConfigValueType.INT)

    assert exc_info.value.code == 400
    assert "Invalid key format" in exc_info.value.message


def test_prepare_set_unconvertible_value():
    """Prepare set raises exception for unconvertible value"""
    # Arrange & Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService._prepare_set("MAX_RETRIES", "not_a_number", ConfigValueType.INT)

    assert exc_info.value.code == 400
    assert "Invalid int value" in exc_info.value.message


# ===========================================
# Tests: _apply_update()
# ===========================================


def test_apply_update_all_fields(sample_config):
    """Apply update mutates all fields on existing config"""
    # Arrange
    original_update_date = sample_config.update_date

    # Act
    DynamicConfigService._apply_update(sample_config, "10", ConfigValueType.INT, "Updated description", "new-admin-456")

    # Assert
    assert sample_config.value == "10"
    assert sample_config.value_type == ConfigValueType.INT
    assert sample_config.description == "Updated description"
    assert sample_config.updated_by == "new-admin-456"
    assert sample_config.update_date > original_update_date


def test_apply_update_without_description(sample_config):
    """Apply update preserves description when None passed"""
    # Arrange
    original_description = sample_config.description

    # Act
    DynamicConfigService._apply_update(sample_config, "20", ConfigValueType.INT, None, "admin-789")

    # Assert
    assert sample_config.value == "20"
    assert sample_config.description == original_description  # Unchanged


# ===========================================
# Tests: _build_new_config()
# ===========================================


def test_build_new_config_all_fields():
    """Build new config creates DynamicConfig with all fields"""
    # Arrange & Act
    config = DynamicConfigService._build_new_config(
        "NEW_KEY", "42", ConfigValueType.INT, "Test description", "admin-123"
    )

    # Assert
    assert config.key == "NEW_KEY"
    assert config.value == "42"
    assert config.value_type == ConfigValueType.INT
    assert config.description == "Test description"
    assert config.updated_by == "admin-123"
    assert config.id is not None
    assert config.date is not None
    assert config.update_date is not None


def test_build_new_config_without_description():
    """Build new config creates DynamicConfig without description"""
    # Arrange & Act
    config = DynamicConfigService._build_new_config("NEW_KEY", "42", ConfigValueType.INT, None, "admin-123")

    # Assert
    assert config.description is None


# ===========================================
# Tests: get()
# ===========================================


@patch("codemie.service.dynamic_config_service.Session")
def test_get_found(mock_session_cls, sample_config):
    """Get returns typed value when config exists"""
    # Arrange
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = sample_config
    mock_session.exec.return_value = mock_exec_result

    # Act
    result = DynamicConfigService.get("MAX_RETRIES")

    # Assert
    assert result == 5
    assert isinstance(result, int)


@patch("codemie.service.dynamic_config_service.Session")
def test_get_not_found_with_default(mock_session_cls):
    """Get returns default when config not found"""
    # Arrange
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = None
    mock_session.exec.return_value = mock_exec_result

    # Act
    result = DynamicConfigService.get("MISSING_KEY", default=99)

    # Assert
    assert result == 99


@patch("codemie.service.dynamic_config_service.Session")
def test_get_not_found_without_default(mock_session_cls):
    """Get raises 404 when config not found and no default"""
    # Arrange
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = None
    mock_session.exec.return_value = mock_exec_result

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService.get("MISSING_KEY")

    assert exc_info.value.code == 404
    assert "Config not found" in exc_info.value.message


# ===========================================
# Tests: set()
# ===========================================


@patch("codemie.service.dynamic_config_service.Session")
def test_set_creates_new_config(mock_session_cls, mock_user):
    """Set creates new config when key does not exist"""
    # Arrange
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = None  # Not found
    mock_session.exec.return_value = mock_exec_result

    # Act
    result = DynamicConfigService.set("NEW_KEY", "100", ConfigValueType.INT, "Test", mock_user)

    # Assert
    assert result.key == "NEW_KEY"
    assert result.value == "100"
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()


@patch("codemie.service.dynamic_config_service.Session")
def test_set_updates_existing_config(mock_session_cls, mock_user, sample_config):
    """Set updates existing config when key exists"""
    # Arrange
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = sample_config
    mock_session.exec.return_value = mock_exec_result

    # Act
    result = DynamicConfigService.set("MAX_RETRIES", "20", ConfigValueType.INT, "Updated", mock_user)

    # Assert
    assert result.value == "20"
    mock_session.add.assert_called_once_with(sample_config)
    mock_session.commit.assert_called_once()


# ===========================================
# Tests: delete()
# ===========================================


@patch("codemie.rest_api.security.user.config")
@patch("codemie.service.dynamic_config_service.Session")
def test_delete_success(mock_session_cls, mock_config, mock_user, sample_config):
    """Delete removes config when it exists"""
    # Arrange
    mock_config.ENV = "production"
    mock_config.ENABLE_USER_MANAGEMENT = True

    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = sample_config
    mock_session.exec.return_value = mock_exec_result

    # Act
    DynamicConfigService.delete("MAX_RETRIES", mock_user)

    # Assert
    mock_session.delete.assert_called_once_with(sample_config)
    mock_session.commit.assert_called_once()


@patch("codemie.rest_api.security.user.config")
@patch("codemie.service.dynamic_config_service.Session")
def test_delete_not_found(mock_session_cls, mock_config, mock_user):
    """Delete raises 404 when config not found"""
    # Arrange
    mock_config.ENV = "production"
    mock_config.ENABLE_USER_MANAGEMENT = True

    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = None
    mock_session.exec.return_value = mock_exec_result

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService.delete("MISSING_KEY", mock_user)

    assert exc_info.value.code == 404
    assert "Config not found" in exc_info.value.message


@patch("codemie.rest_api.security.user.config")
def test_delete_not_admin(mock_config, mock_regular_user):
    """Delete raises 403 when user is not super admin"""
    # Arrange - ensure ENV is not local (which makes everyone admin)
    mock_config.ENV = "production"
    mock_config.ENABLE_USER_MANAGEMENT = True

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService.delete("MAX_RETRIES", mock_regular_user)

    assert exc_info.value.code == 403
    assert "Super-admin privileges required" in exc_info.value.details


# ===========================================
# Tests: list_all()
# ===========================================


@patch("codemie.service.dynamic_config_service.Session")
def test_list_all_returns_all_configs(mock_session_cls, sample_config):
    """List all returns all configs ordered by key"""
    # Arrange
    config2 = DynamicConfig(
        id="config-456",
        key="ANOTHER_KEY",
        value="test",
        value_type=ConfigValueType.STRING,
        description=None,
        date=datetime.now(UTC).replace(tzinfo=None),
        update_date=datetime.now(UTC).replace(tzinfo=None),
        updated_by="admin-123",
    )

    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.all.return_value = [config2, sample_config]
    mock_session.exec.return_value = mock_exec_result

    # Act
    result = DynamicConfigService.list_all()

    # Assert
    assert len(result) == 2
    assert result[0].key == "ANOTHER_KEY"
    assert result[1].key == "MAX_RETRIES"


# ===========================================
# Tests: get_typed_value()
# ===========================================


@patch("codemie.service.dynamic_config_service.Session")
def test_get_typed_value_correct_type(mock_session_cls, sample_config):
    """Get typed value returns value when type matches"""
    # Arrange
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = sample_config
    mock_session.exec.return_value = mock_exec_result

    # Act
    result = DynamicConfigService.get_typed_value("MAX_RETRIES", int)

    # Assert
    assert result == 5
    assert isinstance(result, int)


@patch("codemie.service.dynamic_config_service.Session")
def test_get_typed_value_wrong_type(mock_session_cls, sample_config):
    """Get typed value raises 400 when type does not match"""
    # Arrange
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = sample_config
    mock_session.exec.return_value = mock_exec_result

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService.get_typed_value("MAX_RETRIES", str)

    assert exc_info.value.code == 400
    assert "Type mismatch" in exc_info.value.message


@patch("codemie.service.dynamic_config_service.Session")
def test_get_typed_value_not_found_with_default(mock_session_cls):
    """Get typed value returns default when not found and default provided"""
    # Arrange
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = None
    mock_session.exec.return_value = mock_exec_result

    # Act
    result = DynamicConfigService.get_typed_value("MISSING_KEY", int, default=42)

    # Assert
    assert result == 42


# ===========================================
# Tests: Async methods
# ===========================================


@pytest.mark.asyncio
@patch("codemie.service.dynamic_config_service.get_async_session")
async def test_aget_found(mock_get_async_session, sample_config):
    """Async get returns typed value when config exists"""
    # Arrange
    mock_session = MagicMock()
    mock_get_async_session.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = sample_config
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    # Act
    result = await DynamicConfigService.aget("MAX_RETRIES")

    # Assert
    assert result == 5
    assert isinstance(result, int)


@pytest.mark.asyncio
@patch("codemie.service.dynamic_config_service.get_async_session")
async def test_aget_not_found_with_default(mock_get_async_session):
    """Async get returns default when config not found"""
    # Arrange
    mock_session = MagicMock()
    mock_get_async_session.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = None
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    # Act
    result = await DynamicConfigService.aget("MISSING_KEY", default=99)

    # Assert
    assert result == 99


@pytest.mark.asyncio
@patch("codemie.service.dynamic_config_service.get_async_session")
async def test_aset_creates_new_config(mock_get_async_session):
    """Async set creates new config when key does not exist"""
    # Arrange
    mock_session = MagicMock()
    mock_get_async_session.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = None  # Not found
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    # Act
    result = await DynamicConfigService.aset("NEW_KEY", "100", ConfigValueType.INT, "Test", "admin-123")

    # Assert
    assert result.key == "NEW_KEY"
    assert result.value == "100"
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
@patch("codemie.service.dynamic_config_service.get_async_session")
async def test_aget_by_key_found(mock_get_async_session, sample_config):
    """Async get by key returns config model when found"""
    # Arrange
    mock_session = MagicMock()
    mock_get_async_session.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = sample_config
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    # Act
    result = await DynamicConfigService.aget_by_key("MAX_RETRIES")

    # Assert
    assert result == sample_config


@pytest.mark.asyncio
@patch("codemie.service.dynamic_config_service.get_async_session")
async def test_aget_by_key_not_found(mock_get_async_session):
    """Async get by key returns None when not found"""
    # Arrange
    mock_session = MagicMock()
    mock_get_async_session.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = None
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    # Act
    result = await DynamicConfigService.aget_by_key("MISSING_KEY")

    # Assert
    assert result is None


# ===========================================
# Tests: get_by_key() — raw model fetch
# ===========================================


@patch("codemie.service.dynamic_config_service.Session")
def test_get_by_key_found(mock_session_cls, sample_config):
    """get_by_key returns raw DynamicConfig model when key exists"""
    # Arrange
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = sample_config
    mock_session.exec.return_value = mock_exec_result

    # Act
    result = DynamicConfigService.get_by_key("MAX_RETRIES")

    # Assert
    assert result is sample_config
    assert result.key == "MAX_RETRIES"


@patch("codemie.service.dynamic_config_service.Session")
def test_get_by_key_not_found(mock_session_cls):
    """get_by_key returns None when key does not exist"""
    # Arrange
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__.return_value = mock_session
    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = None
    mock_session.exec.return_value = mock_exec_result

    # Act
    result = DynamicConfigService.get_by_key("NONEXISTENT_KEY")

    # Assert
    assert result is None


# ===========================================
# Tests: convert_value() — exhaustive guard
# ===========================================


def test_convert_value_unhandled_type_raises_500():
    """convert_value raises 500 for unknown type (defensive guard path)"""
    # Arrange — create a value_type that bypasses all known if-checks
    unknown_type = MagicMock()
    unknown_type.__eq__ = lambda self, other: False  # never matches any branch
    unknown_type.value = "unknown"

    # Act & Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        DynamicConfigService.convert_value("any_value", unknown_type)

    assert exc_info.value.code == 500
    assert "Unsupported value type" in exc_info.value.message


# ===========================================
# Tests: aset() — update existing path
# ===========================================


@pytest.mark.asyncio
@patch("codemie.service.dynamic_config_service.get_async_session")
async def test_aset_updates_existing_config(mock_get_async_session, sample_config):
    """Async set updates existing config when key already exists"""
    # Arrange
    mock_session = MagicMock()
    mock_get_async_session.return_value.__aenter__.return_value = mock_session
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = sample_config  # existing record found
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    # Act
    result = await DynamicConfigService.aset(
        "MAX_RETRIES", "10", ConfigValueType.INT, "Updated desc", "migration_service"
    )

    # Assert — update path: existing record mutated and returned
    assert result is sample_config
    assert sample_config.value == "10"
    assert sample_config.updated_by == "migration_service"
    mock_session.add.assert_called_once_with(sample_config)
    mock_session.commit.assert_called_once()
    mock_session.refresh.assert_called_once_with(sample_config)


# ===========================================
# Tests: get_typed_value() — None return path
# ===========================================


@patch.object(DynamicConfigService, "get", return_value=None)
def test_get_typed_value_returns_none_when_get_returns_none(mock_get):
    """get_typed_value returns None when get() itself returns None"""
    # Act
    result = DynamicConfigService.get_typed_value("SOME_KEY", str, default=None)

    # Assert
    assert result is None
    mock_get.assert_called_once_with("SOME_KEY", None)
