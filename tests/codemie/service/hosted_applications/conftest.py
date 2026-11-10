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

"""Real PostgreSQL transactions, not an in-memory substitute for ACL/locking tests."""

import hashlib
import importlib.util
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from codemie.core.hosted_applications.identity import VerifiedIdentityContext
from codemie.core.hosted_applications.validation import inspect_bundle
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.rest_api.security.hosted_applications import HostedActor
from codemie.rest_api.security.user import User
from codemie.repository.hosted_applications.filesystem import HostedFileStorage
from codemie.service.hosted_applications.worker import ValidationWorker
from codemie.repository.hosted_applications.repository import HostedApplicationRepository
from codemie.service.hosted_applications.catalog import HostedApplicationCatalog


class TestDirectory:
    __test__ = False

    def __init__(self):
        self.inactive = set()

    async def require(self, context):
        return await self.resolve(context.principal_id)

    async def resolve(self, principal):
        from codemie.core.hosted_applications.errors import HostedApplicationError

        if not self._eligible(principal):
            raise HostedApplicationError("NOT_FOUND", "Application not found", 404)
        return {"principal_id": principal, "display_name": principal}

    def _eligible(self, principal):
        return principal not in self.inactive

    # Batched lookup used by catalog and sharing. No ``cached()`` probe on purpose: shared fixtures
    # keep the live-directory path that the launch preflight tests intercept.
    async def resolve_many(self, principals, *, contact=False):
        return {
            principal: {"principal_id": principal, "display_name": principal}
            for principal in dict.fromkeys(principals)
            if self._eligible(principal)
        }


# Every test principal is a member of this project (``User`` defaults to it), so ``AppCreate(project=PROJECT)``
# and shared reads work without further setup.
PROJECT = "demo"
PRINCIPALS = ("owner", "viewer", "other-owner")


@pytest.fixture
def identity():
    """Factory of the actor that management services take: the platform user plus the recorded sign-in."""

    def make(principal="owner", **changes):
        now = datetime.now(timezone.utc)
        sign_in = VerifiedIdentityContext(
            principal,
            "local_jwt",
            now - timedelta(seconds=10),
            now + timedelta(hours=1),
            b"a" * 32,
            **changes,
        )
        user = User(id=principal, username=principal, name=principal, project_names=[PROJECT])
        # ENV=local forces is_admin on at construction; a test principal is an ordinary member.
        user.is_admin = False
        return HostedActor(user, sign_in)

    return make


MIGRATIONS = Path(__file__).parents[4] / "src/external/alembic/versions"
# The feature's revisions in chain order; the fixture installs exactly what a fresh deployment runs.
HOSTED_MIGRATION = (
    "e3b7c1a95d42_add_hosted_applications.py",
    "f8d24a6c0b17_platform_sharing_grants_and_links.py",
)


RESOURCE_TYPE_ENUM = """
DO $$
BEGIN
    IF to_regtype('codemie.resourcetype') IS NULL THEN
        CREATE SCHEMA IF NOT EXISTS codemie;
        CREATE TYPE codemie.resourcetype AS ENUM ('DATASOURCE', 'ASSISTANT', 'HOSTED_APPLICATION');
    END IF;
END $$;
"""


def load_migration(name):
    spec = importlib.util.spec_from_file_location("hosted_test_" + name.split("_", 1)[0], MIGRATIONS / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture
async def database():
    url = os.environ.get("HOSTED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set HOSTED_TEST_DATABASE_URL to an isolated PostgreSQL database")
    schema = "hosted_test_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        # ``share_links.resource_type`` is the platform's ``codemie.resourcetype`` (as on ``permissions``); a bare
        # test database gets it here, a migrated platform database already has it.
        await connection.execute(text(RESOURCE_TYPE_ENUM))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema + ",public"}})
    try:
        migrations = [load_migration(name) for name in HOSTED_MIGRATION]
        async with engine.begin() as connection:

            def install(sync):
                for migration in migrations:
                    migration.op = Operations(MigrationContext.configure(sync))
                    migration.upgrade()

            await connection.run_sync(install)
            # The access rules read the platform's ``users`` and ``user_projects`` tables.
            for table in (UserDB.__table__, UserProject.__table__):
                await connection.run_sync(table.create)
            for principal in PRINCIPALS:
                await connection.execute(
                    UserDB.__table__.insert().values(
                        id=principal,
                        username=principal,
                        email=f"{principal}@example.com",
                        user_type="regular",
                        is_active=True,
                        is_admin=False,
                        is_maintainer=False,
                        is_auditor=False,
                        auth_source="local",
                        email_verified=False,
                    )
                )
                await connection.execute(
                    UserProject.__table__.insert().values(
                        id=uuid4().hex,
                        user_id=principal,
                        project_name=PROJECT,
                        is_project_admin=False,
                        is_default=False,
                    )
                )
        yield engine, migrations[0]
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest_asyncio.fixture
async def catalog(database):
    engine, _ = database

    @asynccontextmanager
    async def sessions():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    directory = TestDirectory()
    repository = HostedApplicationRepository(sessions, secret=b"s" * 48, quota_bytes=2 * 1024**3)
    return HostedApplicationCatalog(repository, directory), repository, directory


class TrustedValidatorSeam:
    def __init__(self, root):
        self.root = root
        self.rejection = None
        self.available = True

    async def ready(self):
        return self.available

    @asynccontextmanager
    async def inspect(self, chunks, *, size, digest, kind):
        if self.rejection:
            raise self.rejection
        source = self.root / (uuid4().hex + ".html")
        source.write_bytes(b"".join([part async for part in chunks]))
        assert source.stat().st_size == size and hashlib.sha256(source.read_bytes()).digest() == digest
        self.output = self.root / uuid4().hex
        candidate = inspect_bundle(source, kind, self.output)
        yield (
            "a" * 64,
            candidate,
            {
                "version": "1.5.4",
                "signature_version": "28143",
                "signature_updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def file_chunks(self, identifier, index, item):
        yield (self.output / item.path).read_bytes()


@pytest.fixture
def processing(catalog, tmp_path):
    service, repo, directory = catalog
    service.storage = HostedFileStorage(tmp_path / "private-storage")
    validator = TrustedValidatorSeam(tmp_path)
    return service, repo, directory, ValidationWorker(repo, service.storage, validator), validator


@pytest.fixture
def launch(processing):
    service, _, directory, worker, _ = processing
    from codemie.core.hosted_applications.settings import HostedSettings, IdentityAuthority
    from codemie.service.hosted_applications.launch import HostedApplicationLaunchService

    settings = HostedSettings(
        enabled=True,
        disabled_reason=None,
        content_domain="apps.localhost",
        content_port=8081,
        local=True,
        ui_origins=("http://localhost:5173",),
        secret=b"s" * 48,
        authority=IdentityAuthority(),
    )
    return HostedApplicationLaunchService(service, settings), service, worker, directory
