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
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from codemie.core.hosted_applications.identity import VerifiedIdentityContext
from codemie.core.hosted_applications.validation import inspect_bundle
from codemie.rest_api.models.category import Category
from codemie.rest_api.models.permission import Permission
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
    "2394f0863c6a_add_hosted_application_categories.py",
    "6158f5209f50_add_hosted_version_preview_pointer.py",
)

# Two shared-platform categories seeded for every test; later tasks that exercise hosted application categories
# use these ids directly instead of inventing their own.
CATEGORY_A = "category-a"
CATEGORY_B = "category-b"
SEEDED_CATEGORIES = {CATEGORY_A: "Category A", CATEGORY_B: "Category B"}


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


@contextmanager
def sync_migration_connection(url, schema):
    """A sync, search-pathed connection for installing or stepping the hosted migrations.

    Alembic's raw, unsplit multi-statement DDL (``f8d24a6c0b17``'s ``SHARE_LINKS_DDL``, the permissions ``DO $$``
    block) cannot run through asyncpg: PostgreSQL's extended query protocol, which SQLAlchemy's asyncpg dialect
    always uses, rejects a PREPARE containing more than one statement ("cannot insert multiple commands into a
    prepared statement"). psycopg2 uses the simple protocol and has no such limit, so migrations run against a
    plain sync engine on the same schema; the async engine is only for the tests' own ORM-level reads and writes.
    """
    engine = create_engine(
        url.replace("+asyncpg", "+psycopg2"), connect_args={"options": f"-c search_path={schema},public"}
    )
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def install_migrations(connection, migrations):
    """Run every migration's ``upgrade()`` against ``connection``, inside one MigrationContext-owned transaction."""
    context = MigrationContext.configure(connection)
    with context.begin_transaction():
        for migration in migrations:
            migration.op = Operations(context)
            migration.upgrade()


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
    # Installed here, into ``public`` (the admin connection's default search_path), so it is visible from every
    # per-test schema. Left unqualified, ``e3b7c1a95d42``'s own ``CREATE EXTENSION IF NOT EXISTS pg_trgm`` would
    # install it into the test's random schema instead, which a parity test's own separate ``hosted_model_*``
    # schema (also ``{schema},public``) cannot see, and which the fixture's teardown (``DROP SCHEMA ... CASCADE``)
    # would destroy anyway. In its own transaction so a concurrent (e.g. xdist) peer's identical, equally
    # unqualified create cannot abort the schema/enum work above; "IF NOT EXISTS" does not fully guard against
    # that race (both sides can see "not yet created" before either commits), so a losing side's duplicate-key
    # error is swallowed once the extension is confirmed to exist.
    try:
        async with admin.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    except IntegrityError:
        async with admin.connect() as connection:
            exists = (await connection.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"))).first()
        if exists is None:
            raise
    migrations = [load_migration(name) for name in HOSTED_MIGRATION]
    with sync_migration_connection(url, schema) as connection:
        install_migrations(connection, migrations)
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema + ",public"}})
    try:
        async with engine.connect() as connection:
            # The access rules read the platform's ``users`` and ``user_projects`` tables; category checks read
            # the shared ``categories`` table; named-grant reads (``catalog``'s ``_grants``/``apreload``) hit
            # ``permissions`` on every app projection, grant or no grant.
            #
            # ``permissions`` is created from the model, not a migration, and only now, after
            # ``f8d24a6c0b17`` already ran: that migration's own ``ALTER TABLE permissions ADD COLUMN
            # grant_source, ADD COLUMN source_link_id`` is guarded by ``to_regclass('permissions') IS NOT
            # NULL`` and is a no-op while the table doesn't exist yet. The model already declares
            # ``grant_source``/``source_link_id`` plus their CHECK and FK (the columns a migrated platform
            # database has after that migration), so creating it here yields that same final shape directly
            # without the migration's ALTER ever running against it — avoiding a duplicate-column error from
            # doing both. ``grant_world`` (test_access_matrix.py) creates the same table with
            # ``checkfirst=True`` and keeps working: it finds the table already here and no-ops.
            for table in (UserDB.__table__, UserProject.__table__, Category.__table__, Permission.__table__):
                # ``checkfirst``: ``Permission.resource_type`` is ``codemie.resourcetype``, which the admin
                # connection above already created; without it this unconditionally re-issues ``CREATE TYPE``
                # and collides with that existing type.
                await connection.run_sync(table.create, checkfirst=True)
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
            # ``Category.date``/``update_date`` are plain (timezone-naive) ``timestamp`` columns.
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            for category_id, name in SEEDED_CATEGORIES.items():
                await connection.execute(
                    Category.__table__.insert().values(id=category_id, name=name, date=now, update_date=now)
                )
            await connection.commit()
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


def preview_jpeg(width=64, height=48):
    """A small real baseline JPEG, the kind of picture the validator's capture returns."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height), (40, 90, 160)).save(buffer, "JPEG", quality=80)
    return buffer.getvalue()


class TrustedValidatorSeam:
    def __init__(self, root):
        self.root = root
        self.rejection = None
        self.available = True
        # The picture ``capture`` describes, or None for "no picture". ``preview_changes`` replaces fields of the
        # answer (a wrong digest, size or dimension); ``capture_error`` and ``preview_pull_error`` are raised.
        self.preview = None
        self.preview_changes = {}
        self.capture_error = None
        self.preview_pull_error = None
        self.capture_calls = []
        # What ``capture`` answers when there is no picture: a validator reason, "unknown" or "failed".
        self.no_picture_reason = "no_picture"
        # One scanner answer per seam, so two runs of the same upload give the same report bytes.
        self.scanner = {
            "version": "1.5.4",
            "signature_version": "28143",
            "signature_updated_at": datetime.now(timezone.utc).isoformat(),
        }

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
        self.candidate = inspect_bundle(source, kind, self.output)
        yield "a" * 64, self.candidate, dict(self.scanner)

    async def capture(self, identifier, site_root, entrypoint, routing_mode, *, lock_wait, timeout):
        from codemie.core.hosted_applications.binary import dimensions
        from codemie.service.hosted_applications.validator_client import PreviewFile

        self.capture_calls.append(
            {
                "identifier": identifier,
                "site_root": site_root,
                "entrypoint": entrypoint,
                "routing_mode": routing_mode,
                "lock_wait": lock_wait,
                "timeout": timeout,
            }
        )
        if self.capture_error is not None:
            raise self.capture_error
        if self.preview is None:
            return self.no_picture_reason
        width, height = dimensions(self.preview, ".jpg")
        found = PreviewFile(
            len(self.candidate.files), len(self.preview), hashlib.sha256(self.preview).hexdigest(), width, height
        )
        return replace(found, **self.preview_changes)

    async def file_chunks(self, identifier, index, item):
        if index == len(self.candidate.files):
            if self.preview_pull_error is not None:
                raise self.preview_pull_error
            yield self.preview
            return
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
