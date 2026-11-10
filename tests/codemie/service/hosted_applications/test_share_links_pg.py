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

"""Share links end to end on real PostgreSQL (EPMCDME-15659, AC2-3, 5-9, 12, 14).

Skipped, through the ``database`` fixture, unless HOSTED_TEST_DATABASE_URL points at an isolated PostgreSQL
database. Every step runs the real catalog, grant statements, row locks and access rules; only the activity insert
is replaced by a recorder, because the activity table is not part of the hosted test schema.

The AC14 interleavings are injected, not raced: a wrapper around ``share_link_service.alive_by_token`` (step 1) or
``HostedTransaction.lock_app`` (step 2) runs the competing change in its own committed transaction exactly once,
then lets the open continue.
"""

from collections import Counter
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs import config
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.repository.hosted_applications.repository import HostedTransaction
from codemie.rest_api.models.hosted_application import HostedApplications as App
from codemie.rest_api.models.hosted_application_requests import VersionSelection
from codemie.rest_api.models.permission import Permission, ResourceType, ShareLink
from codemie.rest_api.models.user_management import UserDB
from codemie.rest_api.security.user import User
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.permission import named_grant_service, share_link_service
from tests.codemie.service.hosted_applications.test_worker import queued

GRANT_ADDED = "hosted_application.grant.added"
GRANT_REMOVED = "hosted_application.grant.removed"
LINK_CREATED = "hosted_application.share_link.created"
LINK_REVOKED = "hosted_application.share_link.revoked"
# ``viewer`` and ``other-owner`` are project members of an unshared app, ``outsider`` is in no project: none of them
# reads the app without a grant.
FIRST, SECOND, THIRD = "viewer", "outsider", "other-owner"


@pytest.fixture
def events(monkeypatch):
    recorded = []

    async def record(batch, session):
        recorded.extend(batch)
        return []

    monkeypatch.setattr(activity_event_repository, "async_insert_many_strict", record)
    return recorded


@pytest_asyncio.fixture
async def world(database, launch, events, monkeypatch):
    engine, _ = database
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    async with engine.begin() as connection:
        await connection.execute(sa.text("CREATE SCHEMA IF NOT EXISTS codemie"))
        await connection.run_sync(Permission.__table__.create, checkfirst=True)
        await connection.execute(
            UserDB.__table__.insert().values(
                id=SECOND,
                username=SECOND,
                email=f"{SECOND}@example.com",
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
    return SimpleNamespace(engine=engine, handoff=handoff, service=service, worker=worker, events=events)


async def active_app(world, owner):
    """An active, unshared application of ``owner``: ``(app_id, version_id)``."""
    app, queued_version = await queued(world.service, owner)
    assert await world.worker.run_one()
    current = await world.service.get_app(owner, app["id"])
    await world.service.activate_version(
        owner, app["id"], VersionSelection(version_id=UUID(queued_version["version_id"])), current["etag"], str(uuid4())
    )
    return app["id"], UUID(queued_version["version_id"])


async def link_of(world, owner, app_id):
    return (await world.service.create_share_link(owner, app_id))["link"]


async def open_link(world, who, token):
    return await world.service.open_share_link(who, token)


async def rows(world, app_id):
    """The app's permission rows as ``{principal_id: (grant_source, source_link_id)}``."""
    async with world.engine.connect() as connection:
        found = await connection.execute(
            select(Permission.__table__).where(Permission.__table__.c.resource_id == UUID(str(app_id)))
        )
        return {row.principal_id: (row.grant_source, row.source_link_id) for row in found}


async def link_rows(world, app_id):
    async with world.engine.connect() as connection:
        found = await connection.execute(
            select(ShareLink.__table__)
            .where(ShareLink.__table__.c.resource_id == UUID(str(app_id)))
            .order_by(ShareLink.__table__.c.created_at)
        )
        return list(found)


def counts(world):
    return Counter(event.event_type for event in world.events)


async def sees(world, who, app_id, version_id):
    """True when the app opens by id AND through the gateway for ``who`` (never a half answer)."""
    by_id = await refused(world.service.get_app(who, str(app_id)))
    try:
        ticket = await world.handoff.issue(who, str(app_id), purpose="published_view")
        cookie, _ = await world.handoff.consume(ticket["ticket"], world.handoff.host(version_id))
        await world.handoff.authorize(cookie, version_id)
    except HostedApplicationError as error:
        assert error.status in (401, 404), (error.code, error.status)
        assert by_id, "the gateway refused while open-by-id answered"
        return False
    assert not by_id, "open-by-id refused while the gateway answered"
    return True


async def refused(call):
    try:
        await call
    except HostedApplicationError as error:
        assert error.status in (401, 404), (error.code, error.status)
        return True
    return False


async def listed(world, who, scope):
    return [item["id"] for item in (await world.service.list_apps(who, scope=scope))["items"]]


async def not_available(world, who, token):
    with pytest.raises(HostedApplicationError) as error:
        await open_link(world, who, token)
    assert (error.value.code, error.value.status) == ("SHARE_LINK_NOT_AVAILABLE", 404)


@pytest.mark.asyncio
async def test_open_grants_access_by_id_and_gateway_and_revoke_ends_it_but_keeps_by_name_rows(world, identity):
    owner = identity()
    app_id, version_id = await active_app(world, owner)
    first, second, stranger = identity(FIRST), identity(SECOND), identity(THIRD)
    link = await link_of(world, owner, app_id)
    assert not await sees(world, second, app_id, version_id)

    assert await open_link(world, first, link["token"]) == {"app_id": app_id}
    assert await open_link(world, second, link["token"]) == {"app_id": app_id}

    # AC2: one link row per recipient with the link's id, one grant.added event each, listed and shared-with.
    link_id = UUID(link["id"])
    assert await rows(world, app_id) == {FIRST: ("link", link_id), SECOND: ("link", link_id)}
    added = [event for event in world.events if event.event_type == GRANT_ADDED]
    assert sorted(event.attributes["principal_id"] for event in added) == sorted([FIRST, SECOND])
    assert {event.attributes["grant_source"] for event in added} == {"link"}
    assert {event.attributes["source_link_id"] for event in added} == {link["id"]}
    assert await listed(world, second, "shared") == [app_id]
    shared_with = (await world.service.shared_with(owner, app_id))["shared_with"]
    assert {item["id"]: item["grant_source"] for item in shared_with} == {FIRST: "link", SECOND: "link"}

    # AC3: later access by id and through the gateway needs no link.
    assert await sees(world, first, app_id, version_id) and await sees(world, second, app_id, version_id)

    # AC5: a by-name grant turns FIRST's row into a name row.
    await world.service.share(owner, app_id, [FIRST])
    assert (await rows(world, app_id))[FIRST] == ("name", None)

    # AC6: revoke -> 404 for the token, only SECOND's link row goes (one event), by-name row stays.
    world.events.clear()
    await world.service.revoke_share_link(owner, app_id)
    await not_available(world, stranger, link["token"])
    assert await rows(world, app_id) == {FIRST: ("name", None)}
    assert counts(world) == {LINK_REVOKED: 1, GRANT_REMOVED: 1}
    removed = [event for event in world.events if event.event_type == GRANT_REMOVED]
    assert removed[0].attributes["principal_id"] == SECOND
    assert not await sees(world, second, app_id, version_id)
    assert await listed(world, second, "shared") == []
    assert await sees(world, first, app_id, version_id)
    assert await listed(world, first, "shared") == [app_id]
    stale = await link_rows(world, app_id)
    assert len(stale) == 1 and stale[0].revoked_at is not None


@pytest.mark.asyncio
async def test_owner_unshare_ends_a_link_recipients_access_and_reopening_adds_a_new_link_row(world, identity):
    owner = identity()
    app_id, version_id = await active_app(world, owner)
    who = identity(SECOND)
    link = await link_of(world, owner, app_id)
    await open_link(world, who, link["token"])
    assert await sees(world, who, app_id, version_id)

    await world.service.unshare(owner, app_id, [SECOND])
    assert await rows(world, app_id) == {}
    assert not await sees(world, who, app_id, version_id)

    await open_link(world, who, link["token"])
    assert await rows(world, app_id) == {SECOND: ("link", UUID(link["id"]))}
    assert await sees(world, who, app_id, version_id)
    assert sum(1 for event in world.events if event.event_type == GRANT_ADDED) == 2


@pytest.mark.asyncio
async def test_a_new_link_after_revoke_has_a_new_id_and_token_and_the_old_token_stays_404(world, identity):
    owner = identity()
    app_id, _ = await active_app(world, owner)
    who = identity(FIRST)
    old = await link_of(world, owner, app_id)
    await world.service.revoke_share_link(owner, app_id)

    new = await link_of(world, owner, app_id)

    assert new["id"] != old["id"] and new["token"] != old["token"]
    await not_available(world, who, old["token"])
    assert await open_link(world, who, new["token"]) == {"app_id": app_id}
    assert (await rows(world, app_id))[FIRST] == ("link", UUID(new["id"]))
    assert len(await link_rows(world, app_id)) == 2


@pytest.mark.asyncio
async def test_delete_revokes_the_link_and_removes_its_grants_with_events(world, identity):
    owner = identity()
    app_id, _ = await active_app(world, owner)
    link = await link_of(world, owner, app_id)
    await open_link(world, identity(FIRST), link["token"])
    await open_link(world, identity(SECOND), link["token"])
    current = await world.service.get_app(owner, app_id)
    world.events.clear()

    assert (await world.service.delete(owner, app_id, current["etag"], str(uuid4())))["state"] == "purging"

    assert await rows(world, app_id) == {}
    (stored,) = await link_rows(world, app_id)
    assert stored.id == UUID(link["id"]) and stored.revoked_at is not None
    assert counts(world) == {LINK_REVOKED: 1, GRANT_REMOVED: 2}
    await not_available(world, identity(THIRD), link["token"])


@pytest.mark.asyncio
async def test_a_platform_admin_deleting_one_permission_removes_a_single_link_grant(world, identity):
    owner = identity()
    app_id, _ = await active_app(world, owner)
    link = await link_of(world, owner, app_id)
    await open_link(world, identity(FIRST), link["token"])
    await open_link(world, identity(SECOND), link["token"])
    async with world.engine.connect() as connection:
        found = await connection.execute(
            select(Permission.__table__.c.id).where(
                Permission.__table__.c.resource_id == UUID(app_id), Permission.__table__.c.principal_id == SECOND
            )
        )
        permission_id = found.scalar_one()
    admin = User(id="platform-admin", username="platform-admin", name="Platform Admin")
    world.events.clear()

    # The service function behind ``DELETE /v1/permissions/{id}``; its sync wrapper only adds the descriptor check.
    async with AsyncSession(world.engine, expire_on_commit=False) as session, session.begin():
        permission = await session.get(Permission, permission_id)
        await named_grant_service.arevoke_permission(admin, permission, session, "demo")

    assert permission.resource_type == ResourceType.HOSTED_APPLICATION
    assert await rows(world, app_id) == {FIRST: ("link", UUID(link["id"]))}
    assert counts(world) == {GRANT_REMOVED: 1}
    assert world.events[0].attributes["principal_id"] == SECOND
    assert (await link_rows(world, app_id))[0].revoked_at is None


@pytest.mark.asyncio
async def test_a_revoke_committed_between_lookup_and_lock_leaves_no_link_row(world, identity, monkeypatch):
    owner = identity()
    app_id, _ = await active_app(world, owner)
    link = await link_of(world, owner, app_id)
    lookup, interleaved = share_link_service.alive_by_token, []

    async def lookup_then_revoke(session, resource_type, token):
        found = await lookup(session, resource_type, token)
        if not interleaved:
            interleaved.append(True)
            await world.service.revoke_share_link(owner, app_id)
        return found

    monkeypatch.setattr(share_link_service, "alive_by_token", lookup_then_revoke)

    await not_available(world, identity(SECOND), link["token"])

    assert interleaved and await rows(world, app_id) == {}
    assert LINK_REVOKED in counts(world) and GRANT_ADDED not in counts(world)


@pytest.mark.asyncio
async def test_a_suspend_committed_between_lookup_and_lock_leaves_no_link_row(world, identity, monkeypatch):
    owner = identity()
    app_id, _ = await active_app(world, owner)
    link = await link_of(world, owner, app_id)
    lookup, interleaved = share_link_service.alive_by_token, []

    async def lookup_then_suspend(session, resource_type, token):
        found = await lookup(session, resource_type, token)
        if not interleaved:
            interleaved.append(True)
            async with world.engine.begin() as connection:
                await connection.execute(
                    App.__table__.update().where(App.__table__.c.id == UUID(app_id)).values(security_state="suspended")
                )
        return found

    monkeypatch.setattr(share_link_service, "alive_by_token", lookup_then_suspend)

    await not_available(world, identity(SECOND), link["token"])

    assert interleaved and await rows(world, app_id) == {}
    assert GRANT_ADDED not in counts(world)


@pytest.mark.asyncio
async def test_a_by_name_grant_racing_an_open_leaves_exactly_one_name_row(world, identity, monkeypatch):
    owner = identity()
    app_id, _ = await active_app(world, owner)
    link = await link_of(world, owner, app_id)
    lock_app, interleaved = HostedTransaction.lock_app, []

    async def share_then_lock(self, query):
        if not interleaved:
            interleaved.append(True)
            await world.service.share(owner, app_id, [FIRST])
        return await lock_app(self, query)

    monkeypatch.setattr(HostedTransaction, "lock_app", share_then_lock)

    assert await open_link(world, identity(FIRST), link["token"]) == {"app_id": app_id}

    assert interleaved and await rows(world, app_id) == {FIRST: ("name", None)}
    assert {event.attributes["grant_source"] for event in world.events if event.event_type == GRANT_ADDED} == {"name"}
