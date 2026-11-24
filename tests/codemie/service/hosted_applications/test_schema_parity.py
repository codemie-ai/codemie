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

"""The SQLModel entities describe exactly the schema the hosted migrations create."""

import os
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import create_async_engine

from codemie.rest_api.models.hosted_application import HOSTED_APP_MODELS
from codemie.rest_api.models.permission import ShareLink
from codemie.rest_api.models.hosted_application_cover import HostedApplicationCovers
from tests.codemie.service.hosted_applications.conftest import (
    HOSTED_MIGRATION,
    load_migration,
    sync_migration_connection,
)

# Every hosted entity, including covers kept in their own module, against the hosted migration.
MODELS = (*HOSTED_APP_MODELS, HostedApplicationCovers, ShareLink)
TABLES = tuple(sorted(model.__table__.name for model in MODELS))
BACKGROUND_INDEXES = {
    "hosted_application_jobs_failed",
    "hosted_application_jobs_version",
    "hosted_application_attempt_version",
    "hosted_application_version_upload_expiry",
    "hosted_application_operation_created",
    "hosted_applications_purged",
    "hosted_application_credential_binding_expiry",
}


async def describe(engine):
    async with engine.connect() as connection:
        schema = (await connection.execute(text("SELECT current_schema()"))).scalar_one()
        columns = (
            await connection.execute(
                text(
                    "SELECT table_name, column_name, data_type, character_maximum_length, is_nullable, column_default "
                    "FROM information_schema.columns WHERE table_schema = current_schema() "
                    "AND table_name = ANY(:tables) ORDER BY table_name, column_name"
                ),
                {"tables": list(TABLES)},
            )
        ).all()
        constraints = (
            await connection.execute(
                text(
                    "SELECT c.conrelid::regclass::text, c.contype, pg_get_constraintdef(c.oid), c.condeferrable, "
                    "c.condeferred FROM pg_constraint c WHERE c.connamespace = current_schema()::regnamespace "
                    "AND c.conrelid::regclass::text = ANY(:tables)"
                ),
                {"tables": list(TABLES)},
            )
        ).all()
        indexes = (
            await connection.execute(
                text(
                    "SELECT tablename, indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = current_schema() AND tablename = ANY(:tables)"
                ),
                {"tables": list(TABLES)},
            )
        ).all()
    return {
        "columns": [tuple(row) for row in columns],
        "constraints": sorted(tuple(row) for row in constraints),
        "indexes": sorted((table, name, definition.replace(schema + ".", "")) for table, name, definition in indexes),
    }


@pytest.mark.asyncio
async def test_models_match_the_migrated_schema_exactly(database):
    engine, _ = database
    url = os.environ["HOSTED_TEST_DATABASE_URL"]
    schema = "hosted_model_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    modeled_engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema + ",public"}})
    try:
        metadata = MetaData()
        for model in MODELS:
            model.__table__.to_metadata(metadata)
        async with modeled_engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
        migrated, modeled = await describe(engine), await describe(modeled_engine)
        for part in ("columns", "constraints", "indexes"):
            assert modeled[part] == migrated[part], part
        created = {name for _, name, _ in migrated["indexes"]}
        assert created.issuperset(BACKGROUND_INDEXES)
        assert {table for table, *_ in migrated["columns"]} == set(TABLES)
    finally:
        await modeled_engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


HOSTED_CATEGORIES_REVISION = "2394f0863c6a"
HOSTED_PREVIEW_REVISION = "6158f5209f50"
CATEGORIES_COLUMN = "categories"
CATEGORIES_INDEX = "ix_hosted_applications_categories"


def _migrations_by_revision():
    """Every hosted migration module, keyed by its own ``revision`` id rather than by load order."""
    return {module.revision: module for module in (load_migration(name) for name in HOSTED_MIGRATION)}


def test_hosted_migrations_follow_the_platform_head():
    migrations = _migrations_by_revision()
    hosted = migrations["e3b7c1a95d42"]
    sharing = migrations["f8d24a6c0b17"]
    categories = migrations[HOSTED_CATEGORIES_REVISION]
    assert (hosted.revision, hosted.down_revision) == ("e3b7c1a95d42", "0ba28b31fa53")
    assert (sharing.revision, sharing.down_revision) == ("f8d24a6c0b17", "e3b7c1a95d42")
    # Pinned to the platform's actual current head, not a guess: `alembic heads` in src/external/alembic.
    assert (categories.revision, categories.down_revision) == (HOSTED_CATEGORIES_REVISION, "64825fe31931")


@pytest.mark.asyncio
async def test_migrated_schema_has_no_tenant_keys_folders_or_trash(database):
    engine, _ = database
    migrated = await describe(engine)
    columns = {(table, column) for table, column, *_ in migrated["columns"]}
    tables = {table for table, _ in columns}
    forbidden = {"org_id", "issuer", "actor_issuer", "scope", "folder_id"}
    assert not {(table, column) for table, column in columns if column in forbidden}
    assert "application_folders" not in tables
    definitions = " ".join(definition for _, _, definition, *_ in migrated["constraints"])
    assert "trashed" not in definitions
    assert "hosted_applications_trash" not in {name for _, name, _ in migrated["indexes"]}
    foreign_keys = {
        definition for _, _, definition, *_ in migrated["constraints"] if definition.startswith("FOREIGN KEY")
    }
    assert "FOREIGN KEY (app_id) REFERENCES hosted_applications(id) ON DELETE RESTRICT" in foreign_keys
    assert "application_grants" not in tables
    assert not {(table, column) for table, column in columns if column in {"owner_id", "audience"}}
    assert {"project", "created_by", "shared", "is_global"} <= {c for t, c in columns if t == "hosted_applications"}
    assert ("hosted_application_storage_attempts", "quota_principal_id") in columns
    assert (
        "FOREIGN KEY (principal_id, parent_id, parent_kind) REFERENCES "
        "hosted_application_view_sessions(principal_id, id, kind) ON DELETE RESTRICT"
    ) in foreign_keys


PREVIEW_COLUMNS = {"preview_key", "preview_size", "preview_sha256"}


@pytest.mark.asyncio
async def test_hosted_migrations_downgrade_and_upgrade_again(database):
    """Round trip on a sync connection, one commit per revision: the sharing revision needs autocommit."""
    engine, _ = database
    async with engine.connect() as connection:
        schema = (await connection.execute(text("SELECT current_schema()"))).scalar_one()

    migrations = _migrations_by_revision()
    base = migrations["e3b7c1a95d42"]
    share_links = migrations["f8d24a6c0b17"]
    categories = migrations[HOSTED_CATEGORIES_REVISION]
    preview = migrations[HOSTED_PREVIEW_REVISION]

    def column_exists(sync):
        return (
            sync.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'hosted_applications' "
                    "AND column_name = :column"
                ),
                {"column": CATEGORIES_COLUMN},
            ).first()
            is not None
        )

    def preview_columns(sync):
        rows = sync.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'hosted_application_versions'"
            )
        )
        return PREVIEW_COLUMNS & {row[0] for row in rows}

    def index_exists(sync):
        return sync.execute(text(f"SELECT to_regclass('{CATEGORIES_INDEX}')::text")).scalar_one() is not None

    # A sync (psycopg2) connection, not the fixture's asyncpg one: ``f8d24a6c0b17``'s unsplit multi-statement DDL
    # cannot run through asyncpg's extended protocol (see ``sync_migration_connection``'s docstring).
    with sync_migration_connection(os.environ["HOSTED_TEST_DATABASE_URL"], schema) as sync:
        context = MigrationContext.configure(sync)
        for migration in (base, share_links, categories, preview):
            migration.op = Operations(context)

        with context.begin_transaction():
            assert column_exists(sync)
            assert index_exists(sync)

            assert preview_columns(sync) == PREVIEW_COLUMNS
            preview.downgrade()
            assert preview_columns(sync) == set()
            preview.upgrade()
            assert preview_columns(sync) == PREVIEW_COLUMNS

            preview.downgrade()
            categories.downgrade()
            assert not column_exists(sync)
            assert not index_exists(sync)
            categories.upgrade()
            preview.upgrade()
            assert column_exists(sync)
            assert index_exists(sync)

            preview.downgrade()
            categories.downgrade()
            share_links.downgrade()
            share_links.upgrade()
            categories.upgrade()
            preview.upgrade()

            preview.downgrade()
            categories.downgrade()
            share_links.downgrade()
            base.downgrade()
            for table in TABLES:
                assert sync.execute(text(f"SELECT to_regclass('{table}')::text")).scalar_one() is None
            base.upgrade()
            share_links.upgrade()
            categories.upgrade()
            preview.upgrade()
            assert preview_columns(sync) == PREVIEW_COLUMNS
            assert sync.execute(text("SELECT to_regclass('hosted_application_covers')::text")).scalar_one()
