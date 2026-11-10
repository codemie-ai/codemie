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

"""Generic share links: one live link per resource, tombstoned on revoke, looked up only by SHA-256 (SQLite)."""

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.models.permission import ResourceType, ShareLink
from codemie.rest_api.models.user_management import UserDB
from codemie.service.activity.activity_models import ActivityDomain
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.permission import named_grant_service, share_link_service, share_link_types

HOSTED = ResourceType.HOSTED_APPLICATION
ACTOR = SimpleNamespace(
    id="u-1", username="owner", name="Owner", user_type="regular", is_admin=False, is_maintainer=False
)
OPENER = SimpleNamespace(
    id="u-2", username="opener", name="Opener", user_type="regular", is_admin=False, is_maintainer=False
)
PROJECT = "team-a"


def _app(owner="u-1", security_state="clear"):
    return SimpleNamespace(id=uuid4(), created_by=SimpleNamespace(id=owner), security_state=security_state)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    metadata = sa.MetaData()
    table = ShareLink.__table__
    sa.Table(
        table.name,
        metadata,
        *[
            sa.Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable, unique=c.unique)
            for c in table.columns
        ],
        sa.Index("uq_live", "resource_type", "resource_id", unique=True, sqlite_where=sa.text("revoked_at IS NULL")),
    )
    users = UserDB.__table__.to_metadata(metadata)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        for uid, active in (("u-1", True), ("u-2", True), ("u-gone", False)):
            await connection.execute(
                users.insert().values(
                    id=uid,
                    username=uid,
                    email=f"{uid}@example.com",
                    user_type="regular",
                    is_active=active,
                    is_admin=False,
                    is_maintainer=False,
                    is_auditor=False,
                    auth_source="local",
                    email_verified=False,
                )
            )
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()


@pytest.fixture
def events(monkeypatch):
    recorded = []

    async def record(batch, session):
        recorded.extend(batch)
        return []

    monkeypatch.setattr(activity_event_repository, "async_insert_many_strict", record)
    return recorded


@pytest.fixture
def removed(monkeypatch):
    calls = []

    async def remove_link_grants(actor, resource_type, resource_id, link_id, project, session):
        calls.append((actor.id, resource_type, resource_id, link_id, project))

    monkeypatch.setattr(named_grant_service, "aremove_link_grants", remove_link_grants)
    return calls


# ---- registry ------------------------------------------------------------------------------------------------------


def test_only_hosted_applications_have_link_sharing():
    assert set(share_link_types.SHARE_LINK_TYPES) == {HOSTED}
    with pytest.raises(KeyError):
        share_link_types.link_descriptor_for(ResourceType.ASSISTANT)


def test_every_link_type_is_a_named_grant_type():
    """A link open and revoke go through the named-grant service and read the type's grant descriptor (finding 1)."""
    from codemie.service.permission.named_grant_types import NAMED_GRANT_TYPES

    assert set(share_link_types.SHARE_LINK_TYPES) <= set(NAMED_GRANT_TYPES)
    for resource_type in share_link_types.SHARE_LINK_TYPES:
        assert share_link_types.link_descriptor_for(resource_type) is not None


def test_link_descriptor_keeps_only_link_specific_fields():
    """Domain, entity type, owner rule, pre-check, audit attributes and recipient rule live in the grant descriptor."""
    from dataclasses import fields

    names = {field.name for field in fields(share_link_types.ShareLinkDescriptor)}
    for shared in ("activity_domain", "activity_entity_type", "is_owner", "before_create", "audit_attributes"):
        assert shared not in names, shared


def test_descriptor_import_is_lazy():
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from codemie.service.permission import share_link_types as t\n"
        "assert 'codemie.service.hosted_applications.share_link_descriptor' not in sys.modules\n"
        "from codemie.rest_api.models.permission import ResourceType\n"
        "t.link_descriptor_for(ResourceType.HOSTED_APPLICATION)\n"
        "assert 'codemie.service.hosted_applications.share_link_descriptor' in sys.modules\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# ---- create, read, revoke -------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_the_same_live_link_with_one_created_event(session, events):
    app = _app()

    first = await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)
    second = await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)

    assert second.id == first.id and second.token == first.token
    assert (first.resource_type, first.resource_id) == (HOSTED, app.id)
    assert first.token_sha256 == hashlib.sha256(first.token.encode()).hexdigest()
    assert [event.event_type for event in events] == ["hosted_application.share_link.created"]
    assert events[0].domain == ActivityDomain.HOSTED_APPLICATION
    attributes = events[0].attributes
    assert attributes["link_id"] == str(first.id)
    assert (attributes["resource_type"], attributes["resource_id"]) == ("hosted_application", str(app.id))
    assert attributes["app_id"] == str(app.id)
    assert attributes["project"] == PROJECT and attributes["actor_id"] == ACTOR.id


@pytest.mark.asyncio
async def test_create_is_owner_only_and_refused_while_suspended(session, events):
    with pytest.raises(HostedApplicationError) as not_owner:
        await share_link_service.acreate(session, HOSTED, _app(owner="someone-else"), ACTOR, PROJECT)
    assert not_owner.value.status == 403
    with pytest.raises(HostedApplicationError) as suspended:
        await share_link_service.acreate(session, HOSTED, _app(security_state="suspended"), ACTOR, PROJECT)
    assert (suspended.value.code, suspended.value.status) == ("APP_SUSPENDED", 409)
    assert events == [] and (await session.execute(sa.select(ShareLink))).all() == []


@pytest.mark.asyncio
async def test_link_owner_rule_and_events_come_from_the_grant_descriptor(session, events, monkeypatch):
    from codemie.service.permission import named_grant_types

    grant = named_grant_types.descriptor_for(HOSTED)
    custom = replace(
        grant,
        is_owner=lambda resource, actor: actor.id == "u-2",
        audit_attributes=lambda resource_id: {"marker": str(resource_id)},
    )
    monkeypatch.setitem(named_grant_types._cache, HOSTED, custom)
    app = _app(owner="u-1")

    with pytest.raises(HostedApplicationError) as refused:
        await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)
    assert refused.value.status == 403 and refused.value.message == grant.not_owner_message
    link = await share_link_service.acreate(session, HOSTED, app, OPENER, PROJECT)

    (event,) = events
    assert event.domain == grant.activity_domain and event.entity_type == grant.activity_entity_type
    assert event.attributes["marker"] == str(app.id) and event.attributes["link_id"] == str(link.id)


def test_hosted_grant_events_carry_app_id_like_link_events():
    from codemie.service.permission.named_grant_types import descriptor_for

    app_id = uuid4()
    assert descriptor_for(HOSTED).audit_attributes(app_id) == {"app_id": str(app_id)}


@pytest.mark.asyncio
async def test_revoke_tombstones_the_link_and_removes_its_grants_in_one_step(session, events, removed):
    app = _app()
    link = await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)
    events.clear()

    revoked_id = await share_link_service.arevoke(session, HOSTED, app.id, ACTOR, PROJECT)

    assert revoked_id == link.id
    row = await share_link_service.areload(session, link.id)
    assert row.revoked_at is not None and row.revoked_by.id == ACTOR.id
    assert await share_link_service.aget_live(session, HOSTED, app.id) is None
    assert [event.event_type for event in events] == ["hosted_application.share_link.revoked"]
    assert events[0].attributes["link_id"] == str(link.id)
    # AR-11: the link's recipients go with it, so no caller can revoke a link and leave its grants behind.
    assert removed == [(ACTOR.id, HOSTED, app.id, link.id, PROJECT)]


@pytest.mark.asyncio
async def test_revoke_without_live_link_returns_none_and_writes_nothing(session, events, removed):
    assert await share_link_service.arevoke(session, HOSTED, uuid4(), ACTOR, PROJECT) is None
    assert events == [] and removed == []


@pytest.mark.asyncio
async def test_checked_revoke_refuses_anyone_but_owner_or_admin_before_any_change(session, events, removed):
    app = _app()
    await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)
    events.clear()
    with pytest.raises(HostedApplicationError) as error:
        await share_link_service.arevoke_checked(session, HOSTED, app, OPENER, PROJECT)
    assert error.value.status == 403
    assert events == [] and removed == []
    assert await share_link_service.aget_live(session, HOSTED, app.id) is not None


@pytest.mark.asyncio
async def test_owner_revoke_is_allowed_while_suspended(session, events, removed):
    app = _app()
    link = await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)
    app.security_state = "suspended"
    assert await share_link_service.arevoke_checked(session, HOSTED, app, ACTOR, PROJECT) == link.id


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["is_admin", "is_maintainer"])
async def test_a_platform_admin_or_maintainer_revokes_as_the_actor(session, events, removed, flag):
    """AR-1: the hosted ``may_revoke`` rule is owner OR ``grant_remover``; the admin is the audit actor."""
    app = _app()
    link = await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)
    events.clear()
    admin = SimpleNamespace(
        id="u-9",
        username="admin",
        name="Admin",
        user_type="regular",
        is_admin=flag == "is_admin",
        is_maintainer=flag == "is_maintainer",
    )
    assert await share_link_service.arevoke_checked(session, HOSTED, app, admin, PROJECT) == link.id
    assert (await share_link_service.areload(session, link.id)).revoked_by.id == "u-9"
    assert removed == [("u-9", HOSTED, app.id, link.id, PROJECT)]


@pytest.mark.asyncio
async def test_an_external_admin_may_not_revoke(session, events, removed):
    app = _app()
    await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)
    admin = SimpleNamespace(id="u-9", username="x", name="x", user_type="external", is_admin=True, is_maintainer=False)
    with pytest.raises(HostedApplicationError):
        await share_link_service.arevoke_checked(session, HOSTED, app, admin, PROJECT)


@pytest.mark.asyncio
async def test_new_link_after_revoke_has_a_new_token(session, events, removed):
    app = _app()
    old = await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)
    old_token = old.token
    await share_link_service.arevoke(session, HOSTED, app.id, ACTOR, PROJECT)

    new = await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)

    assert new.id != old.id and new.token != old_token
    assert await share_link_service.alive_by_token(session, HOSTED, old_token) is None
    assert (await share_link_service.alive_by_token(session, HOSTED, new.token)).id == new.id


@pytest.mark.asyncio
async def test_a_token_opens_only_its_own_resource_type(session, events):
    link = await share_link_service.acreate(session, HOSTED, _app(), ACTOR, PROJECT)
    assert await share_link_service.alive_by_token(session, ResourceType.ASSISTANT, link.token) is None


def test_token_lookup_binds_only_the_digest():
    token = "secret-token-value"
    compiled = share_link_service.live_link_by_token_statement(HOSTED, token).compile(dialect=postgresql.dialect())

    assert token not in compiled.params.values()
    assert share_link_service.token_digest(token) in compiled.params.values()


@pytest.mark.asyncio
async def test_no_event_attribute_contains_the_token(session, events, removed):
    app = _app()
    link = await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)
    await share_link_service.arevoke(session, HOSTED, app.id, ACTOR, PROJECT)

    assert len(events) == 2
    for event in events:
        assert link.token not in repr(event.model_dump())


# ---- open -----------------------------------------------------------------------------------------------------------


class _NotAvailable(Exception):
    pass


class _Refused(Exception):
    pass


@pytest.fixture
def opening(monkeypatch):
    """The hosted descriptor with its readable check and lock replaced by recorders; grants only recorded."""
    state = SimpleNamespace(readable=False, locked=True, lock_hook=None, grants=[], locks=[])

    async def areadable(context, resource_id, opener_id):
        return state.readable

    async def alock(context, resource_id):
        state.locks.append(resource_id)
        if state.lock_hook:
            await state.lock_hook()
        return SimpleNamespace(id=resource_id, project=PROJECT) if state.locked else None

    async def alink_grant(session, resource_type, resource, user, link_id, project, granted_by):
        state.grants.append((resource_type, resource.id, user.id, link_id, project, granted_by))
        return True

    descriptor = replace(
        share_link_types.link_descriptor_for(HOSTED),
        areadable=areadable,
        alock=alock,
        not_available=_NotAvailable,
        opener_refused=_Refused,
    )
    monkeypatch.setattr(share_link_service, "link_descriptor_for", lambda _type: descriptor)
    monkeypatch.setattr(named_grant_service, "alink_grant", alink_grant)
    return state


@pytest.mark.asyncio
async def test_open_grants_the_opener_with_the_link_owner_as_the_grant_creator(session, events, opening):
    app = _app()
    link = await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)

    opened = await share_link_service.aopen(session, HOSTED, link.token, OPENER, context="ctx")

    assert opened == app.id
    ((resource_type, resource_id, user_id, link_id, project, granted_by),) = opening.grants
    assert (resource_type, resource_id, user_id, link_id, project) == (HOSTED, app.id, OPENER.id, link.id, PROJECT)
    # AR-6: the grant row is created by the link's owner; the opener stays the principal and the audit actor.
    assert granted_by.id == ACTOR.id


@pytest.mark.asyncio
async def test_open_by_someone_who_can_already_read_writes_nothing(session, events, opening):
    app = _app()
    link = await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)
    opening.readable = True
    assert await share_link_service.aopen(session, HOSTED, link.token, OPENER, context="ctx") == app.id
    assert opening.grants == [] and opening.locks == []


@pytest.mark.asyncio
async def test_open_refuses_an_ineligible_opener_before_reading_the_link(session, events, opening):
    link = await share_link_service.acreate(session, HOSTED, _app(), ACTOR, PROJECT)
    gone = SimpleNamespace(id="u-gone", username="gone", name="Gone")
    with pytest.raises(_Refused):
        await share_link_service.aopen(session, HOSTED, link.token, gone, context="ctx")
    assert opening.grants == []


@pytest.mark.asyncio
async def test_open_admits_openers_by_the_types_recipient_rule(session, events, opening, monkeypatch):
    """Link open reads the grant descriptor's recipient rule instead of a hard-coded internal-user check."""
    from codemie.service.permission import named_grant_types

    link = await share_link_service.acreate(session, HOSTED, _app(), ACTOR, PROJECT)
    only_gone = replace(named_grant_types.descriptor_for(HOSTED), recipient=lambda users: users.id == "u-gone")
    monkeypatch.setitem(named_grant_types._cache, HOSTED, only_gone)
    gone = SimpleNamespace(id="u-gone", username="gone", name="Gone")

    await share_link_service.aopen(session, HOSTED, link.token, gone, context="ctx")
    with pytest.raises(_Refused):
        await share_link_service.aopen(session, HOSTED, link.token, OPENER, context="ctx")
    assert [grant[2] for grant in opening.grants] == ["u-gone"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unknown", "lock_refused", "revoked_before_lock"])
async def test_every_unusable_link_answers_not_available(session, events, removed, opening, case):
    app = _app()
    link = await share_link_service.acreate(session, HOSTED, app, ACTOR, PROJECT)
    token = link.token
    if case == "unknown":
        token = "not-a-token"
    elif case == "lock_refused":
        opening.locked = False
    else:

        async def revoke():
            link.revoked_at = datetime.now(timezone.utc)
            await session.flush()

        opening.lock_hook = revoke
    with pytest.raises(_NotAvailable):
        await share_link_service.aopen(session, HOSTED, token, OPENER, context="ctx")
    assert opening.grants == []
