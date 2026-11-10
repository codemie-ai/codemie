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

"""Catalog: create, open, list and patch under the platform access model.

The catalog runs its real SQL (``manageable`` / ``readable`` and the list scopes) on in-memory SQLite against the
real ``users``, ``user_projects`` and ``hosted_applications`` tables. Only the management transaction is a fake:
it records the order of the quota lock and the idempotency lookup, and replays by key like the real one.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import bindparam, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from codemie.core.hosted_applications.access import manageable
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.repository.hosted_applications.repository import as_uuid, not_found
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs as Job,
    HostedApplicationStorageAttempts as Attempt,
    HostedApplications as App,
)
from codemie.rest_api.models.hosted_application_requests import AppCreate, AppPatch
from codemie.configs import config
from codemie.rest_api.models.permission import Action, Permission, PrincipalType, ResourceType, ShareLink
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.rest_api.security.hosted_applications import HostedActor
from codemie.rest_api.security.user import User
from codemie.service.hosted_applications import catalog as catalog_module
from codemie.service.hosted_applications import runtime as runtime_module
from codemie.service.hosted_applications.catalog import HostedApplicationCatalog, list_scope
from codemie.service.permission import named_grant_service, share_link_service

TEAM = "team-a"
OTHER = "team-b"


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


class FakeTx:
    """The real SQL session plus an in-memory stand-in for the locking, idempotency and audit steps."""

    def __init__(self, session, repository, user):
        self.session, self.repository, self.user = session, repository, user
        self.now = datetime.now(timezone.utc)
        self.quota = None

    async def lock_quota(self, principal_id):
        self.repository.events.append(("lock_quota", principal_id))
        self.quota = principal_id

    async def operation(self, action, target, payload, key=None, *, app_id=None):
        self.repository.events.append(("operation", action))
        stored = self.repository.replays.get((action, str(target), key))
        if stored is not None:
            return stored, stored["body"]
        record = type("Operation", (), {"app_id": app_id, "key": (action, str(target), key)})()
        return record, None

    async def finish(self, operation, response, status=200, *, details=None):
        self.repository.replays[operation.key] = {"body": response}
        return response

    async def managed_app(self, app_id, user, *, lock=False, states=("active",), action=Action.WRITE):
        self.repository.manager_actions.append(action)
        user_id = bindparam("manager_id", user.id, type_=sa.Text)
        query = select(App).where(
            App.id == as_uuid(app_id), App.lifecycle_state.in_(states), manageable(App.__table__, user_id, action)
        )
        app = (await self.session.execute(query)).scalar_one_or_none()
        if app is None:
            raise not_found()
        return app

    async def set_lock_timeout(self, milliseconds):
        """CR-014: too late for open; the timeout is a transaction option, set before its first statement."""
        raise AssertionError("open must bound its lock waits through repository.transaction(lock_timeout_ms=...)")

    async def lock_app(self, query):
        """The locking select, run plainly: SQLite has no FOR UPDATE."""
        return (await self.session.execute(query.execution_options(populate_existing=True))).scalar_one_or_none()


class FakeRepository:
    secret = b"s" * 48

    def __init__(self, engine):
        self.engine = engine
        self.events = []
        self.replays = {}
        self.manager_actions = []

    @asynccontextmanager
    async def transaction(
        self, user, recorded_identity=None, *, write=False, authorized_until=None, lock_timeout_ms=None
    ):
        async with AsyncSession(self.engine, expire_on_commit=False) as session, session.begin():
            if lock_timeout_ms is not None:
                # SQLite has no lock_timeout; the option is recorded so its order can be checked.
                self.events.append(("lock_timeout", lock_timeout_ms))
            tx = FakeTx(session, self, user)
            tx.eligible_versions, tx.covered = {}, set()
            yield tx


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    @sa.event.listens_for(engine.sync_engine, "connect")
    def _char_length(dbapi_connection, _):
        dbapi_connection.create_function("char_length", 1, lambda value: None if value is None else len(value))

    # Jobs, attempts and share links, which delete reads and writes, without their PostgreSQL CHECK constraints.
    metadata = sa.MetaData()
    for table in (Job.__table__, Attempt.__table__, Permission.__table__, ShareLink.__table__):
        sa.Table(table.name, metadata, *[column._copy() for column in table.columns])
    async with engine.begin() as connection:
        for table in (UserDB.__table__, UserProject.__table__, App.__table__):
            await connection.run_sync(table.create)
        await connection.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def repository(engine):
    return FakeRepository(engine)


@pytest.fixture
def catalog(repository, monkeypatch):
    # No directory: nothing in the catalog may ask it anything.
    service = HostedApplicationCatalog(repository, directory=None)

    async def no_covers(tx, apps):
        return set()

    monkeypatch.setattr(service, "_covered", no_covers)
    return service


@pytest_asyncio.fixture
async def people(engine):
    """Persona -> actor; every persona has a ``users`` row and the memberships the access SQL reads."""
    personas = {
        "creator": ("u-creator", {TEAM: False}, {}),
        "padmin": ("u-padmin", {TEAM: True}, {}),
        "member": ("u-member", {TEAM: False}, {}),
        "outsider": ("u-outsider", {OTHER: False}, {}),
        "admin": ("u-admin", {OTHER: False}, {"is_admin": True}),
        "external": ("u-ext", {TEAM: False}, {"user_type": "external"}),
    }
    actors = {}
    async with engine.begin() as connection:
        for name, (user_id, memberships, flags) in personas.items():
            await connection.execute(
                UserDB.__table__.insert().values(
                    id=user_id,
                    username=name,
                    email=f"{name}@example.com",
                    user_type=flags.get("user_type", "regular"),
                    is_active=True,
                    is_admin=flags.get("is_admin", False),
                    is_maintainer=False,
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
            user = User(
                id=user_id,
                username=name,
                name=name.title(),
                project_names=list(memberships),
                admin_project_names=[project for project, admin in memberships.items() if admin],
                user_type=flags.get("user_type", "regular"),
            )
            # ENV=local forces is_admin on at construction; the persona decides here.
            user.is_admin = flags.get("is_admin", False)
            actors[name] = HostedActor(user, None)
    return actors


def request(**changes):
    return AppCreate(**{"name": "Delivery map", "description": "A useful visual", "project": TEAM, **changes})


async def create(catalog, actor, **changes):
    return await catalog.create_app(actor, request(**changes), str(uuid4()))


async def set_app(engine, app_id, **values):
    async with engine.begin() as connection:
        await connection.execute(App.__table__.update().where(App.id == as_uuid(app_id)).values(**values))


async def names(catalog, actor, **filters):
    return [item["name"] for item in (await catalog.list_apps(actor, **filters))["items"]]


# ---- create ----


@pytest.mark.asyncio
async def test_create_in_a_project_the_caller_cannot_access_is_forbidden(catalog, people, repository):
    with pytest.raises(HostedApplicationError) as error:
        await create(catalog, people["outsider"])
    assert error.value.status == 403
    assert repository.events == []


@pytest.mark.asyncio
async def test_external_user_cannot_create(catalog, people):
    with pytest.raises(HostedApplicationError) as error:
        await create(catalog, people["external"])
    assert error.value.status == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"is_active": False}, {"deleted_at": datetime(2026, 1, 1)}])
async def test_an_inactive_or_deleted_account_cannot_create(catalog, people, repository, engine, change):
    async with engine.begin() as connection:
        await connection.execute(UserDB.__table__.update().where(UserDB.__table__.c.id == "u-creator").values(**change))
    with pytest.raises(HostedApplicationError) as error:
        await create(catalog, people["creator"])
    assert (error.value.code, error.value.status) == ("ACCOUNT_NOT_SUPPORTED", 403)
    assert repository.events == []


@pytest.mark.asyncio
async def test_create_stores_the_project_and_the_creator(catalog, people, engine):
    created = await create(catalog, people["creator"])
    async with AsyncSession(engine) as session:
        row = await session.get(App, as_uuid(created["id"]))
    assert row.project == TEAM
    assert (row.created_by.id, row.created_by.username, row.created_by.name) == ("u-creator", "creator", "Creator")
    assert (row.shared, row.is_global) == (False, False)
    assert "write" in created["user_abilities"] and "capabilities" not in created and "audience" not in created
    assert "can_manage" not in created and "can_launch" not in created
    assert created["publisher"] == {"display_name": "Creator"}


@pytest.mark.asyncio
async def test_create_shared_with_the_project_shows_the_app_to_members(catalog, people, engine):
    created = await create(catalog, people["creator"], shared=True)
    async with AsyncSession(engine) as session:
        row = await session.get(App, as_uuid(created["id"]))
    assert (row.shared, row.is_global) == (True, False)
    opened = await catalog.get_app(people["member"], created["id"])
    assert opened["user_abilities"] == ["read"]
    assert await names(catalog, people["member"], scope="project") == ["Delivery map"]


@pytest.mark.asyncio
async def test_create_with_shared_off_keeps_the_app_private(catalog, people):
    await create(catalog, people["creator"], shared=False)
    assert await names(catalog, people["member"], scope="project") == []


@pytest.mark.asyncio
async def test_create_shared_in_a_project_the_caller_cannot_access_is_forbidden(catalog, people, repository):
    with pytest.raises(HostedApplicationError) as error:
        await create(catalog, people["outsider"], shared=True)
    assert error.value.status == 403
    assert repository.events == []


@pytest.mark.asyncio
async def test_create_locks_the_creator_quota_before_the_idempotency_lookup(catalog, people, repository):
    await create(catalog, people["creator"])
    assert repository.events == [("lock_quota", "u-creator"), ("operation", "app.create")]


@pytest.mark.asyncio
async def test_two_creates_with_one_key_return_one_app(catalog, people):
    key = str(uuid4())
    first = await catalog.create_app(people["creator"], request(), key)
    again = await catalog.create_app(people["creator"], request(), key)
    assert first == again
    assert len(await names(catalog, people["creator"], scope="mine")) == 1


@pytest.mark.asyncio
async def test_application_count_is_capped_per_creator(catalog, people, monkeypatch):
    from codemie.service.hosted_applications import catalog as catalog_module

    monkeypatch.setattr(catalog_module, "MAX_APPLICATIONS_PER_OWNER", 1)
    await create(catalog, people["creator"])
    with pytest.raises(HostedApplicationError) as error:
        await create(catalog, people["creator"])
    assert (error.value.code, error.value.status) == ("APPLICATION_LIMIT", 409)
    await create(catalog, people["padmin"])


@pytest.mark.asyncio
async def test_delete_leaves_shared_and_is_global_alone(catalog, people, engine):
    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], shared=True, is_global=True)
    await catalog.delete(people["creator"], app["id"], app["etag"], str(uuid4()))
    async with AsyncSession(engine) as session:
        row = await session.get(App, as_uuid(app["id"]))
    # Only PATCH changes ``shared`` and only /marketplace/* changes ``is_global``; the lifecycle state ends access.
    assert (row.lifecycle_state, row.shared, row.is_global) == ("purging", True, True)


@pytest.mark.asyncio
async def test_delete_asks_the_manager_rule_for_delete(catalog, people, repository):
    """The delete path asks ``manageable(DELETE)``, so a future WRITE grant term cannot imply delete (AR-3)."""
    app = await create(catalog, people["creator"])
    repository.manager_actions.clear()
    await catalog.delete(people["creator"], app["id"], app["etag"], str(uuid4()))
    assert repository.manager_actions == [Action.DELETE]


# ---- open by id ----


@pytest.mark.asyncio
async def test_shared_off_hides_the_app_from_members_and_on_shows_it(catalog, people):
    app = await create(catalog, people["creator"])
    with pytest.raises(HostedApplicationError) as error:
        await catalog.get_app(people["member"], app["id"])
    assert error.value.status == 404
    assert await names(catalog, people["member"], scope="project") == []

    patched = await catalog.patch_app(people["creator"], app["id"], AppPatch(shared=True), app["etag"], str(uuid4()))
    opened = await catalog.get_app(people["member"], app["id"])
    assert opened["id"] == app["id"] and opened["user_abilities"] == ["read"]
    assert await names(catalog, people["member"], scope="project") == ["Delivery map"]

    await catalog.patch_app(people["creator"], app["id"], AppPatch(shared=False), patched["etag"], str(uuid4()))
    assert await names(catalog, people["member"], scope="project") == []


@pytest.mark.asyncio
async def test_a_shared_app_without_an_active_version_still_returns_details(catalog, people, engine):
    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], shared=True)
    opened = await catalog.get_app(people["member"], app["id"])
    assert opened["name"] == "Delivery map"
    assert opened["has_active_version"] is False


@pytest.mark.asyncio
async def test_managers_open_an_unshared_app_and_outsiders_never_do(catalog, people):
    app = await create(catalog, people["creator"])
    for persona in ("creator", "padmin", "admin"):
        assert "write" in (await catalog.get_app(people[persona], app["id"]))["user_abilities"]
    for persona in ("member", "outsider", "external"):
        with pytest.raises(HostedApplicationError) as error:
            await catalog.get_app(people[persona], app["id"])
        assert error.value.status == 404


@pytest.mark.asyncio
async def test_suspended_app_is_open_to_managers_and_not_to_viewers(catalog, people, engine):
    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], shared=True, security_state="suspended")
    assert (await catalog.get_app(people["padmin"], app["id"]))["id"] == app["id"]
    with pytest.raises(HostedApplicationError):
        await catalog.get_app(people["member"], app["id"])


@pytest.mark.asyncio
async def test_a_purging_application_is_not_found_by_id(catalog, people, engine):
    from datetime import datetime, timezone

    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], lifecycle_state="purging", deleted_at=datetime.now(timezone.utc))
    with pytest.raises(HostedApplicationError) as error:
        await catalog.get_app(people["creator"], app["id"])
    assert error.value.status == 404


# ---- lists ----


@pytest.mark.asyncio
async def test_project_admin_lists_the_projects_unshared_apps(catalog, people):
    await create(catalog, people["creator"], name="Private one")
    assert await names(catalog, people["padmin"], scope="project") == ["Private one"]
    assert await names(catalog, people["member"], scope="project") == []


@pytest.mark.asyncio
async def test_platform_admin_opens_and_lists_every_projects_apps_private_ones_included(catalog, people):
    app = await create(catalog, people["creator"], name="Private elsewhere")  # not shared; the admin is no member
    assert (await catalog.get_app(people["admin"], app["id"]))["id"] == app["id"]
    assert await names(catalog, people["admin"], scope="project") == ["Private elsewhere"]
    assert await names(catalog, people["admin"], scope="mine") == []


@pytest.mark.asyncio
async def test_mine_lists_only_what_the_caller_created(catalog, people):
    await create(catalog, people["creator"], name="Mine")
    await create(catalog, people["padmin"], name="Theirs")
    assert await names(catalog, people["creator"], scope="mine") == ["Mine"]
    assert await names(catalog, people["padmin"], scope="mine") == ["Theirs"]


@pytest.mark.asyncio
async def test_marketplace_lists_global_apps_for_internal_users_only(catalog, people, engine):
    shown = await create(catalog, people["creator"], name="On the marketplace")
    await create(catalog, people["creator"], name="Not published")
    await set_app(engine, shown["id"], is_global=True)
    for persona in ("outsider", "member", "admin"):
        assert await names(catalog, people[persona], scope="marketplace") == ["On the marketplace"]
    assert await names(catalog, people["external"], scope="marketplace") == []
    await set_app(engine, shown["id"], security_state="suspended")
    assert await names(catalog, people["outsider"], scope="marketplace") == []


@pytest.mark.asyncio
async def test_a_project_move_changes_which_members_see_the_app(catalog, people, engine):
    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], shared=True)
    await set_app(engine, app["id"], project=OTHER)
    assert await names(catalog, people["member"], scope="project") == []
    assert await names(catalog, people["outsider"], scope="project") == ["Delivery map"]


@pytest.mark.asyncio
async def test_list_rejects_removed_options_and_scopes(catalog, people):
    actor = people["creator"]
    for scope in ("explore", "trash"):
        with pytest.raises(HostedApplicationError) as error:
            await catalog.list_apps(actor, scope=scope)
        assert error.value.code == "INVALID_FILTER"
    for option in ("audience", "publication_state"):
        with pytest.raises(TypeError):
            await catalog.list_apps(actor, **{option: "private"})


@pytest.mark.asyncio
async def test_search_is_literal_and_cursor_is_actor_and_filter_bound(catalog, people):
    actor = people["creator"]
    for name in ("100% plan", "Other plan", "100_ideas"):
        await create(catalog, actor, name=name)
    assert await names(catalog, actor, q="%") == ["100% plan"]
    assert await names(catalog, actor, q="_") == ["100_ideas"]
    page = await catalog.list_apps(actor, limit=1)
    assert len(page["items"]) == 1 and page["next_cursor"]
    second = await catalog.list_apps(actor, limit=1, cursor=page["next_cursor"])
    assert second["items"][0]["id"] != page["items"][0]["id"]
    with pytest.raises(HostedApplicationError):
        await catalog.list_apps(people["padmin"], limit=1, cursor=page["next_cursor"])
    with pytest.raises(HostedApplicationError):
        await catalog.list_apps(actor, limit=1, q="different", cursor=page["next_cursor"])


@pytest.mark.asyncio
async def test_project_and_created_by_narrow_every_scope(catalog, people, engine):
    team = await create(catalog, people["creator"], name="Team app")
    other = await create(catalog, people["outsider"], name="Other app", project=OTHER)
    padmin = await create(catalog, people["padmin"], name="Padmin app")
    for app in (team, other, padmin):
        await set_app(engine, app["id"], shared=True, is_global=True)
    viewer = people["admin"]
    assert await names(catalog, viewer, scope="marketplace", project=[TEAM]) == ["Padmin app", "Team app"]
    assert await names(catalog, viewer, scope="marketplace", project=[OTHER, TEAM], created_by="Creator") == [
        "Team app"
    ]
    assert await names(catalog, viewer, scope="marketplace", created_by="Outsider") == ["Other app"]
    assert await names(catalog, people["member"], scope="project", created_by="Padmin") == ["Padmin app"]
    assert await names(catalog, people["member"], scope="project", project=[OTHER]) == []
    assert await names(catalog, people["creator"], scope="mine", project=TEAM) == ["Team app"]
    assert await names(catalog, people["creator"], scope="mine", created_by="Padmin") == []


@pytest.mark.asyncio
async def test_shared_and_status_describe_the_callers_own_apps(catalog, people, engine, grants_on):
    actor = people["creator"]
    await create(catalog, actor, name="Private")
    by_name = await create(catalog, actor, name="People")
    in_project = await create(catalog, actor, name="Project")
    published = await create(catalog, actor, name="Published")
    await grant_row(engine, by_name["id"], "u-outsider")
    await set_app(engine, in_project["id"], shared=True, active_version_id=uuid4())
    await set_app(engine, published["id"], is_global=True, active_version_id=uuid4())
    assert await names(catalog, actor, shared="private") == ["Private"]
    assert await names(catalog, actor, shared="people") == ["People"]
    assert await names(catalog, actor, shared="project") == ["Project"]
    assert await names(catalog, actor, status="draft") == ["People", "Private"]
    assert await names(catalog, actor, status="published") == ["Published"]
    assert await names(catalog, actor, status="unpublished") == ["Project"]
    assert await names(catalog, actor, shared="private", status="draft") == ["Private"]
    # Published before any version is active (allowed, CORE-15): Published only, never Draft as well (S-22).
    early = await create(catalog, actor, name="Early")
    await set_app(engine, early["id"], is_global=True)
    assert await names(catalog, actor, status="draft") == ["People", "Private"]
    assert await names(catalog, actor, status="published") == ["Early", "Published"]
    assert await names(catalog, actor, status="unpublished") == ["Project"]
    # A grant of an account that can no longer sign in is not live, so the app counts as private again.
    async with engine.begin() as connection:
        await connection.execute(UserDB.__table__.update().where(UserDB.id == "u-outsider").values(is_active=False))
    assert await names(catalog, actor, shared="people") == []
    assert await names(catalog, actor, shared="private") == ["People", "Private"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters",
    [
        {"shared": "anyone"},
        {"status": "deleted"},
        {"scope": "project", "shared": "private"},
        {"scope": "marketplace", "status": "published"},
        {"scope": "shared", "status": "draft"},
        {"project": [f"p{i}" for i in range(51)]},
        {"project": ["x" * 201]},
        {"project": [1]},
        {"created_by": "x" * 201},
    ],
)
async def test_list_rejects_malformed_project_creator_sharing_and_status_filters(catalog, people, filters):
    with pytest.raises(HostedApplicationError) as error:
        await catalog.list_apps(people["creator"], **filters)
    assert error.value.code == "INVALID_FILTER"


@pytest.mark.asyncio
async def test_the_cursor_is_bound_to_project_creator_sharing_and_status(catalog, people):
    actor = people["creator"]
    for name in ("One", "Two"):
        await create(catalog, actor, name=name)
    filters = {"project": [TEAM], "created_by": "Creator", "shared": "private", "status": "draft"}
    page = await catalog.list_apps(actor, limit=1, **filters)
    assert page["next_cursor"]
    # Order and blanks of the project list do not change the binding.
    same = {**filters, "project": ["", TEAM, TEAM]}
    assert len((await catalog.list_apps(actor, limit=1, cursor=page["next_cursor"], **same))["items"]) == 1
    for change in ({"project": [OTHER]}, {"created_by": "Padmin"}, {"shared": "people"}, {"status": "published"}):
        with pytest.raises(HostedApplicationError) as error:
            await catalog.list_apps(actor, limit=1, cursor=page["next_cursor"], **{**filters, **change})
        assert error.value.code == "INVALID_CURSOR"


@pytest.mark.asyncio
async def test_creators_are_those_of_the_apps_the_caller_may_list(catalog, people, engine):
    team = await create(catalog, people["creator"], name="Team app")
    hidden = await create(catalog, people["outsider"], name="Hidden", project=OTHER)
    padmin = await create(catalog, people["padmin"], name="Padmin app")
    await set_app(engine, team["id"], shared=True)
    await set_app(engine, padmin["id"], shared=True)

    def ids(rows):
        return [row["id"] for row in rows]

    member = people["member"]
    assert ids(await catalog.list_creators(member)) == ["u-creator", "u-padmin"]
    assert ids(await catalog.list_creators(member, scope="mine")) == []
    assert ids(await catalog.list_creators(people["creator"], scope="mine")) == ["u-creator"]
    assert await catalog.list_creators(people["creator"], scope="mine") == [
        {"id": "u-creator", "username": "creator", "name": "Creator"}
    ]
    # Apps the caller cannot list give no creators: the outsider's, until it is in the Marketplace.
    assert "u-outsider" not in ids(await catalog.list_creators(member))
    await set_app(engine, hidden["id"], is_global=True)
    assert "u-outsider" in ids(await catalog.list_creators(member, scope="marketplace"))
    with pytest.raises(HostedApplicationError) as error:
        await catalog.list_creators(member, scope="trash")
    assert error.value.code == "INVALID_FILTER"


# ---- no moderation scope (S-28, supersedes S-20) ----


@pytest.mark.asyncio
@pytest.mark.parametrize("persona", ["admin", "creator"])
async def test_there_is_no_moderation_scope(catalog, people, persona):
    """No Moderation tab: suspend, reinstate and link revoke stay in the app's actions, found by the app's URL."""
    for call in (
        catalog.list_apps(people[persona], scope="moderation"),
        catalog.list_creators(people[persona], scope="moderation"),
    ):
        with pytest.raises(HostedApplicationError) as error:
            await call
        assert error.value.code == "INVALID_FILTER"


def test_the_list_takes_no_security_state_filter():
    import inspect

    from codemie.rest_api.routers import hosted_applications as router

    assert "security_state" not in inspect.signature(HostedApplicationCatalog.list_apps).parameters
    assert "security_state" not in inspect.signature(router.catalog).parameters
    assert not hasattr(catalog_module, "MODERATION_SCOPE") and not hasattr(catalog_module, "SECURITY_FILTERS")


@pytest.mark.asyncio
async def test_an_admin_finds_and_opens_a_suspended_app_of_another_project(catalog, people, engine):
    other = await create(catalog, people["creator"], name="Team suspended")  # the admin is not a TEAM member
    await set_app(engine, other["id"], security_state="suspended")
    assert "Team suspended" in await names(catalog, people["admin"], scope="project")
    detail = await catalog.get_app(people["admin"], other["id"])
    assert detail["suspended"] is True and "write" in detail["user_abilities"]


# ---- patch ----


@pytest.mark.asyncio
async def test_shared_on_and_a_move_are_refused_while_suspended_but_shared_off_is_allowed(catalog, people, engine):
    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], shared=False, security_state="suspended")
    etag = app["etag"]
    for change in ({"shared": True}, {"project": TEAM + "-moved"}):
        actor = people["admin"]  # a platform admin has access to any project
        with pytest.raises(HostedApplicationError) as error:
            await catalog.patch_app(actor, app["id"], AppPatch(**change), etag, str(uuid4()))
        assert (error.value.code, error.value.status) == ("APP_SUSPENDED", 409)
    await set_app(engine, app["id"], shared=True)
    off = await catalog.patch_app(people["creator"], app["id"], AppPatch(shared=False), etag, str(uuid4()))
    assert off["revision"] == 2


@pytest.mark.asyncio
async def test_an_edit_that_resends_an_unchanged_shared_true_is_allowed_while_suspended(catalog, people, engine):
    """CR-013: only a real share-on transition is gated; a full-form edit re-sends ``shared=true``."""
    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], shared=True, security_state="suspended")
    patched = await catalog.patch_app(
        people["creator"], app["id"], AppPatch(name="Renamed", shared=True), app["etag"], str(uuid4())
    )
    assert (patched["name"], patched["revision"]) == ("Renamed", 2)


@pytest.mark.asyncio
async def test_a_move_needs_access_to_the_target_project(catalog, people):
    app = await create(catalog, people["padmin"])
    with pytest.raises(HostedApplicationError) as error:
        await catalog.patch_app(people["padmin"], app["id"], AppPatch(project=OTHER), app["etag"], str(uuid4()))
    assert error.value.status == 403


@pytest.mark.asyncio
async def test_a_move_changes_the_project_and_keeps_the_access_revision(catalog, people, engine):
    app = await create(catalog, people["admin"])
    moved = await catalog.patch_app(people["admin"], app["id"], AppPatch(project=OTHER), app["etag"], str(uuid4()))
    assert moved["revision"] == 2
    async with AsyncSession(engine) as session:
        row = await session.get(App, as_uuid(app["id"]))
    # Only delete and purge bump the access revision; the gateway re-evaluates the rule on every request.
    assert (row.project, row.access_revision) == (OTHER, 1)


@pytest.mark.asyncio
async def test_project_members_cannot_patch(catalog, people, engine):
    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], shared=True)
    with pytest.raises(HostedApplicationError) as error:
        await catalog.patch_app(people["member"], app["id"], AppPatch(name="Renamed"), app["etag"], str(uuid4()))
    assert error.value.status == 404


@pytest.mark.asyncio
async def test_patch_replays_by_key_and_checks_the_revision(catalog, people):
    app = await create(catalog, people["creator"])
    key = str(uuid4())
    updated = await catalog.patch_app(people["creator"], app["id"], AppPatch(name="Updated"), app["etag"], key)
    assert updated["revision"] == 2 and updated["name"] == "Updated"
    assert await catalog.patch_app(people["creator"], app["id"], AppPatch(name="Updated"), app["etag"], key) == updated
    with pytest.raises(HostedApplicationError) as error:
        await catalog.patch_app(people["creator"], app["id"], AppPatch(name="Again"), app["etag"], str(uuid4()))
    assert error.value.code == "REVISION_CONFLICT"


def test_patch_request_accepts_project_and_shared_and_rejects_nulls():
    assert AppPatch(project="team-a").project == "team-a"
    assert AppPatch(shared=False).shared is False
    for bad in ({}, {"shared": None}, {"project": None}, {"shared": "yes"}):
        with pytest.raises(ValueError):
            AppPatch(**bad)
    with pytest.raises(ValueError):
        AppCreate(name="n", description="d")


# ---- named view-only grants ----


@pytest.fixture
def grants_on(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)


async def grant_row(engine, app_id, principal, level=Action.READ):
    async with engine.begin() as connection:
        await connection.execute(
            Permission.__table__.insert().values(
                id=str(uuid4()),
                resource_type=ResourceType.HOSTED_APPLICATION,
                resource_id=as_uuid(app_id),
                principal_type=PrincipalType.USER,
                principal_id=principal,
                permission_level=level,
            )
        )


@pytest.mark.asyncio
async def test_a_grantee_reads_the_app_and_lists_it_under_shared(catalog, people, engine, grants_on):
    app = await create(catalog, people["creator"], name="Granted")
    assert await names(catalog, people["outsider"], scope="shared") == []
    await grant_row(engine, app["id"], "u-outsider")
    opened = await catalog.get_app(people["outsider"], app["id"])
    assert opened["user_abilities"] == ["read"] and opened["can_share"] is False
    assert await names(catalog, people["outsider"], scope="shared") == ["Granted"]
    assert await names(catalog, people["outsider"], scope="project") == []
    # The creator's own app is not "shared with me"; a non-grantee sees nothing.
    await grant_row(engine, app["id"], "u-creator")
    assert await names(catalog, people["creator"], scope="shared") == []
    assert await names(catalog, people["member"], scope="shared") == []
    await set_app(engine, app["id"], security_state="suspended")
    assert await names(catalog, people["outsider"], scope="shared") == []


def test_shared_scope_is_active_and_grant_and_not_creator_and_readable_without_project_or_global_terms():
    from sqlalchemy import and_
    from sqlalchemy.dialects import postgresql

    from codemie.core.hosted_applications.access import grant_recipient, readable
    from codemie.rest_api.models.permission import grant_exists

    apps = App.__table__
    user = User(id="u-1", username="u", project_names=["p"])
    viewer = bindparam("viewer_id", "u-1", type_=sa.Text)
    expected = and_(
        apps.c.lifecycle_state == "active",
        grant_exists(ResourceType.HOSTED_APPLICATION, apps.c.id, viewer, Action.READ, recipient=grant_recipient),
        apps.c.created_by["id"].as_string() != "u-1",
        readable(apps, viewer),
    )
    dialect = postgresql.dialect()
    actual = list_scope(apps, "shared", user)
    assert str(actual.compile(dialect=dialect)) == str(expected.compile(dialect=dialect))


def test_project_scope_lists_every_project_for_a_platform_admin_and_member_projects_otherwise():
    from sqlalchemy import and_
    from sqlalchemy.dialects import postgresql

    from codemie.core.hosted_applications.access import readable

    apps = App.__table__
    dialect = postgresql.dialect()
    viewer = bindparam("viewer_id", "u-1", type_=sa.Text)
    active = apps.c.lifecycle_state == "active"
    # ``platform_user`` copies: roles set after construction, as the catalog sees them (ENV=local forces is_admin).
    base = User(id="u-1", username="u", project_names=["p"])
    member = base.model_copy(update={"is_admin": False, "is_maintainer": False})
    admin = base.model_copy(update={"is_admin": True})

    def sql(term):
        return str(term.compile(dialect=dialect))

    assert sql(list_scope(apps, "project", admin)) == sql(and_(active, readable(apps, viewer)))
    assert sql(list_scope(apps, "project", member)) == sql(
        and_(active, apps.c.project.in_(["p"]), readable(apps, viewer))
    )


@pytest.mark.asyncio
async def test_grants_are_preloaded_once_per_transaction_and_reach_every_ability(
    catalog, people, engine, grants_on, monkeypatch
):
    for index in range(3):
        app = await create(catalog, people["creator"], name=f"App {index}")
        await grant_row(engine, app["id"], "u-outsider")
    real, calls, abilities = named_grant_service.apreload, [], []

    async def counting(session, resource_type, user, resource_ids=None):
        calls.append(user.id)
        return await real(session, resource_type, user, resource_ids)

    monkeypatch.setattr(named_grant_service, "apreload", counting)
    original = catalog_module.app_projection

    def spying(app, user, **kwargs):
        abilities.append(kwargs.get("grants"))
        return original(app, user, **kwargs)

    monkeypatch.setattr(catalog_module, "app_projection", spying)
    page = await catalog.list_apps(people["outsider"], scope="shared")
    assert [item["user_abilities"] for item in page["items"]] == [["read"]] * 3
    assert calls == ["u-outsider"]
    assert len(abilities) == 3 and all(item is not None for item in abilities)


@pytest.mark.asyncio
async def test_rows_the_share_writes_are_the_rows_the_grant_terms_read(catalog, people, engine, grants_on):
    """Writer/reader contract: the rows agrant upserts make the grantee a reader and list the app as shared."""
    app = await create(catalog, people["creator"], name="Round trip")
    rows = named_grant_service.grant_rows(
        ResourceType.HOSTED_APPLICATION, app["id"], people["creator"].user, ["u-outsider"]
    )
    async with engine.begin() as connection:
        await connection.execute(Permission.__table__.insert().values(rows))
    opened = await catalog.get_app(people["outsider"], app["id"])
    assert opened["user_abilities"] == ["read"]
    assert await names(catalog, people["outsider"], scope="shared") == ["Round trip"]


class RecordingTx(FakeTx):
    """Records ``managed_app`` locks so the share operations can be checked to run on the locked row."""

    async def managed_app(self, app_id, user, *, lock=False, states=("active",), action=Action.WRITE):
        self.repository.events.append(("managed_app", lock))
        return await super().managed_app(app_id, user, lock=lock, states=states, action=action)


@pytest.fixture
def share_calls(repository, monkeypatch):
    calls = []

    @asynccontextmanager
    async def transaction(user, recorded_identity=None, *, write=False, authorized_until=None, lock_timeout_ms=None):
        async with AsyncSession(repository.engine, expire_on_commit=False) as session, session.begin():
            repository.events.append(("transaction", write))
            if lock_timeout_ms is not None:
                repository.events.append(("lock_timeout", lock_timeout_ms))
            tx = RecordingTx(session, repository, user)
            tx.eligible_versions, tx.covered = {}, set()
            yield tx

    repository.transaction = transaction

    async def agrant(session, **kwargs):
        calls.append(("grant", kwargs))
        return kwargs["user_ids"]

    async def arevoke(session, **kwargs):
        calls.append(("revoke", kwargs))
        return kwargs["user_ids"]

    async def listed(session, resource_type, resource_id):
        calls.append(("list", resource_id))
        return [{"id": "u-outsider"}]

    async def search(session, resource_type, query):
        assert resource_type == ResourceType.HOSTED_APPLICATION
        calls.append(("search", query))
        return [{"id": "u-member"}]

    monkeypatch.setattr(named_grant_service, "agrant", agrant)
    monkeypatch.setattr(named_grant_service, "arevoke", arevoke)
    monkeypatch.setattr(named_grant_service, "alist_shared_with", listed)
    monkeypatch.setattr(named_grant_service, "search_sharable_users", search)
    return calls


@pytest.mark.asyncio
async def test_share_and_unshare_are_owner_only_on_the_locked_app(catalog, people, repository, share_calls):
    app = await create(catalog, people["creator"])
    repository.events.clear()
    shared = await catalog.share(people["creator"], app["id"], ["u-outsider"])
    assert shared == {"shared_with": [{"id": "u-outsider"}]}
    assert repository.events == [("transaction", True), ("managed_app", True)]
    kinds = [kind for kind, _ in share_calls]
    assert kinds == ["grant", "list"]
    assert share_calls[0][1]["project"] == TEAM and share_calls[0][1]["resource"].id == as_uuid(app["id"])
    assert share_calls[0][1]["resource_type"] == ResourceType.HOSTED_APPLICATION
    assert share_calls[0][1]["owner"].id == "u-creator"
    await catalog.unshare(people["creator"], app["id"], ["u-outsider"])
    assert [kind for kind, _ in share_calls][2:] == ["revoke", "list"]
    for operation in (catalog.share, catalog.unshare):
        # A project admin manages the app but is not its owner; so does a platform admin.
        for persona in ("padmin", "admin"):
            share_calls.clear()
            with pytest.raises(HostedApplicationError) as error:
                await operation(people[persona], app["id"], ["u-outsider"])
            assert (error.value.status, error.value.message) == (403, "Only the owner can share or revoke access")
            assert share_calls == []
    for persona in ("member", "outsider"):
        with pytest.raises(HostedApplicationError) as error:
            await catalog.share(people[persona], app["id"], ["u-member"])
        assert error.value.status == 404
    with pytest.raises(HostedApplicationError) as error:
        await catalog.shared_with(people["padmin"], app["id"])
    assert error.value.status == 403
    with pytest.raises(HostedApplicationError) as error:
        await catalog.sharable_users(people["padmin"], app["id"], "ab")
    assert error.value.status == 403
    assert await catalog.shared_with(people["creator"], app["id"]) == {"shared_with": [{"id": "u-outsider"}]}
    assert await catalog.sharable_users(people["creator"], app["id"], "ab") == {"users": [{"id": "u-member"}]}


@pytest.mark.asyncio
async def test_a_reader_who_cannot_manage_the_app_gets_403_on_every_share_operation(
    catalog, people, engine, grants_on, share_calls
):
    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], shared=True)
    await grant_row(engine, app["id"], "u-outsider")
    calls = (
        lambda who: catalog.share(who, app["id"], ["u-x"]),
        lambda who: catalog.unshare(who, app["id"], ["u-x"]),
        lambda who: catalog.shared_with(who, app["id"]),
        lambda who: catalog.sharable_users(who, app["id"], "ab"),
    )
    # A project member views the shared app; a grantee reads it through the named grant: both get 403, not 404.
    for persona in ("member", "outsider"):
        for call in calls:
            with pytest.raises(HostedApplicationError) as error:
                await call(people[persona])
            assert (error.value.status, error.value.message) == (403, "Only the owner can share or revoke access")
    assert share_calls == []
    # An unknown app stays 404.
    with pytest.raises(HostedApplicationError) as error:
        await catalog.share(people["member"], str(uuid4()), ["u-x"])
    assert error.value.status == 404


REAL_AGRANT = named_grant_service.agrant
REAL_LIST_SHARED_WITH = named_grant_service.alist_shared_with


@pytest.mark.asyncio
async def test_share_checks_before_grant_and_unshare_does_not(catalog, people, engine, share_calls, monkeypatch):
    # The shared agrant runs the descriptor's before_grant, so the real one is used for the share.
    monkeypatch.setattr(named_grant_service, "agrant", REAL_AGRANT)
    app = await create(catalog, people["creator"])
    await set_app(engine, app["id"], security_state="suspended")
    with pytest.raises(HostedApplicationError) as error:
        await catalog.share(people["creator"], app["id"], ["u-outsider"])
    assert (error.value.code, error.value.status) == ("APP_SUSPENDED", 409)
    assert share_calls == []
    await catalog.unshare(people["creator"], app["id"], ["u-outsider"])
    assert [kind for kind, _ in share_calls] == ["revoke", "list"]


@pytest.mark.asyncio
async def test_delete_removes_every_grant_in_the_same_transaction(catalog, people, monkeypatch):
    app = await create(catalog, people["creator"])
    seen = []

    async def removed(actor, resource_type, resource_id, project, session):
        seen.append((actor.id, resource_type, resource_id, project))

    monkeypatch.setattr(named_grant_service, "aremove_all_grants", removed)
    await catalog.delete(people["creator"], app["id"], app["etag"], str(uuid4()))
    assert seen == [("u-creator", ResourceType.HOSTED_APPLICATION, as_uuid(app["id"]), TEAM)]


# ---- share link ----


@pytest.fixture
def link_calls(share_calls, monkeypatch):
    """Real link rows on SQLite; the activity insert and the grant removal only record."""
    events, removed = [], []

    async def insert(items, session):
        events.extend(item.event_type for item in items)

    async def remove_link_grants(actor, resource_type, resource_id, link_id, project, session):
        share_calls.append(("remove_link_grants", link_id))
        removed.append((actor.id, resource_type, resource_id, link_id, project))

    monkeypatch.setattr(share_link_service.activity_event_repository, "async_insert_many_strict", insert)
    monkeypatch.setattr(named_grant_service, "aremove_link_grants", remove_link_grants)
    return events, removed


def same_link(left, right):
    """Equal links; SQLite hands timestamps back without the zone the first, unflushed read still has."""

    def plain(payload):
        link = payload["link"]
        return {**link, "created_at": link["created_at"].removesuffix("+00:00")}

    return plain(left) == plain(right)


@pytest.mark.asyncio
async def test_owner_creates_one_link_and_reads_it_back(catalog, people, repository, share_calls, link_calls):
    events, _ = link_calls
    app = await create(catalog, people["creator"])
    assert await catalog.get_share_link(people["creator"], app["id"]) == {"link": None}
    repository.events.clear()
    first = await catalog.create_share_link(people["creator"], app["id"])
    second = await catalog.create_share_link(people["creator"], app["id"])
    assert repository.events[:2] == [("transaction", True), ("managed_app", True)]
    link = first["link"]
    assert set(link) == {"id", "token", "created_at"} and isinstance(link["id"], str) and link["token"]
    assert same_link(second, first)
    assert same_link(await catalog.get_share_link(people["creator"], app["id"]), first)
    assert events == ["hosted_application.share_link.created"]


@pytest.mark.asyncio
async def test_creating_a_link_is_refused_while_suspended_but_reading_and_revoking_are_not(
    catalog, people, engine, share_calls, link_calls
):
    app = await create(catalog, people["creator"])
    created = await catalog.create_share_link(people["creator"], app["id"])
    await set_app(engine, app["id"], security_state="suspended")
    with pytest.raises(HostedApplicationError) as error:
        await catalog.create_share_link(people["creator"], app["id"])
    assert (error.value.code, error.value.status) == ("APP_SUSPENDED", 409)
    assert "share_on" in error.value.message
    assert same_link(await catalog.get_share_link(people["creator"], app["id"]), created)
    assert await catalog.revoke_share_link(people["creator"], app["id"]) == {"link": None}
    assert await catalog.get_share_link(people["creator"], app["id"]) == {"link": None}


@pytest.mark.asyncio
async def test_revoke_tombstones_then_removes_that_links_grants(catalog, people, repository, share_calls, link_calls):
    events, removed = link_calls
    app = await create(catalog, people["creator"])
    created = await catalog.create_share_link(people["creator"], app["id"])
    share_calls.clear()
    repository.events.clear()
    assert await catalog.revoke_share_link(people["creator"], app["id"]) == {"link": None}
    assert repository.events == [("transaction", True), ("managed_app", True)]
    assert events == ["hosted_application.share_link.created", "hosted_application.share_link.revoked"]
    assert [(r[0], r[1], r[2], str(r[3]), r[4]) for r in removed] == [
        ("u-creator", ResourceType.HOSTED_APPLICATION, as_uuid(app["id"]), created["link"]["id"], TEAM)
    ]
    # A new link is a different link; the revoked token never comes back.
    again = (await catalog.create_share_link(people["creator"], app["id"]))["link"]
    assert again["id"] != created["link"]["id"] and again["token"] != created["link"]["token"]


@pytest.mark.asyncio
async def test_revoke_without_a_live_link_is_a_no_op(catalog, people, share_calls, link_calls):
    events, removed = link_calls
    app = await create(catalog, people["creator"])
    assert await catalog.revoke_share_link(people["creator"], app["id"]) == {"link": None}
    assert events == [] and removed == []


@pytest.mark.asyncio
async def test_link_operations_are_owner_only(catalog, people, share_calls, link_calls):
    events, removed = link_calls
    app = await create(catalog, people["creator"])
    operations = (catalog.get_share_link, catalog.create_share_link, catalog.revoke_share_link)
    for operation in operations:
        revoke = operation == catalog.revoke_share_link
        message = catalog_module.NOT_LINK_REVOKER if revoke else "Only the owner can share or revoke access"
        for persona in ("padmin",) if revoke else ("padmin", "admin"):
            with pytest.raises(HostedApplicationError) as error:
                await operation(people[persona], app["id"])
            assert (error.value.status, error.value.message) == (403, message)
        for persona in ("member", "outsider"):
            with pytest.raises(HostedApplicationError) as error:
                await operation(people[persona], app["id"])
            assert error.value.status == 404
    assert events == [] and removed == []


@pytest.mark.asyncio
async def test_a_platform_admin_revokes_a_live_link_without_suspending_the_app(
    catalog, people, engine, repository, link_calls
):
    """AR-1: an admin stops a leaked link in one step (tombstone plus link grants), audited as the admin."""
    events, removed = link_calls
    app = await create(catalog, people["creator"])
    created = await catalog.create_share_link(people["creator"], app["id"])
    repository.events.clear()
    assert await catalog.revoke_share_link(people["admin"], app["id"]) == {"link": None}
    assert repository.events == [("transaction", True), ("managed_app", True)]
    assert events[-1] == "hosted_application.share_link.revoked"
    assert [(r[0], str(r[3])) for r in removed] == [("u-admin", created["link"]["id"])]
    async with AsyncSession(engine) as session:
        link = await session.get(ShareLink, as_uuid(created["link"]["id"]))
        row = await session.get(App, as_uuid(app["id"]))
    assert link.revoked_at is not None and link.revoked_by.id == "u-admin"
    assert row.security_state == "clear"
    # The owner sees no live link; a project admin who is not the owner still may not revoke it.
    assert await catalog.get_share_link(people["creator"], app["id"]) == {"link": None}


@pytest.mark.asyncio
async def test_an_admin_may_revoke_a_link_of_a_suspended_app_but_not_create_or_read_one(
    catalog, people, engine, link_calls
):
    app = await create(catalog, people["creator"])
    await catalog.create_share_link(people["creator"], app["id"])
    await set_app(engine, app["id"], security_state="suspended")
    for operation in (catalog.get_share_link, catalog.create_share_link):
        with pytest.raises(HostedApplicationError) as error:
            await operation(people["admin"], app["id"])
        assert error.value.status == 403
    assert await catalog.revoke_share_link(people["admin"], app["id"]) == {"link": None}
    assert await catalog.get_share_link(people["creator"], app["id"]) == {"link": None}


@pytest.mark.asyncio
async def test_delete_revokes_the_link_before_removing_every_grant(catalog, people, monkeypatch):
    app = await create(catalog, people["creator"])
    order = []

    async def revoke(session, resource_type, app_id, actor, project):
        order.append(("revoke_link", resource_type, actor.id, app_id, project))

    async def removed(actor, resource_type, resource_id, project, session):
        order.append(("remove_all",))

    monkeypatch.setattr(share_link_service, "arevoke", revoke)
    monkeypatch.setattr(named_grant_service, "aremove_all_grants", removed)
    await catalog.delete(people["creator"], app["id"], app["etag"], str(uuid4()))
    assert order == [
        ("revoke_link", ResourceType.HOSTED_APPLICATION, "u-creator", as_uuid(app["id"]), TEAM),
        ("remove_all",),
    ]


@pytest.fixture
def open_calls(link_calls, engine, monkeypatch):
    """Real link and app rows; ``alink_grant`` is faked (its ON CONFLICT ON CONSTRAINT insert is PostgreSQL only).

    The fake inserts the plain link ``permissions`` row the real statement would and records its call.
    """
    calls = []

    async def alink_grant(session, resource_type, resource, user, link_id, project, granted_by):
        calls.append((resource_type, resource.id, user.id, link_id, project, granted_by))
        await session.execute(
            Permission.__table__.insert().values(
                id=str(uuid4()),
                resource_type=resource_type,
                resource_id=resource.id,
                principal_type=PrincipalType.USER,
                principal_id=user.id,
                permission_level=Action.READ,
                grant_source="link",
                source_link_id=link_id,
                created_by=granted_by,
            )
        )
        return True

    monkeypatch.setattr(named_grant_service, "alink_grant", alink_grant)
    monkeypatch.setattr(named_grant_service, "alist_shared_with", REAL_LIST_SHARED_WITH)
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    return calls


async def permission_rows(engine):
    async with engine.begin() as connection:
        return (await connection.execute(sa.select(Permission.__table__))).all()


async def live_token(catalog, people, name="Linked"):
    app = await create(catalog, people["creator"], name=name)
    link = (await catalog.create_share_link(people["creator"], app["id"]))["link"]
    return app, link


@pytest.mark.asyncio
async def test_an_eligible_outsider_opening_a_live_link_becomes_a_view_only_recipient(
    catalog, people, engine, open_calls
):
    app, link = await live_token(catalog, people)
    assert await names(catalog, people["outsider"], scope="shared") == []
    opened = await catalog.open_share_link(people["outsider"], link["token"])
    assert opened == {"app_id": app["id"]} and link["token"] not in str(opened)
    (row,) = await permission_rows(engine)
    assert (row.principal_id, row.grant_source, str(row.source_link_id)) == ("u-outsider", "link", link["id"])
    assert [call[1:3] for call in open_calls] == [(as_uuid(app["id"]), "u-outsider")]
    assert open_calls[0][3] == as_uuid(link["id"]) and open_calls[0][4] == TEAM
    # AR-6: the link's owner creates the row; the opener is only its principal.
    assert open_calls[0][5].id == "u-creator" and row.created_by.id == "u-creator"
    assert await names(catalog, people["outsider"], scope="shared") == ["Linked"]
    shared = await catalog.shared_with(people["creator"], app["id"])
    assert [(u["id"], u["grant_source"]) for u in shared["shared_with"]] == [("u-outsider", "link")]


@pytest.mark.asyncio
async def test_a_caller_who_can_already_read_the_app_gets_it_and_nothing_is_written(
    catalog, people, engine, open_calls
):
    app, link = await live_token(catalog, people)
    await set_app(engine, app["id"], shared=True)
    for persona in ("creator", "member", "padmin"):
        assert await catalog.open_share_link(people[persona], link["token"]) == {"app_id": app["id"]}
    await grant_row(engine, app["id"], "u-outsider")
    assert await catalog.open_share_link(people["outsider"], link["token"]) == {"app_id": app["id"]}
    assert open_calls == [] and len(await permission_rows(engine)) == 1


@pytest.mark.asyncio
async def test_an_external_or_inactive_caller_gets_403_and_no_row(catalog, people, engine, open_calls):
    _, link = await live_token(catalog, people)
    await set_user(engine, "u-outsider", is_active=False)
    for persona in ("external", "outsider"):
        with pytest.raises(HostedApplicationError) as error:
            await catalog.open_share_link(people[persona], link["token"])
        assert error.value.status == 403
    assert open_calls == [] and await permission_rows(engine) == []


@pytest.mark.asyncio
async def test_opening_a_link_bounds_its_lock_wait_before_anything_else(catalog, people, repository, open_calls):
    """AR-9: a long revoke holding the app row cannot stall link opens past the lock timeout."""
    _, link = await live_token(catalog, people)
    repository.events.clear()
    await catalog.open_share_link(people["outsider"], link["token"])
    assert repository.events[:2] == [
        ("transaction", True),
        ("lock_timeout", catalog_module.SHARE_LINK_OPEN_LOCK_TIMEOUT_MS),
    ]
    assert catalog_module.SHARE_LINK_OPEN_LOCK_TIMEOUT_MS == 400
    # Waiting opens must give up well before a hosted pool checkout does, or they can hold the whole pool.
    assert catalog_module.SHARE_LINK_OPEN_LOCK_TIMEOUT_MS < runtime_module.DB_POOL_TIMEOUT_SECONDS * 1000 / 2


@pytest.mark.asyncio
async def test_a_lock_timeout_on_open_answers_a_retryable_busy(catalog, people, monkeypatch):
    from sqlalchemy.exc import DBAPIError

    _, link = await live_token(catalog, people)

    class LockNotAvailable(Exception):
        sqlstate = "55P03"

    async def stalled(*args, **kwargs):
        raise DBAPIError("SELECT ... FOR NO KEY UPDATE", {}, LockNotAvailable())

    monkeypatch.setattr(share_link_service, "aopen", stalled)
    with pytest.raises(HostedApplicationError) as error:
        await catalog.open_share_link(people["outsider"], link["token"])
    assert (error.value.code, error.value.status) == ("SERVICE_BUSY", 503)


@pytest.mark.asyncio
async def test_other_database_errors_on_open_are_not_turned_into_busy(catalog, people, monkeypatch):
    from sqlalchemy.exc import DBAPIError

    _, link = await live_token(catalog, people)

    async def broken(*args, **kwargs):
        raise DBAPIError("SELECT 1", {}, Exception("boom"))

    monkeypatch.setattr(share_link_service, "aopen", broken)
    with pytest.raises(DBAPIError):
        await catalog.open_share_link(people["outsider"], link["token"])


async def set_user(engine, user_id, **values):
    async with engine.begin() as connection:
        await connection.execute(UserDB.__table__.update().where(UserDB.id == user_id).values(**values))


async def assert_not_available(catalog, actor, token):
    with pytest.raises(HostedApplicationError) as error:
        await catalog.open_share_link(actor, token)
    return (error.value.code, error.value.status, error.value.message)


@pytest.mark.asyncio
async def test_every_unavailable_link_gives_the_same_404(catalog, people, engine, open_calls):
    app, live = await live_token(catalog, people, "Live")
    revoked_app, revoked = await live_token(catalog, people, "Revoked")
    await catalog.revoke_share_link(people["creator"], revoked_app["id"])
    purging_app, purging = await live_token(catalog, people, "Purging")
    await set_app(engine, purging_app["id"], lifecycle_state="purging", deleted_at=datetime.now(timezone.utc))
    suspended_app, suspended = await live_token(catalog, people, "Suspended")
    await set_app(engine, suspended_app["id"], security_state="suspended")
    answers = {
        await assert_not_available(catalog, people["outsider"], token)
        for token in ("not-a-token", "", revoked["token"], purging["token"], suspended["token"])
    }
    assert len(answers) == 1
    ((code, status, message),) = answers
    assert (code, status) == ("SHARE_LINK_NOT_AVAILABLE", 404)
    assert all(word not in message for word in (revoked["token"], "revoked", "suspended", "purging"))
    assert open_calls == [] and await permission_rows(engine) == []


@pytest.mark.asyncio
async def test_a_link_revoked_between_lookup_and_lock_gives_404_and_no_row(
    catalog, people, engine, open_calls, monkeypatch
):
    _, link = await live_token(catalog, people)
    plain_lock = FakeTx.lock_app

    async def revoking_lock(self, query):
        await self.session.execute(
            ShareLink.__table__.update().where(ShareLink.id == as_uuid(link["id"])).values(revoked_at=datetime.now())
        )
        return await plain_lock(self, query)

    monkeypatch.setattr(FakeTx, "lock_app", revoking_lock)
    code, status, _ = await assert_not_available(catalog, people["outsider"], link["token"])
    assert (code, status) == ("SHARE_LINK_NOT_AVAILABLE", 404)
    assert open_calls == [] and await permission_rows(engine) == []


@pytest.mark.asyncio
async def test_open_takes_the_app_lock_but_no_quota_lock(catalog, people, repository, open_calls):
    _, link = await live_token(catalog, people)
    repository.events.clear()
    await catalog.open_share_link(people["outsider"], link["token"])
    assert not any(event[0] == "lock_quota" for event in repository.events)
