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

"""Management transaction keyed by the platform actor.

The ``manageable`` SQL condition itself is proven against seeded tables in ``core/hosted_applications/test_access.py``;
here a statement-recording session stands in for PostgreSQL, so the tests can see which rows each statement
locks and in what order. ``admitted`` plays the role of the ``manageable`` condition's answer.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.dml import Insert, Update

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.identity import VerifiedIdentityContext
from codemie.core.models import CreatedByUser
from codemie.repository.hosted_applications.repository import HostedApplicationRepository
from codemie.rest_api.models.hosted_application import (
    HostedApplicationOperations,
    HostedApplicationUserState,
    HostedApplicationViewSessions,
    HostedApplications,
)
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.rest_api.security.hosted_applications import hosted_actor, verified_context
from codemie.rest_api.security.user import User

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
QUOTA = 2 * 1024**3
SECRET = b"s" * 48


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        assert self.value is not None
        return self.value

    def scalar_one_or_none(self):
        return self.value

    scalar = scalar_one_or_none

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self.value))


class RecordingSession:
    """Answers the transaction's statements from in-memory rows and records every row lock taken."""

    def __init__(self):
        self.states: dict[str, HostedApplicationUserState] = {}
        self.apps: dict[UUID, HostedApplications] = {}
        self.admitted: set[tuple[UUID, str]] = set()
        self.revoked_binding = False
        self.locks: list[tuple[str, object, str]] = []
        self.operations: list[HostedApplicationOperations] = []
        self.hidden_operation = None  # committed by a concurrent request, invisible to the first lookup
        self.after_quota_lock = None
        self.added = []
        self.users: dict[str, UserDB] = {}  # the ``users`` rows ``platform_user`` reads
        self.memberships: dict[str, list[UserProject]] = {}

    @asynccontextmanager
    async def begin(self):
        yield

    @asynccontextmanager
    async def begin_nested(self):
        yield

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        pending = [row for row in self.added if isinstance(row, HostedApplicationOperations)]
        self.added = [row for row in self.added if not isinstance(row, HostedApplicationOperations)]
        for row in pending:
            if self.hidden_operation is not None and row.key_digest == self.hidden_operation.key_digest:
                self.operations.append(self.hidden_operation)
                self.hidden_operation = None
                raise IntegrityError(
                    "INSERT",
                    {},
                    Exception('duplicate key value violates unique constraint "hosted_application_operation_key"'),
                )
            self.operations.append(row)

    async def execute(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        sql, params = str(compiled), compiled.params
        if isinstance(statement, Insert):
            principal = params["principal_id"]
            self.states.setdefault(
                principal,
                HostedApplicationUserState(
                    principal_id=principal,
                    byte_limit=params["byte_limit"],
                    stored_bytes=0,
                    reserved_bytes=0,
                    revision=1,
                    updated_at=NOW,
                ),
            )
            return Result(None)
        if isinstance(statement, Update):
            return Result(None)
        entity = statement.column_descriptions[0].get("entity")
        lock = "FOR NO KEY UPDATE" if "FOR NO KEY UPDATE" in sql else "FOR UPDATE" if "FOR UPDATE" in sql else None
        if entity is None:
            return Result(NOW)
        if entity is HostedApplicationUserState:
            (principal,) = [value for value in params.values() if isinstance(value, str)]
            if lock:
                self.locks.append(("quota", principal, lock))
                if self.after_quota_lock:
                    self.after_quota_lock()
            return Result(self.states.get(principal))
        if entity is UserDB:
            (user_id,) = [value for value in params.values() if isinstance(value, str)]
            return Result(self.users.get(user_id))
        if entity is UserProject:
            (user_id,) = [value for value in params.values() if isinstance(value, str)]
            return Result(self.memberships.get(user_id, []))
        if entity is HostedApplicationViewSessions:
            return Result(uuid4() if self.revoked_binding else None)
        if entity is HostedApplications:
            (app_id,) = [value for value in params.values() if isinstance(value, UUID)]
            if lock:
                self.locks.append(("app", app_id, lock))
            app = self.apps.get(app_id)
            # A select without the ``manageable`` condition (no ``manager_id`` bind) is admitted on the row alone.
            if app is None or ("manager_id" in params and (app_id, params["manager_id"]) not in self.admitted):
                return Result(None)
            return Result(app)
        if entity is HostedApplicationOperations:
            key = params.get("key_digest_1")
            match = [row for row in self.operations if row.key_digest == key and row.actor_id == params["actor_id_1"]]
            return Result(match[0] if match else None)
        raise AssertionError(f"unexpected statement: {sql}")


def make_user(user_id, **changes):
    user = User(id=user_id, username=user_id, **changes)
    user.is_admin = changes.get("is_admin", False)
    return user


def make_identity(principal, *, issued_at=None):
    issued_at = issued_at or NOW - timedelta(minutes=5)
    return VerifiedIdentityContext(principal, "local_jwt", issued_at, NOW + timedelta(hours=1), b"a" * 32)


@pytest.fixture
def session():
    return RecordingSession()


@pytest.fixture
def repository(session):
    @asynccontextmanager
    async def sessions():
        yield session

    return HostedApplicationRepository(sessions, secret=SECRET, quota_bytes=QUOTA)


def seed_app(session, creator="u-creator"):
    app = HostedApplications(
        id=uuid4(),
        project="proj-a",
        created_by=CreatedByUser(id=creator, username=creator, name=creator),
        lifecycle_state="active",
        security_state="clear",
    )
    session.apps[app.id] = app
    return app


def seed_state(session, principal, **changes):
    state = HostedApplicationUserState(
        principal_id=principal,
        byte_limit=QUOTA,
        stored_bytes=0,
        reserved_bytes=0,
        revision=1,
        updated_at=NOW,
        **changes,
    )
    session.states[principal] = state
    return state


@pytest.mark.asyncio
async def test_write_after_platform_logout_with_earlier_sign_in_is_refused(repository, session):
    seed_state(session, "u-alice", auth_revoked_before=NOW - timedelta(minutes=1))
    user = make_user("u-alice")
    identity = make_identity("u-alice", issued_at=NOW - timedelta(minutes=10))

    with pytest.raises(HostedApplicationError) as error:
        async with repository.transaction(user, identity, write=True):
            pass

    assert error.value.status == 401
    assert session.locks == []


@pytest.mark.asyncio
async def test_write_with_revoked_binding_is_refused(repository, session):
    session.revoked_binding = True

    with pytest.raises(HostedApplicationError) as error:
        async with repository.transaction(make_user("u-alice"), make_identity("u-alice"), write=True):
            pass

    assert error.value.status == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded", [None, "other-principal"])
async def test_write_without_recorded_identity_is_refused(repository, recorded):
    identity = make_identity(recorded) if recorded else None

    with pytest.raises(HostedApplicationError) as error:
        async with repository.transaction(make_user("u-alice"), identity, write=True):
            pass

    assert (error.value.code, error.value.status) == ("AUTHENTICATION_REQUIRED", 401)


@pytest.mark.asyncio
async def test_read_without_recorded_identity_is_admitted(repository):
    async with repository.transaction(make_user("u-alice"), None) as tx:
        assert tx.actor_id == "u-alice"


@pytest.mark.asyncio
async def test_transaction_locks_no_quota_row_by_itself(repository, session):
    async with repository.transaction(make_user("u-alice"), make_identity("u-alice"), write=True) as tx:
        assert tx.quota is None

    assert session.locks == []


@pytest.mark.asyncio
async def test_lock_quota_inserts_locks_and_syncs_the_row(repository, session):
    seed_state(session, "u-bob")
    session.states["u-bob"].byte_limit = 1
    async with repository.transaction(make_user("u-alice"), make_identity("u-alice"), write=True) as tx:
        created = await tx.lock_quota("u-carol")
        synced = await tx.lock_quota("u-bob")

    assert created.principal_id == "u-carol" and created.byte_limit == QUOTA
    assert synced.byte_limit == QUOTA and synced.revision == 2
    assert session.locks == [("quota", "u-carol", "FOR UPDATE"), ("quota", "u-bob", "FOR UPDATE")]


@pytest.mark.asyncio
async def test_read_quota_creates_a_missing_row_without_a_lock(repository, session):
    async with repository.transaction(make_user("u-alice"), None) as tx:
        state = await tx.read_quota("u-carol")

    assert (state.principal_id, state.byte_limit, state.stored_bytes, state.reserved_bytes) == ("u-carol", QUOTA, 0, 0)
    assert session.locks == [] and tx.quota is None


@pytest.mark.asyncio
async def test_read_quota_brings_a_stale_limit_to_the_configured_quota(repository, session, monkeypatch):
    seed_state(session, "u-bob")
    session.states["u-bob"].byte_limit, session.states["u-bob"].stored_bytes = 1, 7
    synced = []

    async def sync(db_session, state, now):
        # The guarded UPDATE of an unlocked row; the recording session cannot run it, so apply it here.
        synced.append(state.principal_id)
        state.byte_limit, state.revision = QUOTA, state.revision + 1
        return state

    monkeypatch.setattr(repository, "_sync_unlocked_quota", sync)
    async with repository.transaction(make_user("u-alice"), None) as tx:
        state = await tx.read_quota("u-bob")
        again = await tx.read_quota("u-bob")

    assert (state.byte_limit, state.stored_bytes, state.revision) == (QUOTA, 7, 2)
    assert again is state and synced == ["u-bob"], "An up-to-date row is not written again"
    assert session.locks == []


@pytest.mark.asyncio
async def test_project_admin_is_admitted_and_charged_to_the_creator(repository, session):
    app = seed_app(session, creator="u-creator")
    admin = make_user("u-padmin", project_names=["proj-a"], admin_project_names=["proj-a"])
    session.admitted.add((app.id, admin.id))

    async with repository.transaction(admin, make_identity(admin.id), write=True) as tx:
        managed = await tx.managed_app(app.id, admin, lock=True)

    assert managed is app
    assert tx.quota.principal_id == "u-creator"
    # Lock order: the creator's quota row first, then the app; the caller's own row is never locked.
    assert session.locks == [("quota", "u-creator", "FOR UPDATE"), ("app", app.id, "FOR NO KEY UPDATE")]
    assert "u-padmin" not in session.states


@pytest.mark.asyncio
async def test_managed_app_unlocked_takes_no_lock(repository, session):
    app = seed_app(session)
    user = make_user("u-creator")
    session.admitted.add((app.id, user.id))

    async with repository.transaction(user, None) as tx:
        assert await tx.managed_app(app.id, user) is app

    assert session.locks == []


@pytest.mark.asyncio
async def test_managed_app_refuses_a_non_manager_with_404(repository, session):
    app = seed_app(session)
    outsider = make_user("u-outsider")

    with pytest.raises(HostedApplicationError) as error:
        async with repository.transaction(outsider, make_identity(outsider.id), write=True) as tx:
            await tx.managed_app(app.id, outsider, lock=True)

    assert error.value.status == 404
    assert session.locks == []


@pytest.mark.asyncio
async def test_managed_app_rechecks_access_after_locking(repository, session):
    app = seed_app(session)
    admin = make_user("u-padmin")
    session.admitted.add((app.id, admin.id))
    # The admin loses project-admin rights between the unlocked read and the app lock.
    session.after_quota_lock = lambda: session.admitted.discard((app.id, admin.id))

    with pytest.raises(HostedApplicationError) as error:
        async with repository.transaction(admin, make_identity(admin.id), write=True) as tx:
            await tx.managed_app(app.id, admin, lock=True)

    assert error.value.status == 404


@pytest.mark.asyncio
async def test_lock_app_locks_only_the_app_row_and_returns_it(repository, session):
    app = seed_app(session)

    async with repository.transaction(make_user("u-alice"), make_identity("u-alice"), write=True) as tx:
        locked = await tx.lock_app(select(HostedApplications).where(HostedApplications.id == app.id))

    assert locked is app
    assert session.locks == [("app", app.id, "FOR NO KEY UPDATE")]
    assert tx.quota is None


@pytest.mark.asyncio
async def test_lock_app_returns_none_when_the_locked_select_is_empty(repository, session):
    missing = uuid4()

    async with repository.transaction(make_user("u-alice"), make_identity("u-alice"), write=True) as tx:
        locked = await tx.lock_app(select(HostedApplications).where(HostedApplications.id == missing))

    assert locked is None
    assert session.locks == [("app", missing, "FOR NO KEY UPDATE")]


@pytest.mark.asyncio
async def test_operation_records_the_platform_actor(repository, session):
    app = seed_app(session)
    admin = make_user("u-padmin")
    async with repository.transaction(admin, make_identity(admin.id), write=True) as tx:
        operation, replay = await tx.operation("app.update", app.id, {"a": 1}, "k" * 20, app_id=app.id)

    assert replay is None and operation.actor_id == "u-padmin"


@pytest.mark.asyncio
async def test_unique_violation_on_the_operation_key_returns_the_stored_replay(repository, session):
    app = seed_app(session)
    user = make_user("u-alice")
    async with repository.transaction(user, make_identity(user.id), write=True) as tx:
        first, _ = await tx.operation("app.update", app.id, {"a": 1}, "k" * 20, app_id=app.id)
        await tx.finish(first, {"done": True})
    session.operations.clear()
    session.hidden_operation = first

    async with repository.transaction(user, make_identity(user.id), write=True) as tx:
        operation, replay = await tx.operation("app.update", app.id, {"a": 1}, "k" * 20, app_id=app.id)

    assert operation is first and replay == {"done": True}


def request_with(identity, **headers):
    return SimpleNamespace(state=SimpleNamespace(hosted_identity=identity), headers=headers)


@pytest.mark.asyncio
async def test_hosted_actor_returns_the_user_and_recorded_identity():
    user = make_user("u-alice")
    identity = make_identity("u-alice")

    actor = await hosted_actor(request_with(identity), user)

    assert (actor.user, actor.identity) == (user, identity)
    assert actor.principal_id == "u-alice"


@pytest.mark.asyncio
async def test_hosted_actor_without_recorded_identity_has_none():
    actor = await hosted_actor(request_with(None), make_user("u-alice"))

    assert actor.identity is None


@pytest.mark.asyncio
async def test_hosted_actor_refuses_an_external_user():
    with pytest.raises(HostedApplicationError) as error:
        await hosted_actor(request_with(make_identity("u-ext")), make_user("u-ext", user_type="external"))

    assert error.value.status == 403


def test_service_account_passes_verified_context():
    identity = make_identity("u-svc")

    assert verified_context(request_with(identity), make_user("u-svc", user_type="service_account")) is identity


def test_external_user_fails_verified_context():
    identity = make_identity("u-ext")

    assert verified_context(request_with(identity), make_user("u-ext", user_type="external")) is None


@pytest.mark.asyncio
async def test_set_lock_timeout_is_a_transaction_local_setting():
    from codemie.repository.hosted_applications.repository import HostedTransaction

    executed = []

    class Session:
        async def execute(self, statement):
            executed.append(str(statement))

    repository = type("Repository", (), {"secret": b"s"})()
    tx = HostedTransaction(Session(), "u-1", None, None, repository)
    await tx.set_lock_timeout(2000)
    assert executed == ["SET LOCAL lock_timeout = '2000ms'"]


@pytest.mark.asyncio
async def test_a_transaction_lock_timeout_is_its_first_statement(repository, session):
    """CR-014: spec 6.3 step 1, before the clock read and the sign-in checks of the transaction itself."""
    executed = []
    answer = session.execute

    async def execute(statement):
        executed.append(str(statement))
        if "lock_timeout" in str(statement):
            return Result(None)
        return await answer(statement)

    session.execute = execute
    async with repository.transaction(make_user("u-alice"), make_identity("u-alice"), write=True, lock_timeout_ms=400):
        pass
    assert executed[0] == "SET LOCAL lock_timeout = '400ms'"
    assert len(executed) > 1 and not any("lock_timeout" in statement for statement in executed[1:])


@pytest.mark.asyncio
async def test_a_transaction_without_a_lock_timeout_sets_none(repository, session):
    executed = []
    answer = session.execute

    async def execute(statement):
        executed.append(str(statement))
        return await answer(statement)

    session.execute = execute
    async with repository.transaction(make_user("u-alice"), make_identity("u-alice"), write=True):
        pass
    assert not any("lock_timeout" in statement for statement in executed)


def test_is_lock_timeout_reads_the_sqlstate_of_the_driver_error():
    from sqlalchemy.exc import DBAPIError

    from codemie.repository.hosted_applications.repository import is_lock_timeout

    class Driver(Exception):
        def __init__(self, sqlstate):
            self.sqlstate = sqlstate

    assert is_lock_timeout(DBAPIError("x", {}, Driver("55P03")))
    assert not is_lock_timeout(DBAPIError("x", {}, Driver("40001")))
    assert not is_lock_timeout(ValueError("x"))
