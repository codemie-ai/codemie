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


"""AC13 and AC18: administrator suspend and reinstate on the access model.

Runs the real service over the in-memory SQLite world of the access matrix (real ``users`` / ``user_projects`` and
hosted tables); only the management transaction's lock, idempotency and audit are stand-ins there. The lock order and
the operation digest are proven on the statement-recording session of ``test_transaction``.
"""
# ruff: noqa: F811  (imported fixtures are requested by name)

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.protocol import etag
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs as Job,
    HostedApplicationOperations as Operation,
    HostedApplicationVersions as Version,
    HostedApplicationStorageAttempts as Attempt,
    HostedApplicationUserState as State,
    HostedApplicationViewSessions as Credential,
    HostedApplications as App,
)
from codemie.rest_api.models.hosted_application_requests import VersionSelection, SecurityReason
from codemie.rest_api.models.permission import Permission
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.rest_api.routers.hosted_applications import app_security
from codemie.rest_api.security.hosted_applications import HostedActor
from codemie.service.hosted_applications.administration import HostedApplicationAdministration
from tests.codemie.service.hosted_applications.test_access_matrix import (  # noqa: F401  (fixtures)
    PERSONAS,
    FakeTx,
    actor,
    app_row,
    bare_copy,
    seed_app,
    utcnow,
    view_session,
    world,
)
from tests.codemie.service.hosted_applications.test_transaction import (  # noqa: F401  (fixtures)
    NOW,
    make_identity,
    make_user,
    repository,
    seed_app as seed_recorded_app,
    seed_state,
    session,
)


@pytest_asyncio.fixture
async def engine():
    """The matrix database plus the jobs table, which ``retire_version`` reads and writes."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    @sa.event.listens_for(engine.sync_engine, "connect")
    def _functions(dbapi_connection, _):
        dbapi_connection.create_function("char_length", 1, lambda value: None if value is None else len(value))
        dbapi_connection.create_function(
            "clock_timestamp", 0, lambda: utcnow().replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")
        )

    metadata = sa.MetaData()
    for table in (State, App, Version, Attempt, Credential, Operation, Job, Permission):
        bare_copy(table.__table__, metadata)
    async with engine.begin() as connection:
        for table in (UserDB.__table__, UserProject.__table__):
            await connection.run_sync(table.create)
        await connection.run_sync(metadata.create_all)
    yield engine
    await engine.dispose()


async def second_version(engine, app_id, author="creator"):
    version_id, attempt_id, now = uuid4(), uuid4(), utcnow()
    async with engine.begin() as connection:
        await connection.execute(
            Version.__table__.insert().values(
                id=version_id,
                app_id=app_id,
                sequence=2,
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
                quota_principal_id=PERSONAS[author][0],
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
    return version_id


async def add_job(engine, app_id, version_id, state):
    now = utcnow()
    values = {"lease_token": uuid4(), "lease_until": now + timedelta(minutes=5)} if state == "running" else {}
    async with engine.begin() as connection:
        await connection.execute(
            Job.__table__.insert().values(
                id=uuid4(),
                app_id=app_id,
                version_id=version_id,
                kind="validate",
                state=state,
                phase="scan",
                available_at=now,
                created_at=now,
                revision=1,
                safe_details={},
                **values,
            )
        )


async def suspended_app(world, *, author="creator", **flags):
    flags = {"shared": True, "is_global": True, **flags}
    app_id, version_id = await seed_app(world.engine, author, **flags)
    await HostedApplicationAdministration(world.catalog).security(
        await actor(world.engine, "platform_admin"), app_id, "suspend", "Phishing report", None, str(uuid4())
    )
    return app_id, version_id


def admin_service(world):
    return HostedApplicationAdministration(world.catalog)


async def reinstate(world, app_id, persona="maintainer", **options):
    current = etag("app", app_id, (await app_row(world.engine, app_id)).revision)
    return await admin_service(world).security(
        await actor(world.engine, persona), app_id, "reinstate", "Resolved", current, str(uuid4()), **options
    )


@pytest.mark.asyncio
async def test_reinstate_requires_the_revision_the_administrator_saw_and_suspend_does_not(world):
    app_id, _ = await suspended_app(world)
    with pytest.raises(HostedApplicationError) as error:
        await admin_service(world).security(
            await actor(world.engine, "maintainer"), app_id, "reinstate", "Resolved", None, str(uuid4())
        )
    assert (error.value.code, error.value.status) == ("PRECONDITION_REQUIRED", 428)
    assert (await app_row(world.engine, app_id)).security_state == "suspended"
    assert (await reinstate(world, app_id))["suspended"] is False


# --- who may act ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["suspend", "reinstate"])
@pytest.mark.parametrize("persona", ["creator", "project_admin", "member"])
async def test_a_user_who_is_not_a_platform_admin_is_refused(world, persona, action):
    app_id, _ = await seed_app(world.engine, "creator", shared=True, is_global=True)
    with pytest.raises(HostedApplicationError) as error:
        await admin_service(world).security(
            await actor(world.engine, persona), app_id, action, "Reason", None, str(uuid4())
        )
    assert (error.value.code, error.value.status) == ("ACCESS_DENIED", 403)
    assert (await app_row(world.engine, app_id)).security_state == "clear"


@pytest.mark.asyncio
async def test_the_route_leaves_the_administrator_check_to_the_service():
    """The request ``User``'s roles may come from the IdP; the service reads the ``users`` row instead."""
    called = []

    class Spy:
        async def security(self, *args, **kwargs):
            called.append(args)
            return {}

    await app_security(
        uuid4(),
        "suspend",
        SecurityReason(reason="Reason"),
        "identity",
        SimpleNamespace(administration=Spy()),
        None,
        "k" * 16,
    )
    assert called and called[0][0] == "identity"


@pytest.mark.asyncio
async def test_the_route_passes_the_chosen_version_and_the_delete_option_to_the_service():
    calls = []

    class Spy:
        async def security(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"ok": True}

    version_id = uuid4()
    body = SecurityReason(reason="Resolved", version_id=version_id, delete_previous=True)
    await app_security(
        uuid4(),
        "reinstate",
        body,
        "identity",
        SimpleNamespace(administration=Spy()),
        None,
        "k" * 16,
    )
    (_, kwargs) = calls[0]
    assert kwargs == {"version_id": version_id, "delete_previous": True}


@pytest.mark.asyncio
async def test_a_maintainer_may_suspend_and_every_reason_is_required(world):
    app_id, _ = await seed_app(world.engine, "creator", shared=True, is_global=True)
    maintainer = await actor(world.engine, "maintainer")
    for action, reason in (("delete", "Reason"), ("suspend", "   ")):
        with pytest.raises(HostedApplicationError) as error:
            await admin_service(world).security(maintainer, app_id, action, reason, None, str(uuid4()))
        assert error.value.status == 422
    suspended = await admin_service(world).security(maintainer, app_id, "suspend", "Phishing", None, str(uuid4()))
    assert suspended["suspended"] is True


@pytest.mark.asyncio
async def test_the_creator_may_not_reinstate_their_own_app_but_another_admin_may(world):
    app_id, _ = await suspended_app(world, author="platform_admin")
    with pytest.raises(HostedApplicationError) as error:
        await reinstate(world, app_id, persona="platform_admin")
    assert (error.value.code, error.value.status) == ("INDEPENDENT_REVIEW_REQUIRED", 403)
    assert (await app_row(world.engine, app_id)).security_state == "suspended"
    assert (await reinstate(world, app_id, persona="maintainer"))["suspended"] is False


# --- suspend -------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_sets_the_security_state_only(world):
    app_id, _ = await seed_app(world.engine, "creator", shared=True, is_global=True)
    before = await app_row(world.engine, app_id)
    await admin_service(world).security(
        await actor(world.engine, "platform_admin"), app_id, "suspend", "Phishing", None, str(uuid4())
    )
    after = await app_row(world.engine, app_id)
    assert after.security_state == "suspended"
    assert (after.shared, after.is_global, after.access_revision) == (True, True, before.access_revision)
    assert (after.active_version_id, after.selected_version_id) == (
        before.active_version_id,
        before.selected_version_id,
    )


@pytest.mark.asyncio
async def test_reinstate_restores_access_exactly_and_needs_a_suspension(world):
    app_id, version_id = await seed_app(world.engine, "creator", shared=True, is_global=False)
    with pytest.raises(HostedApplicationError) as error:
        await reinstate(world, app_id)
    assert (error.value.code, error.value.status) == ("NOT_SUSPENDED", 409)
    await admin_service(world).security(
        await actor(world.engine, "platform_admin"), app_id, "suspend", "Phishing", None, str(uuid4())
    )
    await reinstate(world, app_id)
    row = await app_row(world.engine, app_id)
    assert (row.security_state, row.shared, row.is_global, row.active_version_id) == (
        "clear",
        True,
        False,
        version_id,
    )


# --- reinstate onto a chosen version -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reinstate_onto_another_version_serves_nothing_from_the_old_one(world):
    app_id, old_id = await seed_app(world.engine, "creator", shared=True, is_global=True)
    old_cookie = await view_session(world, "member", app_id, old_id, "published_view")
    await admin_service(world).security(
        await actor(world.engine, "platform_admin"), app_id, "suspend", "Phishing", None, str(uuid4())
    )
    new_id = await second_version(world.engine, app_id)

    await reinstate(world, app_id, version_id=new_id)

    row = await app_row(world.engine, app_id)
    assert (row.active_version_id, row.selected_version_id, row.security_state) == (new_id, new_id, "clear")
    assert (row.shared, row.is_global) == (True, True)
    with pytest.raises(HostedApplicationError):
        await world.launch.authorize(old_cookie, old_id)
    fresh = await view_session(world, "member", app_id, new_id, "published_view")
    assert (await world.launch.authorize(fresh, new_id))["version"]["id"] == new_id


@pytest.mark.asyncio
async def test_reinstate_without_a_choice_keeps_the_current_active_version(world):
    app_id, old_id = await suspended_app(world)
    await reinstate(world, app_id)
    row = await app_row(world.engine, app_id)
    assert (row.active_version_id, row.selected_version_id) == (old_id, old_id)


@pytest.mark.asyncio
async def test_reinstate_is_refused_only_when_the_chosen_version_is_not_eligible(world):
    app_id, old_id = await suspended_app(world)
    with pytest.raises(HostedApplicationError) as error:
        await reinstate(world, app_id, version_id=uuid4())
    assert (error.value.code, error.value.status) == ("REVALIDATION_REQUIRED", 409)
    row = await app_row(world.engine, app_id)
    assert (row.security_state, row.active_version_id) == ("suspended", old_id)


@pytest.mark.asyncio
async def test_an_app_with_no_active_version_is_reinstated_with_none_unless_one_is_chosen(world):
    app_id, version_id = await seed_app(world.engine, "creator", shared=True, is_global=False, active=False)
    await admin_service(world).security(
        await actor(world.engine, "platform_admin"), app_id, "suspend", "Phishing", None, str(uuid4())
    )
    await reinstate(world, app_id)
    row = await app_row(world.engine, app_id)
    assert (row.security_state, row.active_version_id) == ("clear", None)

    await admin_service(world).security(
        await actor(world.engine, "platform_admin"), app_id, "suspend", "Again", None, str(uuid4())
    )
    await reinstate(world, app_id, version_id=version_id)
    assert (await app_row(world.engine, app_id)).active_version_id == version_id


# --- deleting the previous version ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reinstate_can_delete_the_previous_version_in_the_same_action(world):
    app_id, old_id = await suspended_app(world)
    new_id = await second_version(world.engine, app_id)

    await reinstate(world, app_id, version_id=new_id, delete_previous=True)

    async with world.engine.connect() as connection:
        old = (await connection.execute(select(Version.__table__).where(Version.__table__.c.id == old_id))).one()
        jobs = (
            await connection.execute(select(Job.__table__.c.kind).where(Job.__table__.c.version_id == old_id))
        ).all()
        audit = (
            await connection.execute(
                select(Operation.__table__).where(Operation.__table__.c.action == "version.retention")
            )
        ).one()
    assert (old.validation_state, old.security_state) == ("canceled", "blocked")
    assert [job.kind for job in jobs] == ["cleanup_upload"]
    # CR-012: the retirement is the administrator's action, as the reinstate itself is.
    assert (audit.actor_id, audit.actor_kind) == (PERSONAS["maintainer"][0], "administrator")
    assert (await app_row(world.engine, app_id)).active_version_id == new_id


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["none", "same"])
async def test_deleting_the_previous_version_without_choosing_another_is_refused(world, choice):
    app_id, old_id = await suspended_app(world)
    options = {"delete_previous": True, **({"version_id": old_id} if choice == "same" else {})}
    with pytest.raises(HostedApplicationError) as error:
        await reinstate(world, app_id, **options)
    assert error.value.status == 409
    assert (await app_row(world.engine, app_id)).security_state == "suspended"


@pytest.mark.asyncio
async def test_deleting_a_version_with_a_running_job_is_a_conflict_not_a_server_error(world):
    app_id, old_id = await suspended_app(world)
    new_id = await second_version(world.engine, app_id)
    await add_job(world.engine, app_id, old_id, "running")

    with pytest.raises(HostedApplicationError) as error:
        await reinstate(world, app_id, version_id=new_id, delete_previous=True)

    assert (error.value.code, error.value.status) == ("VERSION_IN_USE", 409)
    row = await app_row(world.engine, app_id)
    assert (row.security_state, row.active_version_id) == ("suspended", old_id)


@pytest.mark.asyncio
async def test_suspend_takes_no_version_options(world):
    app_id, _ = await seed_app(world.engine, "creator", shared=True, is_global=True)
    with pytest.raises(HostedApplicationError) as error:
        await admin_service(world).security(
            await actor(world.engine, "platform_admin"),
            app_id,
            "suspend",
            "Phishing",
            None,
            str(uuid4()),
            delete_previous=True,
        )
    assert error.value.status == 422


@pytest.mark.asyncio
async def test_the_audit_records_the_admin_and_the_version_made_active(world, monkeypatch):
    seen = []

    async def finish(self, operation, response, status=200, *, details=None):
        seen.append((operation.actor_kind, details))
        return response

    monkeypatch.setattr(FakeTx, "finish", finish)
    app_id, _ = await suspended_app(world)
    new_id = await second_version(world.engine, app_id)
    seen.clear()

    await reinstate(world, app_id, version_id=new_id)

    ((kind, details),) = seen
    assert kind == "administrator"
    assert details == {
        "reason": "Resolved",
        "admin_id": PERSONAS["maintainer"][0],
        "active_version_id": str(new_id),
        "deleted_version_id": None,
    }


@pytest.mark.asyncio
async def test_suspending_an_already_suspended_app_is_still_recorded_as_the_administrator(world, monkeypatch):
    """CR-012: the no-op second suspend is a moderation operation too (spec 5.6)."""
    seen = []

    async def finish(self, operation, response, status=200, *, details=None):
        seen.append(operation.actor_kind)
        return response

    app_id, _ = await suspended_app(world)
    monkeypatch.setattr(FakeTx, "finish", finish)
    await admin_service(world).security(
        await actor(world.engine, "platform_admin"), app_id, "suspend", "Again", None, str(uuid4())
    )
    assert seen == ["administrator"]


# --- idempotency and locking ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_same_key_with_a_different_version_is_an_idempotency_conflict(repository, session):
    app = seed_recorded_app(session)
    admin = make_user("u-admin", is_admin=True)
    session.admitted.add((app.id, admin.id))
    key = "k" * 20
    first, other = uuid4(), uuid4()
    digest = HostedApplicationAdministration.digest

    async with repository.transaction(admin, make_identity(admin.id), write=True) as tx:
        op, replay = await tx.operation("app.reinstate", app.id, digest("Resolved", "m", first, False), key)
        assert replay is None
        await tx.finish(op, {"id": str(app.id)})
    async with repository.transaction(admin, make_identity(admin.id), write=True) as tx:
        _, replay = await tx.operation("app.reinstate", app.id, digest("Resolved", "m", first, False), key)
        assert replay == {"id": str(app.id)}
    with pytest.raises(HostedApplicationError) as error:
        async with repository.transaction(admin, make_identity(admin.id), write=True) as tx:
            await tx.operation("app.reinstate", app.id, digest("Resolved", "m", other, False), key)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_the_operation_digest_is_the_reason_the_revision_the_version_and_the_delete_option():
    version_id = uuid4()
    assert HostedApplicationAdministration.digest("Why", "m", version_id, True) == {
        "reason": "Why",
        "if_match": "m",
        "version_id": str(version_id),
        "delete_previous": True,
    }
    assert HostedApplicationAdministration.digest("Why", None, None, False)["version_id"] is None


@pytest.mark.asyncio
async def test_an_activate_that_waited_on_a_suspend_is_refused_on_the_locked_row(repository, session):
    """Activate and suspend both lock the creator's quota row, then the app; the check runs after the locks."""
    from codemie.service.hosted_applications.catalog import HostedApplicationCatalog

    app = seed_recorded_app(session)
    seed_state(session, "u-creator")
    creator = make_user("u-creator")
    session.admitted.add((app.id, creator.id))
    revision = app.revision = 1
    # The suspend commits while the activate waits for the quota row.
    session.after_quota_lock = lambda: setattr(app, "security_state", "suspended")
    catalog = HostedApplicationCatalog(repository, directory=None)

    with pytest.raises(HostedApplicationError) as error:
        await catalog.activate_version(
            HostedActor(creator, make_identity(creator.id)),
            app.id,
            VersionSelection(version_id=uuid4()),
            etag("app", app.id, revision),
            "k" * 20,
        )

    assert (error.value.code, error.value.status) == ("APP_SUSPENDED", 409)
    assert session.locks[:2] == [("quota", "u-creator", "FOR UPDATE"), ("app", app.id, "FOR NO KEY UPDATE")]
    assert app.active_version_id is None


@pytest.mark.asyncio
async def test_suspend_locks_the_creators_quota_row_before_the_app(repository, session):
    app = seed_recorded_app(session)
    app.revision = 1
    seed_state(session, "u-creator")
    admin = make_user("u-admin", is_admin=True)
    session.admitted.add((app.id, admin.id))
    session.users[admin.id] = UserDB(id=admin.id, username=admin.id, email="a@example.com", is_admin=True)
    from codemie.service.hosted_applications.catalog import HostedApplicationCatalog

    async def project(*_):
        return {}

    catalog = HostedApplicationCatalog(repository, directory=None)
    catalog._project = project
    await HostedApplicationAdministration(catalog).security(
        HostedActor(admin, make_identity(admin.id)), app.id, "suspend", "Phishing", None, "k" * 20
    )
    assert app.security_state == "suspended"
    assert session.locks[:2] == [("quota", "u-creator", "FOR UPDATE"), ("app", app.id, "FOR NO KEY UPDATE")]


# --- one source for roles --------------------------------------------------------------------------------------


def request_user(monkeypatch, persona, *, env):
    """The request ``User`` as its validator builds it: ``ENV=local`` makes it an administrator whatever the row says."""
    from codemie.configs import config
    from codemie.core.hosted_applications.identity import VerifiedIdentityContext
    from codemie.rest_api.security.user import User

    monkeypatch.setattr(config, "ENV", env)
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)
    user_id, memberships, _ = PERSONAS[persona]
    user = User(id=user_id, username=user_id, project_names=list(memberships))
    now = utcnow()
    sign_in = VerifiedIdentityContext(
        user_id, "local_jwt", now - timedelta(seconds=10), now + timedelta(hours=1), b"a" * 32
    )
    return HostedActor(user, sign_in)


@pytest.mark.asyncio
async def test_a_request_admin_whose_users_row_is_a_member_gets_member_abilities_and_cannot_suspend(world, monkeypatch):
    app_id, _ = await seed_app(world.engine, "creator", shared=True, is_global=False)
    member = request_user(monkeypatch, "member", env="local")
    assert member.user.is_admin  # the validator's answer, which the access SQL does not share

    opened = await world.catalog.get_app(member, app_id)
    assert (opened["user_abilities"], opened["can_moderate"]) == (["read"], False)
    with pytest.raises(HostedApplicationError) as error:
        await admin_service(world).security(member, app_id, "suspend", "Phishing", None, str(uuid4()))
    assert (error.value.code, error.value.status) == ("ACCESS_DENIED", 403)
    assert (await app_row(world.engine, app_id)).security_state == "clear"


@pytest.mark.asyncio
async def test_an_admin_users_row_is_honoured_when_the_request_user_is_not_an_admin(world, monkeypatch):
    app_id, _ = await seed_app(world.engine, "creator", shared=False, is_global=False)
    admin = request_user(monkeypatch, "platform_admin", env="production")
    assert not admin.user.is_admin

    opened = await world.catalog.get_app(admin, app_id)
    assert (opened["user_abilities"], opened["can_moderate"]) == (["read", "write", "delete"], True)
    suspended = await admin_service(world).security(admin, app_id, "suspend", "Phishing", None, str(uuid4()))
    assert suspended["suspended"] is True
