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

"""Codemie adapter for enterprise Keycloak migration coordinator.

Implements MigrationDeps protocol using codemie repositories, services,
and database infrastructure. The business logic lives in codemie-enterprise;
this module wires up the concrete dependencies.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text

from codemie.clients.postgres import PostgresClient, get_async_session
from codemie.configs import config
from codemie.repository.application_repository import application_repository
from codemie.repository.user_kb_repository import user_kb_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_repository import user_repository
from codemie.rest_api.models.dynamic_config import ConfigValueType
from codemie.rest_api.models.user_management import UserDB
from codemie.service.dynamic_config_service import DynamicConfigService
from codemie.service.project.personal_project_service import PersonalProjectService

# Advisory lock ID for Postgres (stable hash of "keycloak_migration")
_LOCK_ID = 7734269302

personal_project_service = PersonalProjectService()

_MIGRATION_UPDATED_BY = "keycloak_migration_service"


class CodemieMigrationDeps:
    """Implementation of MigrationDeps protocol using codemie repositories."""

    def get_session(self):
        """Return async session context manager."""
        return get_async_session()

    @asynccontextmanager
    async def advisory_lock(self):
        """Acquire Postgres advisory lock. Yields True if acquired (leader)."""
        engine = PostgresClient.get_async_engine()
        async with engine.connect() as lock_conn:
            result = await lock_conn.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": _LOCK_ID})
            acquired = bool(result.scalar())
            try:
                yield acquired
            finally:
                if acquired:
                    await lock_conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": _LOCK_ID})

    async def read_config(self, key: str) -> str | None:
        """Read dynamic_config value by key via DynamicConfigService."""
        record = await DynamicConfigService.aget_by_key(key)
        return record.value if record else None

    async def write_config(self, key: str, value: str) -> None:
        """Upsert dynamic_config record via DynamicConfigService."""
        await DynamicConfigService.aset(key, value, ConfigValueType.STRING, None, _MIGRATION_UPDATED_BY)

    async def get_user_by_id(self, session: Any, user_id: str) -> Any | None:
        """Look up user by ID."""
        return await user_repository.aget_by_id(session, user_id)

    async def get_user_by_email(self, session: Any, email: str) -> Any | None:
        """Look up user by email."""
        return await user_repository.aget_by_email(session, email)

    async def create_user(self, session: Any, user_data: dict[str, Any]) -> None:
        """Create a UserDB record from the provided data dict."""
        db_user = UserDB(**user_data)
        await user_repository.acreate(session, db_user)

    async def add_user_project(self, session: Any, user_id: str, project_name: str, is_admin: bool) -> None:
        """Add user-project association."""
        await user_project_repository.aadd_project(session, user_id, project_name, is_admin)

    async def add_user_kb(self, session: Any, user_id: str, kb_name: str) -> None:
        """Add user-knowledge base association."""
        await user_kb_repository.aadd_kb(session, user_id, kb_name)

    async def get_or_create_application(self, session: Any, name: str) -> Any:
        """Get or create Application record by name."""
        return await application_repository.aget_or_create(session, name)

    async def ensure_personal_project(self, user_id: str, email: str) -> None:
        """Ensure personal project exists for the user."""
        await personal_project_service.ensure_personal_project_async(user_id, email)


async def run_keycloak_migration() -> None:
    """Build dependencies and run the enterprise migration coordinator."""
    from codemie_enterprise.migration import KeycloakMigrationCoordinator, MigrationConfig

    migration_config = MigrationConfig(
        keycloak_admin_url=config.KEYCLOAK_ADMIN_URL,
        keycloak_admin_realm=config.KEYCLOAK_ADMIN_REALM,
        keycloak_admin_client_id=config.KEYCLOAK_ADMIN_CLIENT_ID,
        keycloak_admin_client_secret=config.KEYCLOAK_ADMIN_CLIENT_SECRET,
        batch_size=config.KEYCLOAK_MIGRATION_BATCH_SIZE,
        lock_timeout_minutes=config.KEYCLOAK_MIGRATION_LOCK_TIMEOUT_MINUTES,
        wait_interval_seconds=config.KEYCLOAK_MIGRATION_WAIT_INTERVAL_SECONDS,
        admin_user_id=config.ADMIN_USER_ID,
        admin_role_name=config.ADMIN_ROLE_NAME,
        user_project_limit=config.USER_PROJECT_LIMIT,
    )

    deps = CodemieMigrationDeps()
    coordinator = KeycloakMigrationCoordinator(migration_config, deps)
    await coordinator.run()
