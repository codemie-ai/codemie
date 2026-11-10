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

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.models.user_management import UserDB
from codemie.service.hosted_applications.directory import HostedIdentityDirectory

ISSUER = "test-issuer"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


LOCAL_CONFIG = SimpleNamespace(JWT_ISSUER=ISSUER)


def make_user(principal, **changes):
    values = {
        "id": principal,
        "username": principal,
        "email": principal + "@example.test",
        "name": principal.title(),
        "is_active": True,
        "user_type": "regular",
        "auth_source": "local",
    }
    return UserDB(**(values | changes))


class FakeUsers:
    """Sessions factory over in-memory ``UserDB`` rows; counts queries, can fail (at once or after N queries), can hold queries on a gate."""

    def __init__(self, *principals):
        self.rows = {principal: make_user(principal) for principal in principals}
        self.queries = 0
        self.failing = False
        self.fail_after = None
        self.gate = None

    def __call__(self):
        return self._session()

    @asynccontextmanager
    async def _session(self):
        yield self

    async def execute(self, statement):
        self.queries += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.failing or (self.fail_after is not None and self.queries > self.fail_after):
            raise OperationalError("SELECT", {}, Exception("database down"))
        wanted = {
            value
            for parameter in statement.compile().params.values()
            for value in (parameter if isinstance(parameter, list | tuple | set) else [parameter])
        }
        if "!=" in str(statement):
            # A people search: every user except the actor, whose id is the only bound id.
            rows = [row for principal, row in self.rows.items() if principal not in wanted]
        else:
            rows = [row for principal, row in self.rows.items() if principal in wanted]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


@pytest.mark.asyncio
async def test_directory_outage_never_reuses_a_positive_membership():
    users, clock = FakeUsers("viewer"), Clock()
    directory = HostedIdentityDirectory(users, LOCAL_CONFIG, clock=clock)
    await directory.resolve("viewer")
    clock.now += 5.01
    users.failing = True
    with pytest.raises(HostedApplicationError) as error:
        await directory.resolve("viewer")
    assert error.value.status == 503
    users.failing = False
    uncached = HostedIdentityDirectory(users, LOCAL_CONFIG, cache_seconds=0)
    await uncached.resolve("viewer")
    users.failing = True
    with pytest.raises(HostedApplicationError) as error:
        await uncached.resolve("viewer")
    assert error.value.status == 503


@pytest.mark.asyncio
async def test_directory_unavailability_is_never_cached():
    users, clock = FakeUsers("viewer"), Clock()
    users.failing = True
    directory = HostedIdentityDirectory(users, LOCAL_CONFIG, clock=clock)
    with pytest.raises(HostedApplicationError) as error:
        await directory.resolve("viewer")
    assert error.value.status == 503 and directory.cached("viewer") is None
    users.failing = False
    assert (await directory.resolve("viewer"))["display_name"] == "Viewer"
    assert users.queries == 2


@pytest.mark.asyncio
async def test_cached_probe_reports_fresh_decisions_without_directory_io():
    users, clock = FakeUsers("viewer"), Clock()
    directory = HostedIdentityDirectory(users, LOCAL_CONFIG, clock=clock)
    assert directory.cached("viewer") is None
    await directory.resolve("viewer")
    with pytest.raises(HostedApplicationError):
        await directory.resolve("gone")
    queries = users.queries
    assert directory.cached("viewer") is True
    assert directory.cached("gone") is False
    clock.now += 5.1
    assert directory.cached("viewer") is None
    assert users.queries == queries
    uncached = HostedIdentityDirectory(users, LOCAL_CONFIG, cache_seconds=0)
    await uncached.resolve("viewer")
    assert uncached.cached("viewer") is None and not uncached.memberships


@pytest.mark.asyncio
async def test_concurrent_misses_share_one_lookup_and_the_cache_is_bounded():
    users, clock = FakeUsers("viewer", "second", "third"), Clock()
    users.gate = asyncio.Event()
    directory = HostedIdentityDirectory(users, LOCAL_CONFIG, clock=clock)
    directory.cache_entries = 2
    waiting = [asyncio.create_task(directory.resolve("viewer")) for _ in range(10)]
    await asyncio.sleep(0.05)
    # A cancelled caller never cancels the lookup that other requests are waiting for.
    waiting[0].cancel()
    users.gate.set()
    results = await asyncio.gather(*waiting[1:])
    assert all(result["principal_id"] == "viewer" for result in results)
    assert users.queries == 1
    for principal in ("second", "third"):
        await directory.resolve(principal)
    assert len(directory.memberships) == 2 and directory.cached("viewer") is None


@pytest.mark.asyncio
async def test_database_error_is_unavailable_and_not_cached():
    users = FakeUsers("viewer", "actor")
    users.failing = True
    directory = HostedIdentityDirectory(users, LOCAL_CONFIG)
    with pytest.raises(HostedApplicationError) as error:
        await directory.resolve("viewer")
    assert error.value.status == 503 and error.value.code == "AUTHORIZATION_UNAVAILABLE"
    assert not directory.memberships
    users.failing = False
    queries = users.queries
    assert (await directory.resolve("viewer"))["principal_id"] == "viewer"
    assert users.queries == queries + 1


@pytest.mark.asyncio
async def test_users_of_every_auth_source_are_members(database, identity):
    engine, _ = database  # the fixture already created ``users``
    ids = {source: str(uuid4()) for source in ("local", "keycloak", "oidc")}
    async with AsyncSession(engine) as session, session.begin():
        for source, principal in ids.items():
            session.add(
                UserDB(
                    id=principal,
                    username=source,
                    email=source + "@example.test",
                    name=source.title(),
                    is_active=True,
                    user_type="regular",
                    auth_source=source,
                    date=datetime.now(),
                    update_date=datetime.now(),
                )
            )

    @asynccontextmanager
    async def sessions():
        async with AsyncSession(engine) as session:
            yield session

    directory = HostedIdentityDirectory(sessions, LOCAL_CONFIG)
    for principal in ids.values():
        assert (await directory.resolve(principal))["principal_id"] == principal


@pytest.mark.asyncio
async def test_persistent_batch_resolution_uses_one_users_query(database):
    from sqlalchemy import event

    engine, _ = database  # the fixture already created ``users``
    people = {
        "active": {"is_active": True, "auth_source": "local"},
        "second": {"is_active": True, "auth_source": "local"},
        "inactive": {"is_active": False, "auth_source": "local"},
        "federated": {"is_active": True, "auth_source": "keycloak"},
    }
    ids = {name: str(uuid4()) for name in people}
    async with AsyncSession(engine) as session, session.begin():
        for name, values in people.items():
            session.add(
                UserDB(
                    id=ids[name],
                    username=name,
                    email=name + "@example.test",
                    name=name.title(),
                    user_type="regular",
                    date=datetime.now(),
                    update_date=datetime.now(),
                    **values,
                )
            )

    @asynccontextmanager
    async def sessions():
        async with AsyncSession(engine) as session:
            yield session

    statements = []

    def record(connection, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        directory = HostedIdentityDirectory(
            sessions,
            LOCAL_CONFIG,
        )
        found = await directory.resolve_many(list(ids.values()) + [str(uuid4())])
        # The directory is a profile lookup now: who may use an app (active, not deleted) is the access module's.
        assert set(found) == set(ids.values())
        assert len([statement for statement in statements if "FROM users" in statement]) == 1
        contacts = await directory.resolve_many([ids["active"]], contact=True)
        assert contacts[ids["active"]]["email"] == "active@example.test"
        executed = len(statements)
        assert (await directory.resolve(ids["second"]))["display_name"] == "Second"
        assert (await directory.resolve(ids["inactive"]))["principal_id"] == ids["inactive"]
        assert len(statements) == executed
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
