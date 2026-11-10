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

"""AC7 persona x access-path matrix: open-by-id, the three lists, launch and gateway authorize agree.

Every path runs its real SQL (``readable`` / ``manageable`` from the access module, the list scopes, and the single
``_load`` statement of the gateway) on in-memory SQLite against the real ``users``, ``user_projects`` and hosted
tables. The hosted tables are created from their columns only: their CHECK constraints use PostgreSQL syntax. Only the
management transaction (locking, idempotency, audit) and the version-provenance check are stand-ins.

The expected outcome of each path comes from an oracle written here from the spec, not from the module under test.
The persona "viewer of an app whose creator is deactivated" is the project member under ``author="deactivated"``.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import bindparam, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from codemie.configs import config
from codemie.core.ability import Action
from codemie.core.hosted_applications.access import manageable
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.identity import VerifiedIdentityContext
from codemie.core.hosted_applications.protocol import etag
from codemie.core.hosted_applications.settings import HostedSettings, IdentityAuthority
from codemie.repository.hosted_applications.repository import as_uuid, not_found
from codemie.rest_api.models.hosted_application import (
    HostedApplicationOperations as Operation,
    HostedApplicationVersions as Version,
    HostedApplicationStorageAttempts as Attempt,
    HostedApplicationUserState as State,
    HostedApplicationViewSessions as Credential,
    HostedApplications as App,
)
from codemie.rest_api.models.hosted_application_requests import AppPatch, VersionSelection
from codemie.rest_api.models.permission import Permission, PrincipalType, ResourceType
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.rest_api.security.hosted_applications import HostedActor
from codemie.rest_api.security.user import User
from codemie.service.hosted_applications.catalog import HostedApplicationCatalog
from codemie.service.hosted_applications.launch import HostedApplicationLaunchService
from tests.codemie.service.hosted_applications.test_worker import queued

TEAM, OTHER = "team-a", "team-b"

# persona -> (users.id, {project: is_project_admin}, users flags)
PERSONAS = {
    "creator": ("u-creator", {TEAM: False}, {}),
    "project_admin": ("u-padmin", {TEAM: True}, {}),
    "platform_admin": ("u-admin", {}, {"is_admin": True}),
    "maintainer": ("u-maintainer", {}, {"is_maintainer": True}),
    "member": ("u-member", {TEAM: False}, {}),
    "marketplace_viewer": ("u-market", {OTHER: False}, {}),
    "outsider": ("u-outsider", {}, {}),
    "service_account": ("u-service", {TEAM: False}, {"user_type": "service_account"}),
    "external": ("u-external", {TEAM: False}, {"user_type": "external"}),
    "deactivated": ("u-gone", {TEAM: False}, {"is_active": False}),
}

PLATFORM_ADMINS = ("platform_admin", "maintainer")

# case -> (initial shared / is_global, transitions applied one after another)
CASES = {
    "initial": ({"shared": True, "is_global": True}, []),
    "shared_toggle": ({"shared": True, "is_global": False}, ["shared_off", "shared_on"]),
    "shared_on": ({"shared": False, "is_global": True}, ["shared_on"]),
    "publish": ({"shared": True, "is_global": False}, ["publish"]),
    "unpublish": ({"shared": True, "is_global": True}, ["unpublish"]),
    "suspend": ({"shared": True, "is_global": True}, ["suspend"]),
    "move": ({"shared": True, "is_global": False}, ["move"]),
    "removal": ({"shared": True, "is_global": False}, ["remove_member"]),
}

SETTINGS = HostedSettings(
    enabled=True,
    disabled_reason=None,
    content_domain="apps.localhost",
    content_port=8081,
    local=True,
    ui_origins=("http://localhost:5173",),
    secret=b"s" * 48,
    authority=IdentityAuthority(),
)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


def utcnow():
    return datetime.now(timezone.utc)


class FakeTx:
    """The real SQL session plus stand-ins for the quota lock, idempotency and audit of the management transaction."""

    def __init__(self, session, repository, user, identity):
        self.session, self.repository, self.user, self.identity = session, repository, user, identity
        self.now = utcnow()
        self.quota = None

    async def lock_quota(self, principal_id):
        if await self.session.get(State, principal_id) is None:
            self.session.add(State(principal_id=principal_id, byte_limit=1, revision=1, updated_at=self.now))
            await self.session.flush()
        self.quota = principal_id

    async def operation(self, action, target, payload, key=None, *, app_id=None):
        return SimpleNamespace(app_id=app_id), None

    async def finish(self, operation, response, status=200, *, details=None):
        return response

    async def managed_app(self, app_id, user, *, lock=False, states=("active",)):
        user_id = bindparam("manager_id", user.id, type_=sa.Text)
        query = select(App).where(
            App.id == as_uuid(app_id), App.lifecycle_state.in_(states), manageable(App.__table__, user_id)
        )
        app = (await self.session.execute(query)).scalar_one_or_none()
        if app is None:
            raise not_found()
        return app


class FakeRepository:
    secret = b"s" * 48
    quota_bytes = 1

    def __init__(self, engine):
        self.engine = engine

    @asynccontextmanager
    async def sessions(self):
        async with AsyncSession(self.engine, expire_on_commit=False) as session:
            yield session

    @asynccontextmanager
    async def transaction(self, user, recorded_identity=None, *, write=False, authorized_until=None):
        async with self.sessions() as session, session.begin():
            yield FakeTx(session, self, user, recorded_identity)


def bare_copy(table, metadata):
    """The table's columns without its PostgreSQL CHECK constraints and partial indexes."""
    return sa.Table(table.name, metadata, *[column._copy() for column in table.columns])


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    @sa.event.listens_for(engine.sync_engine, "connect")
    def _functions(dbapi_connection, _):
        dbapi_connection.create_function("char_length", 1, lambda value: None if value is None else len(value))
        dbapi_connection.create_function(
            "clock_timestamp", 0, lambda: utcnow().replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")
        )

    metadata = sa.MetaData()
    for table in (State, App, Version, Attempt, Credential, Operation, Permission):
        bare_copy(table.__table__, metadata)
    async with engine.begin() as connection:
        for table in (UserDB.__table__, UserProject.__table__):
            await connection.run_sync(table.create)
        await connection.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def world(engine, monkeypatch):
    """Services over the shared database, with every persona's ``users`` and ``user_projects`` rows."""
    catalog = HostedApplicationCatalog(FakeRepository(engine), directory=None)

    async def eligible(tx, targets, *, activation=False, keyed_by_version=False):
        # Stand-in for the version provenance check: every stored version of an active app is eligible.
        result = {}
        for app, version_id in targets:
            version = await tx.session.get(Version, version_id) if version_id else None
            if version is not None and app.lifecycle_state == "active":
                result[version.id if keyed_by_version else app.id] = version
        return result

    async def no_covers(tx, apps):
        return set()

    monkeypatch.setattr(catalog, "_eligible_versions", eligible)
    monkeypatch.setattr(catalog, "_covered", no_covers)
    async with engine.begin() as connection:
        for user_id, memberships, flags in PERSONAS.values():
            await connection.execute(
                UserDB.__table__.insert().values(
                    id=user_id,
                    username=user_id,
                    email=f"{user_id}@example.com",
                    user_type=flags.get("user_type", "regular"),
                    is_active=flags.get("is_active", True),
                    is_admin=flags.get("is_admin", False),
                    is_maintainer=flags.get("is_maintainer", False),
                    is_auditor=False,
                    auth_source="local",
                    email_verified=False,
                )
            )
            for project, admin in memberships.items():
                await connection.execute(
                    UserProject.__table__.insert().values(
                        id=str(uuid4()), user_id=user_id, project_name=project, is_project_admin=admin, is_default=False
                    )
                )
    return SimpleNamespace(engine=engine, catalog=catalog, launch=HostedApplicationLaunchService(catalog, SETTINGS))


async def actor(engine, persona):
    """The platform user as the platform loads it for a request: from ``users`` and ``user_projects`` now."""
    user_id = PERSONAS[persona][0]
    async with engine.connect() as connection:
        row = (await connection.execute(select(UserDB.__table__).where(UserDB.__table__.c.id == user_id))).one()
        members = (
            await connection.execute(
                select(UserProject.__table__.c.project_name, UserProject.__table__.c.is_project_admin).where(
                    UserProject.__table__.c.user_id == user_id
                )
            )
        ).all()
    user = User(
        id=user_id,
        username=row.username,
        name=row.username,
        project_names=[member.project_name for member in members],
        admin_project_names=[member.project_name for member in members if member.is_project_admin],
        user_type=row.user_type,
        is_maintainer=row.is_maintainer,
    )
    # ENV=local forces is_admin on at construction; the users row decides here.
    user.is_admin = bool(row.is_admin or row.is_maintainer)
    now = utcnow()
    # A fresh sign-in per call, so every launch starts its own auth context.
    sign_in = VerifiedIdentityContext(
        user_id, "local_jwt", now - timedelta(seconds=10), now + timedelta(hours=1), uuid4().bytes * 2
    )
    return HostedActor(user, sign_in)


async def seed_app(engine, author, *, shared, is_global, active=True):
    app_id, version_id, attempt_id, now = uuid4(), uuid4(), uuid4(), utcnow()
    author_id = PERSONAS[author][0]
    async with engine.begin() as connection:
        await connection.execute(
            App.__table__.insert().values(
                id=app_id,
                project=TEAM,
                created_by={"id": author_id, "username": author_id, "name": author_id},
                name="Delivery map",
                description="A useful visual",
                search_text="delivery map",
                shared=shared,
                is_global=is_global,
                artifact_kind="html",
                selected_version_id=version_id,
                active_version_id=version_id if active else None,
                security_state="clear",
                lifecycle_state="active",
                revision=1,
                access_revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            Version.__table__.insert().values(
                id=version_id,
                app_id=app_id,
                sequence=1,
                original_filename="visual.html",
                artifact_kind="html",
                expected_bytes=10,
                upload_id=uuid4(),
                upload_expires_at=now,
                accepted_attempt_id=attempt_id,
                validation_state="accepted",
                created_at=now,
            )
        )
        await connection.execute(
            Attempt.__table__.insert().values(
                id=attempt_id,
                quota_principal_id=author_id,
                app_id=app_id,
                version_id=version_id,
                kind="validation",
                provider="filesystem",
                namespace="ns",
                prefix="p",
                writer_id=uuid4(),
                writer_fence=uuid4(),
                state="stored",
                write_deadline=now,
                byte_upper_bound=100,
                object_limit=1,
                held_bytes=0,
                created_at=now,
                updated_at=now,
            )
        )
    return app_id, version_id


async def app_row(engine, app_id):
    async with engine.connect() as connection:
        return (await connection.execute(select(App.__table__).where(App.__table__.c.id == app_id))).one()


async def memberships(engine, persona):
    user_id = PERSONAS[persona][0]
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                select(UserProject.__table__.c.project_name, UserProject.__table__.c.is_project_admin).where(
                    UserProject.__table__.c.user_id == user_id
                )
            )
        ).all()
    return {row.project_name: row.is_project_admin for row in rows}


async def oracle(engine, persona, app_id, author):
    """(manage, read) from the spec: managers are the creator, project admins and platform admins (maintainers
    included); viewers are members of the project when ``shared`` is on, or every internal user when the app is
    global, never while suspended. Only eligible users (active, not external) count, whoever created the app."""
    _, _, flags = PERSONAS[persona]
    app = await app_row(engine, app_id)
    member_of = await memberships(engine, persona)
    eligible = flags.get("is_active", True) and flags.get("user_type") != "external"
    manage = eligible and (
        persona == author
        or member_of.get(app.project) is True
        or bool(flags.get("is_admin") or flags.get("is_maintainer"))
    )
    view = (
        eligible and app.security_state != "suspended" and (app.is_global or (app.shared and app.project in member_of))
    )
    return manage, manage or view


async def outcome(call):
    try:
        await call
    except HostedApplicationError as error:
        assert error.status in (401, 404), (error.code, error.status)
        return False
    return True


async def view_session(world, persona, app_id, version_id, purpose):
    ticket = await world.launch.issue(await actor(world.engine, persona), app_id, purpose=purpose)
    cookie, _ = await world.launch.consume(ticket["ticket"], world.launch.host(version_id))
    return cookie


async def check_paths(world, app_id, version_id, author, sessions, step):
    """Every path agrees with the oracle for every persona; earlier sessions follow the current rule."""
    catalog, engine = world.catalog, world.engine
    app = await app_row(engine, app_id)
    for persona in PERSONAS:
        manage, read = await oracle(engine, persona, app_id, author)
        where = f"{persona} after {step}"
        current = await actor(engine, persona)
        assert await outcome(catalog.get_app(current, app_id)) == read, where
        listed = {
            scope: str(app_id) in [item["id"] for item in (await catalog.list_apps(current, scope=scope))["items"]]
            for scope in ("mine", "project", "marketplace")
        }
        assert listed == {
            "mine": read and persona == author,
            # A platform admin or maintainer lists every project's apps (CORE-18), as for skills.
            "project": read and (app.project in current.user.project_names or persona in PLATFORM_ADMINS),
            "marketplace": read and bool(app.is_global),
        }, where
        for purpose, allowed in (("published_view", read), ("owner_preview", manage)):
            launched = await outcome(view_session(world, persona, app_id, version_id, purpose))
            assert launched == allowed, (where, purpose)
            if (persona, purpose) in sessions:
                still = await outcome(world.launch.authorize(sessions[persona, purpose], version_id))
                assert still == allowed, (where, purpose, "existing session")


async def transition(world, app_id, step):
    engine, catalog = world.engine, world.catalog
    admin = await actor(engine, "platform_admin")
    row = await app_row(engine, app_id)
    match = etag("app", app_id, row.revision)
    if step in ("shared_off", "shared_on"):
        await catalog.patch_app(admin, app_id, AppPatch(shared=step == "shared_on"), match, str(uuid4()))
    elif step == "move":
        await catalog.patch_app(admin, app_id, AppPatch(project=OTHER), match, str(uuid4()))
    elif step == "publish":
        await catalog.publish(admin, app_id, match, str(uuid4()))
    elif step == "unpublish":
        await catalog.unpublish(admin, app_id, match, str(uuid4()))
    elif step == "suspend":
        # The admin suspend service is reworked separately; its effect on the row is exactly this.
        async with engine.begin() as connection:
            await connection.execute(
                App.__table__.update().where(App.__table__.c.id == app_id).values(security_state="suspended")
            )
    elif step == "remove_member":
        async with engine.begin() as connection:
            await connection.execute(
                UserProject.__table__.delete().where(
                    UserProject.__table__.c.user_id == PERSONAS["member"][0],
                    UserProject.__table__.c.project_name == TEAM,
                )
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("author", ["creator", "deactivated"])
@pytest.mark.parametrize("case", list(CASES))
async def test_every_access_path_agrees_for_every_persona(world, case, author):
    initial, steps = CASES[case]
    app_id, version_id = await seed_app(world.engine, author, **initial)
    sessions = {}
    for persona in PERSONAS:
        manage, read = await oracle(world.engine, persona, app_id, author)
        for purpose, allowed in (("published_view", read), ("owner_preview", manage)):
            if allowed:
                sessions[persona, purpose] = await view_session(world, persona, app_id, version_id, purpose)
    await check_paths(world, app_id, version_id, author, sessions, "start")
    for step in steps:
        await transition(world, app_id, step)
        await check_paths(world, app_id, version_id, author, sessions, step)
    row = await app_row(world.engine, app_id)
    assert row.access_revision == 1, "only delete and purge end sessions by revision"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["shared_on", "publish"])
async def test_widening_access_ends_no_open_session(world, case):
    initial, steps = CASES[case]
    app_id, version_id = await seed_app(world.engine, "creator", **initial)
    cookie = await view_session(
        world, "member" if case == "publish" else "marketplace_viewer", app_id, version_id, "published_view"
    )
    for step in steps:
        await transition(world, app_id, step)
    assert (await world.launch.authorize(cookie, version_id))["version"]["id"] == version_id


@pytest.mark.asyncio
async def test_a_deactivated_creator_does_not_hide_the_app_from_its_viewers(world):
    app_id, version_id = await seed_app(world.engine, "deactivated", shared=True, is_global=False)
    cookie = await view_session(world, "member", app_id, version_id, "published_view")
    allowed = await world.launch.authorize(cookie, version_id)
    assert allowed["viewer_subject"] == PERSONAS["member"][0]


@pytest.mark.asyncio
async def test_delete_ends_open_sessions_through_the_access_revision(world):
    app_id, version_id = await seed_app(world.engine, "creator", shared=True, is_global=False)
    cookie = await view_session(world, "member", app_id, version_id, "published_view")
    async with world.engine.begin() as connection:
        await connection.execute(
            App.__table__.update()
            .where(App.__table__.c.id == app_id)
            .values(access_revision=App.__table__.c.access_revision + 1)
        )
    with pytest.raises(HostedApplicationError):
        await world.launch.authorize(cookie, version_id)


@pytest.mark.asyncio
async def test_launch_without_an_active_version_is_refused_while_details_open(world):
    app_id, version_id = await seed_app(world.engine, "creator", shared=True, is_global=False, active=False)
    member = await actor(world.engine, "member")
    assert (await world.catalog.get_app(member, app_id))["has_active_version"] is False
    with pytest.raises(HostedApplicationError) as refused:
        await world.launch.issue(member, app_id, purpose="published_view")
    assert refused.value.code == "NO_ACTIVE_VERSION"
    # A manager still previews the working version (AC15).
    assert await view_session(world, "creator", app_id, version_id, "owner_preview")


@pytest.mark.asyncio
async def test_managers_preview_a_suspended_app_and_viewers_cannot_launch_it(world):
    app_id, version_id = await seed_app(world.engine, "creator", shared=True, is_global=True)
    preview = await view_session(world, "project_admin", app_id, version_id, "owner_preview")
    view = await view_session(world, "member", app_id, version_id, "published_view")
    await transition(world, app_id, "suspend")
    assert (await world.launch.authorize(preview, version_id))["viewer_subject"] == PERSONAS["project_admin"][0]
    with pytest.raises(HostedApplicationError):
        await world.launch.authorize(view, version_id)
    with pytest.raises(HostedApplicationError):
        await world.launch.issue(await actor(world.engine, "member"), app_id, purpose="published_view")


@pytest.mark.asyncio
async def test_viewer_kind_filter_follows_the_active_version_and_mine_the_working_one(world):
    """Selecting a working version of another kind copies it to ``App.artifact_kind``; viewers keep the live one."""
    app_id, _ = await seed_app(world.engine, "creator", shared=True, is_global=True)
    working, now = uuid4(), utcnow()
    async with world.engine.begin() as connection:
        await connection.execute(
            Version.__table__.insert().values(
                id=working,
                app_id=app_id,
                sequence=2,
                original_filename="bundle.zip",
                artifact_kind="zip",
                expected_bytes=10,
                upload_id=uuid4(),
                upload_expires_at=now,
                validation_state="accepted",
                created_at=now,
            )
        )
        await connection.execute(
            App.__table__.update()
            .where(App.__table__.c.id == app_id)
            .values(selected_version_id=working, artifact_kind="zip")
        )

    async def listed(persona, scope, kind):
        page = await world.catalog.list_apps(await actor(world.engine, persona), scope=scope, artifact_kind=kind)
        return [item["id"] for item in page["items"]]

    for persona, scope in (("member", "project"), ("marketplace_viewer", "marketplace")):
        assert await listed(persona, scope, "html") == [str(app_id)], persona
        assert await listed(persona, scope, "zip") == [], persona
    assert await listed("creator", "mine", "zip") == [str(app_id)]
    assert await listed("creator", "mine", "html") == []


# ---- named view-only grants on real PostgreSQL (AC4, AC5, AC14, AC15) ----
#
# Skipped, through the ``database`` fixture, unless HOSTED_TEST_DATABASE_URL points at an isolated PostgreSQL
# database.

GRANTEES = ("viewer", "outsider")  # viewer is a project member of an unshared app; outsider is in no project


@pytest_asyncio.fixture
async def grant_world(database, launch, monkeypatch):
    engine, _ = database
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    async with engine.begin() as connection:
        await connection.execute(sa.text("CREATE SCHEMA IF NOT EXISTS codemie"))
        await connection.run_sync(Permission.__table__.create, checkfirst=True)
        await connection.execute(
            UserDB.__table__.insert().values(
                id="outsider",
                username="outsider",
                email="outsider@example.com",
                user_type="regular",
                is_active=True,
                is_admin=False,
                is_maintainer=False,
                is_auditor=False,
                auth_source="local",
                email_verified=False,
            )
        )
    handoff, service, worker, _ = launch
    return SimpleNamespace(engine=engine, handoff=handoff, service=service, worker=worker)


async def _grant(world, app_id, principal, level=Action.READ):
    async with world.engine.begin() as connection:
        await connection.execute(
            Permission.__table__.insert().values(
                id=str(uuid4()),
                resource_type=ResourceType.HOSTED_APPLICATION,
                resource_id=app_id,
                principal_type=PrincipalType.USER,
                principal_id=principal,
                permission_level=level,
            )
        )


async def _revoke(world, app_id):
    async with world.engine.begin() as connection:
        await connection.execute(Permission.__table__.delete().where(Permission.__table__.c.resource_id == app_id))


async def _active_unshared_app(world, owner):
    app, queued_version = await queued(world.service, owner)
    assert await world.worker.run_one()
    current = await world.service.get_app(owner, app["id"])
    await world.service.activate_version(
        owner, app["id"], VersionSelection(version_id=UUID(queued_version["version_id"])), current["etag"], str(uuid4())
    )
    return UUID(app["id"]), UUID(queued_version["version_id"])


async def _listed(world, who, scope):
    return [item["id"] for item in (await world.service.list_apps(who, scope=scope))["items"]]


@pytest.mark.asyncio
async def test_a_named_grant_opens_every_read_path_until_it_is_revoked(grant_world, identity):
    world, owner = grant_world, identity()
    app_id, version_id = await _active_unshared_app(world, owner)
    people = {name: identity(name) for name in GRANTEES}
    for who in people.values():
        assert await outcome(world.service.get_app(who, str(app_id))) is False
        assert await outcome(world.handoff.issue(who, str(app_id), purpose="published_view")) is False
    for name in GRANTEES:
        await _grant(world, app_id, name)
    cookies = {}
    for name, who in people.items():
        assert await outcome(world.service.get_app(who, str(app_id))) is True, name
        assert await _listed(world, who, "mine") == [], name
        ticket = await world.handoff.issue(who, str(app_id), purpose="published_view")
        cookies[name], _ = await world.handoff.consume(ticket["ticket"], world.handoff.host(version_id))
        assert (await world.handoff.authorize(cookies[name], version_id))["viewer_subject"] == name
        # View-only: no management path and no owner preview.
        assert await outcome(world.handoff.issue(who, str(app_id), purpose="owner_preview")) is False, name
    assert str(app_id) in await _listed(world, people["viewer"], "project")
    assert str(app_id) not in await _listed(world, people["outsider"], "project")
    current = await world.service.get_app(owner, str(app_id))
    for who in people.values():
        assert (
            await outcome(
                world.service.patch_app(who, str(app_id), AppPatch(shared=True), current["etag"], str(uuid4()))
            )
            is False
        )
    await _revoke(world, app_id)
    for name, who in people.items():
        assert await outcome(world.service.get_app(who, str(app_id))) is False, name
        assert await outcome(world.handoff.authorize(cookies[name], version_id)) is False, name


@pytest.mark.asyncio
async def test_a_grant_does_not_open_a_suspended_app(grant_world, identity):
    world, owner = grant_world, identity()
    app_id, version_id = await _active_unshared_app(world, owner)
    who = identity("outsider")
    await _grant(world, app_id, "outsider")
    ticket = await world.handoff.issue(who, str(app_id), purpose="published_view")
    cookie, _ = await world.handoff.consume(ticket["ticket"], world.handoff.host(version_id))
    async with world.engine.begin() as connection:
        await connection.execute(
            App.__table__.update().where(App.__table__.c.id == app_id).values(security_state="suspended")
        )
    assert await outcome(world.service.get_app(who, str(app_id))) is False
    assert await outcome(world.handoff.issue(who, str(app_id), purpose="published_view")) is False
    assert await outcome(world.handoff.authorize(cookie, version_id)) is False
    row = await app_row(world.engine, app_id)
    assert row.access_revision == 1, "a grant never bumps the access revision"


@pytest.mark.asyncio
async def test_the_shared_scope_lists_granted_apps_only_for_their_grantees(grant_world, identity):
    world, owner = grant_world, identity()
    app_id, _ = await _active_unshared_app(world, owner)
    viewer, outsider = identity("viewer"), identity("outsider")
    for who in (owner, viewer, outsider):
        assert await _listed(world, who, "shared") == []
    await _grant(world, app_id, "outsider")
    assert await _listed(world, outsider, "shared") == [str(app_id)]
    assert await _listed(world, viewer, "shared") == []
    # The creator never sees their own app in "shared with me", even when granted themselves.
    await _grant(world, app_id, owner.principal_id)
    assert await _listed(world, owner, "shared") == []
    async with world.engine.begin() as connection:
        await connection.execute(
            App.__table__.update().where(App.__table__.c.id == app_id).values(security_state="suspended")
        )
    assert await _listed(world, outsider, "shared") == []
    await _revoke(world, app_id)
    assert await _listed(world, outsider, "shared") == []
