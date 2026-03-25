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

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, List, Optional, Type
from uuid import uuid4

from sqlalchemy import Select
from sqlmodel import Session, select

from codemie.clients.postgres import get_async_session
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.dynamic_config import ConfigValueType, DynamicConfig
from codemie.rest_api.security.user import User

from codemie.configs.logger import logger


class DynamicConfigService:
    """
    Service layer for dynamic configuration management.

    Handles CRUD operations for runtime-updatable config key-value pairs.
    Values stored as strings in DB, converted based on type metadata.
    """

    # Key format: UPPER_SNAKE_CASE only
    KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")

    # --- Private helpers (shared by sync and async methods) ---

    @classmethod
    def _select_by_key(cls, key: str) -> Select[tuple[DynamicConfig]]:
        """Build a select statement for DynamicConfig by key."""
        return select(DynamicConfig).where(DynamicConfig.key == key)

    @classmethod
    def _validate_key_format(cls, key: str) -> None:
        """
        Validate that key follows UPPER_SNAKE_CASE pattern.

        Args:
            key: Config key to validate

        Raises:
            ExtendedHTTPException: 400 if key format invalid
        """
        if not cls.KEY_PATTERN.match(key):
            logger.error(f"Invalid key format: {key=}")
            raise ExtendedHTTPException(
                code=400,
                message="Invalid key format",
                details=f"Key '{key}' does not match UPPER_SNAKE_CASE pattern. "
                f"Must start with uppercase letter and contain only uppercase letters, numbers, and underscores. "
                f"Examples: MAX_RETRIES, FEATURE_X_ENABLED",
            )

    @classmethod
    def convert_value(cls, value_str: str, value_type: ConfigValueType) -> str | int | float | bool:
        """
        Convert string value to typed Python value based on type metadata.

        Args:
            value_str: String value from database
            value_type: Type enum indicating target type

        Returns:
            Converted value (str, int, float, or bool)

        Raises:
            ExtendedHTTPException: 400 if conversion fails
        """
        try:
            if value_type == ConfigValueType.STRING:
                return value_str

            if value_type == ConfigValueType.BOOL:
                lower_val = value_str.lower().strip()
                # Accept common boolean representations
                if lower_val in ("true", "t", "yes", "y", "on", "1"):
                    return True
                if lower_val in ("false", "f", "no", "n", "off", "0"):
                    return False
                logger.error(f"Invalid boolean value: {value_str=}")
                raise ExtendedHTTPException(
                    code=400,
                    message="Invalid boolean value",
                    details=f"Value '{value_str}' is not a valid boolean. "
                    f"Accepted values: true/false, yes/no, on/off, 1/0, t/f, y/n (case-insensitive).",
                )

            if value_type == ConfigValueType.INT:
                return int(value_str)

            if value_type == ConfigValueType.FLOAT:
                return float(value_str)

            # Exhaustive handling - should never reach here
            logger.error(f"Unhandled value type: {value_type=}")
            raise ExtendedHTTPException(code=500, message=f"Unsupported value type: {value_type}")

        except ValueError as e:
            logger.error(f"Type conversion failed: {value_str=}, {value_type=}, error={e}")
            raise ExtendedHTTPException(
                code=400,
                message=f"Invalid {value_type.value} value",
                details=f"Cannot convert '{value_str}' to {value_type.value}. Error: {e}",
            )

    @classmethod
    def _validate_super_admin(cls, user: User) -> None:
        """
        Validate that user is super-admin.

        Args:
            user: User to validate

        Raises:
            ExtendedHTTPException: 403 if not super-admin
        """
        if not user.is_admin:
            logger.warning(f"Non-super-admin user attempted config operation: {user.id=}")
            raise ExtendedHTTPException(
                code=403,
                message="Forbidden",
                details="Super-admin privileges required to manage dynamic configuration",
            )

    @classmethod
    def _process_get_result(cls, key: str, config: DynamicConfig | None, default: Any) -> Any:
        """Process config lookup result with default handling and type conversion.

        Shared logic for get() and aget().

        Args:
            key: Config key (for logging/error messages)
            config: DynamicConfig record or None if not found
            default: Default value if config is None (None means raise 404)

        Returns:
            Typed value (str, int, float, or bool), or default if not found

        Raises:
            ExtendedHTTPException: 404 if config is None and default is None
                                   400 if type conversion fails
        """
        if config is None:
            if default is not None:
                logger.debug(f"Config not found, returning default: {key=}")
                return default

            logger.error(f"Config not found and no default provided: {key=}")
            raise ExtendedHTTPException(
                code=404, message="Config not found", details=f"Configuration key '{key}' does not exist"
            )

        converted_value = cls.convert_value(config.value, config.value_type)
        logger.debug(f"Config retrieved: {key=}, {config.value_type=}, value_length={len(str(converted_value))}")
        return converted_value

    @classmethod
    def _prepare_set(cls, key: str, value: Any, value_type: ConfigValueType) -> str:
        """Validate key format, convert value to string, and verify round-trip conversion.

        Shared validation logic for set() and aset().

        Args:
            key: Config key (validated for UPPER_SNAKE_CASE)
            value: Raw value to store
            value_type: Target type enum

        Returns:
            String representation of value

        Raises:
            ExtendedHTTPException: 400 if key format or value conversion is invalid
        """
        cls._validate_key_format(key)
        value_str = str(value)
        cls.convert_value(value_str, value_type)  # Validate conversion works
        return value_str

    @classmethod
    def _apply_update(
        cls,
        existing: DynamicConfig,
        value_str: str,
        value_type: ConfigValueType,
        description: Optional[str],
        updated_by: str,
    ) -> None:
        """Mutate an existing DynamicConfig record with new values.

        Shared logic for set() and aset().
        """
        existing.value = value_str
        existing.value_type = value_type
        if description is not None:
            existing.description = description
        existing.update_date = datetime.now(UTC).replace(tzinfo=None)
        existing.updated_by = updated_by

    @classmethod
    def _build_new_config(
        cls,
        key: str,
        value_str: str,
        value_type: ConfigValueType,
        description: Optional[str],
        updated_by: str,
    ) -> DynamicConfig:
        """Create a new DynamicConfig instance.

        Shared logic for set() and aset().
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        return DynamicConfig(
            id=str(uuid4()),
            key=key,
            value=value_str,
            value_type=value_type,
            description=description,
            date=now,
            update_date=now,
            updated_by=updated_by,
        )

    # --- Sync methods ---

    @classmethod
    def get_by_key(cls, key: str) -> Optional[DynamicConfig]:
        """
        Get DynamicConfig model by key (returns model, not converted value).

        Args:
            key: Config key

        Returns:
            DynamicConfig model or None if not found
        """
        logger.debug(f"Getting config model: {key=}")

        with Session(DynamicConfig.get_engine()) as session:
            return session.exec(cls._select_by_key(key)).first()

    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """
        Get config value by key with automatic type conversion.

        Args:
            key: Config key (UPPER_SNAKE_CASE)
            default: Default value if key not found (None means raise exception)

        Returns:
            Typed value (str, int, float, or bool)

        Raises:
            ExtendedHTTPException: 404 if key not found and default is None
                                   400 if type conversion fails
        """
        logger.debug(f"Getting config: {key=}, has_default={default is not None}")

        with Session(DynamicConfig.get_engine()) as session:
            config = session.exec(cls._select_by_key(key)).first()
            return cls._process_get_result(key, config, default)

    @classmethod
    def set(
        cls,
        key: str,
        value: Any,
        value_type: ConfigValueType,
        description: Optional[str],
        user: User,
    ) -> DynamicConfig:
        """
        Set config value with validation (create or update).

        Args:
            key: Config key (UPPER_SNAKE_CASE, validated)
            value: Value to store (converted to string)
            value_type: Type enum (STRING, INT, FLOAT, BOOL)
            description: Optional description
            user: Super-admin user (for updated_by tracking)

        Returns:
            Created/updated DynamicConfig instance

        Raises:
            ExtendedHTTPException: 400 for validation errors
                                   403 if user not super-admin
        """
        value_str = cls._prepare_set(key, value, value_type)

        logger.info(f"Setting config: {key=}, {value_type=}, {user.id=}")

        with Session(DynamicConfig.get_engine()) as session:
            existing = session.exec(cls._select_by_key(key)).first()

            if existing:
                cls._apply_update(existing, value_str, value_type, description, user.id)
                session.add(existing)
                session.commit()
                session.refresh(existing)
                logger.info(f"Config updated: {key=}, {existing.id=}")
                return existing

            config = cls._build_new_config(key, value_str, value_type, description, user.id)
            session.add(config)
            session.commit()
            session.refresh(config)
            logger.info(f"Config created: {key=}, {config.id=}")
            return config

    # --- Async methods ---

    @classmethod
    async def aget_by_key(cls, key: str) -> Optional[DynamicConfig]:
        """
        Async version of get_by_key.

        Args:
            key: Config key

        Returns:
            DynamicConfig model or None if not found
        """
        logger.debug(f"Getting config model (async): {key=}")

        async with get_async_session() as session:
            result = await session.execute(cls._select_by_key(key))
            return result.scalars().first()

    @classmethod
    async def aget(cls, key: str, default: Any = None) -> Any:
        """
        Async version of get — config value by key with automatic type conversion.

        Args:
            key: Config key (UPPER_SNAKE_CASE)
            default: Default value if key not found (None means raise exception)

        Returns:
            Typed value (str, int, float, or bool)

        Raises:
            ExtendedHTTPException: 404 if key not found and default is None
                                   400 if type conversion fails
        """
        logger.debug(f"Getting config (async): {key=}, has_default={default is not None}")

        async with get_async_session() as session:
            result = await session.execute(cls._select_by_key(key))
            config = result.scalars().first()
            return cls._process_get_result(key, config, default)

    @classmethod
    async def aset(
        cls,
        key: str,
        value: Any,
        value_type: ConfigValueType,
        description: Optional[str],
        updated_by: str,
    ) -> DynamicConfig:
        """
        Async version of set — upsert config value with validation.

        Args:
            key: Config key (UPPER_SNAKE_CASE, validated)
            value: Value to store (converted to string)
            value_type: Type enum (STRING, INT, FLOAT, BOOL)
            description: Optional description
            updated_by: Identifier for audit tracking (user ID or service name)

        Returns:
            Created/updated DynamicConfig instance

        Raises:
            ExtendedHTTPException: 400 for validation errors
        """
        value_str = cls._prepare_set(key, value, value_type)

        logger.info(f"Setting config (async): {key=}, {value_type=}, {updated_by=}")

        async with get_async_session() as session:
            result = await session.execute(cls._select_by_key(key))
            existing = result.scalars().first()

            if existing:
                cls._apply_update(existing, value_str, value_type, description, updated_by)
                session.add(existing)
                await session.commit()
                await session.refresh(existing)
                logger.info(f"Config updated (async): {key=}, {existing.id=}")
                return existing

            config = cls._build_new_config(key, value_str, value_type, description, updated_by)
            session.add(config)
            await session.commit()
            await session.refresh(config)
            logger.info(f"Config created (async): {key=}, {config.id=}")
            return config

    # --- Other methods ---

    @classmethod
    def delete(cls, key: str, user: User) -> None:
        """
        Delete config by key.

        Args:
            key: Config key
            user: Super-admin user

        Raises:
            ExtendedHTTPException: 404 if key not found
                                   403 if user not super-admin
        """
        cls._validate_super_admin(user)

        logger.info(f"Deleting config: {key=}, {user.id=}")

        with Session(DynamicConfig.get_engine()) as session:
            config = session.exec(cls._select_by_key(key)).first()

            if config is None:
                logger.error(f"Config not found for deletion: {key=}")
                raise ExtendedHTTPException(
                    code=404, message="Config not found", details=f"Configuration key '{key}' does not exist"
                )

            # Audit log before deletion (config details preserved in logs)
            logger.warning(
                f"CONFIG DELETION AUDIT: {key=}, config_id={config.id}, "
                f"value={config.value}, value_type={config.value_type.value}, "
                f"deleted_by={user.id}, user_name={user.name}"
            )

            session.delete(config)
            session.commit()
            logger.info(f"Config deleted: {key=}, config_id={config.id}")

    @classmethod
    def list_all(cls) -> List[DynamicConfig]:
        """
        List all dynamic configs ordered by key.

        Returns:
            List of all DynamicConfig entries
        """
        logger.debug("Listing all configs")

        with Session(DynamicConfig.get_engine()) as session:
            statement = select(DynamicConfig).order_by(DynamicConfig.key.asc())
            configs = session.exec(statement).all()
            logger.debug(f"Found {len(configs)} configs")
            return list(configs)

    @classmethod
    def get_typed_value[T: (str, int, float, bool)](
        cls, key: str, expected_type: Type[T], default: T | None = None
    ) -> T | None:
        """
        Get config value with explicit type checking.

        Args:
            key: Config key
            expected_type: Expected Python type (str, int, float, bool)
            default: Default value if key not found

        Returns:
            Value as expected_type, or None if not found and default is None

        Raises:
            ExtendedHTTPException: 400 if stored type doesn't match expected_type
                                   404 if key not found and no default
        """
        value = cls.get(key, default)

        if value is None:
            return None

        if not isinstance(value, expected_type):
            actual_type = type(value).__name__
            expected_type_name = expected_type.__name__
            logger.error(f"Type mismatch: {key=}, {actual_type=}, {expected_type_name=}")
            raise ExtendedHTTPException(
                code=400,
                message="Type mismatch",
                details=f"Configuration '{key}' is stored as {actual_type}, but expected {expected_type_name}",
            )

        return value

    @classmethod
    def get_typed_value_safe[T: (str, int, float, bool)](
        cls, key: str, expected_type: Type[T], default: T | None = None
    ) -> T | None:
        """
        Get config value with explicit type checking and fall back to the provided default on any error.

        Args:
            key: Config key
            expected_type: Expected Python type (str, int, float, bool)
            default: Value returned when lookup or conversion fails

        Returns:
            Value as expected_type, or the provided default when retrieval fails
        """
        try:
            return cls.get_typed_value(key, expected_type, default)
        except Exception as error:
            logger.warning(
                f"Failed to get typed config value. "
                f"{key=}, expected_type={expected_type.__name__}, returning_default={default!r}, error={error}"
            )
            return default

    @classmethod
    def get_bool_value_safe(cls, key: str, default: bool = False) -> bool:
        """
        Get boolean config value and fall back to the provided default on any error.

        Args:
            key: Config key
            default: Value returned when lookup or conversion fails

        Returns:
            Boolean config value or the provided default
        """
        return cls.get_typed_value_safe(key, bool, default=default)
