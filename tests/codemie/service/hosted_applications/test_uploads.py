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
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.protocol import etag
from codemie.repository.hosted_applications.contract import WriterLease, build_writer_lease
from codemie.repository.hosted_applications.filesystem import HostedFileStorage, _prefix
from codemie.repository.hosted_applications.repository import HostedApplicationRepository
from codemie.rest_api.models.user_management import UserDB, UserProject
from codemie.rest_api.security.hosted_applications import HostedActor
from codemie.service.hosted_applications.catalog import HostedApplicationCatalog
from codemie.rest_api.models.hosted_application import (
    HostedApplicationVersions,
    HostedApplicationStorageAttempts,
    HostedApplicationUserState,
)
from codemie.rest_api.models.hosted_application_requests import AppCreate, VersionCreate, UploadComplete, UploadReset
from codemie.service.hosted_applications.sweep import ATTEMPT_PREFIX
from codemie.service.hosted_applications.uploads import attempt_prefix
from tests.codemie.service.hosted_applications.storage_fakes import CROSS_ADAPTERS
from tests.codemie.service.hosted_applications.test_transaction import (
    NOW,
    QUOTA,
    SECRET,
    RecordingSession,
    Result,
    make_identity,
    make_user,
    seed_app,
    seed_state,
)


@pytest.fixture
def upload_service(catalog, tmp_path):
    service, repo, directory = catalog
    service.storage = HostedFileStorage(tmp_path / "private-storage")
    return service, repo, directory


async def staged(service, context, *, size=20):
    app = await service.create_app(
        context, AppCreate(name="Visual", description="Sample", project="demo"), str(uuid4())
    )
    version = await service.begin_version(
        context,
        app["id"],
        VersionCreate(filename="visual.html", artifact_kind="html", byte_size=size),
        app["etag"],
        str(uuid4()),
    )
    return app, version


async def data(value):
    yield value


def test_attempt_prefix_is_keyed_by_the_app_id_and_the_sweep_recognizes_it():
    app_id = uuid4()
    prefix = attempt_prefix(app_id, uuid4(), uuid4())
    assert prefix.split("/")[0] == f"a-{app_id.hex}" and prefix.split("/")[1] == app_id.hex
    assert len(prefix) <= 240
    _prefix(prefix)
    assert ATTEMPT_PREFIX.fullmatch(prefix)


@pytest.mark.asyncio
async def test_upload_attempts_are_stored_under_the_app_id(upload_service, identity):
    service, repo, _ = upload_service
    context = identity(principal="f:1a2b:john.doe@example.com")
    async with repo.sessions() as session, session.begin():
        # The access rules read ``users`` and ``user_projects``; this federated principal is not a fixture one.
        await session.execute(
            UserDB.__table__.insert().values(
                id=context.principal_id,
                username="john.doe",
                email="john.doe@example.com",
                user_type="regular",
                is_active=True,
                is_admin=False,
                is_maintainer=False,
                is_auditor=False,
                auth_source="keycloak",
                email_verified=False,
            )
        )
        await session.execute(
            UserProject.__table__.insert().values(
                id=uuid4().hex,
                user_id=context.principal_id,
                project_name="demo",
                is_project_admin=False,
                is_default=False,
            )
        )
    original = b"<html>Example</html>"
    app, version = await staged(service, context, size=len(original))
    await service.transfer(
        context, app["id"], version["version_id"], version["generation"], version["upload_etag"], data(original)
    )
    async with repo.sessions() as session:
        attempt = (await session.execute(select(HostedApplicationStorageAttempts))).scalars().one()
    assert attempt.prefix == attempt_prefix(attempt.app_id, attempt.version_id, attempt.id)
    assert attempt.prefix.startswith(f"a-{attempt.app_id.hex}/")
    assert (service.storage.base / attempt.prefix / "original").read_bytes() == original


@pytest.mark.asyncio
async def test_upload_receipt_and_complete_are_durable_and_do_not_publish(upload_service, identity):
    service, repo, _ = upload_service
    context = identity()
    original = b"<html>Example</html>"
    app, version = await staged(service, context, size=len(original))
    receipt = await service.transfer(
        context, app["id"], version["version_id"], version["generation"], version["upload_etag"], data(original)
    )
    assert receipt["received_bytes"] == len(original)
    stored = await service.get_version(context, app["id"], version["version_id"])
    assert stored["upload"]["sha256"] == receipt["sha256"]
    assert stored["upload"]["state"] == "stored"
    key = str(uuid4())
    request = UploadComplete(upload_id=version["upload_id"], generation=version["generation"], sha256=receipt["sha256"])
    job = await service.complete_version(
        context, app["id"], version["version_id"], request, receipt["version_etag"], key
    )
    assert job["state"] == "queued"
    assert (
        await service.complete_version(context, app["id"], version["version_id"], request, receipt["version_etag"], key)
        == job
    )
    current = await service.get_app(context, app["id"])
    assert (current["shared"], current["is_global"], current["has_active_version"]) == (False, False, False)
    async with repo.sessions() as session:
        state = await session.get(HostedApplicationUserState, "owner")
        attempts = (await session.execute(select(HostedApplicationStorageAttempts))).scalars().all()
        assert state.stored_bytes == len(original)
        assert state.reserved_bytes == sum(attempt.held_bytes for attempt in attempts)


@pytest.mark.asyncio
async def test_wrong_size_is_uncertain_charged_and_requires_explicit_reset(upload_service, identity):
    service, repo, _ = upload_service
    context = identity()
    app, version = await staged(service, context)
    with pytest.raises(HostedApplicationError):
        await service.transfer(context, app["id"], version["version_id"], 1, version["upload_etag"], data(b"short"))
    current = await service.get_version(context, app["id"], version["version_id"])
    assert current["upload"]["state"] == "receiving"
    reset = await service.reset_upload(
        context, app["id"], version["version_id"], UploadReset(generation=1), current["upload"]["etag"], str(uuid4())
    )
    assert reset["generation"] == 2
    assert reset["upload_id"] == version["upload_id"]
    assert reset["expires_at"] == version["expires_at"]
    async with repo.sessions() as session:
        attempts = (await session.execute(select(HostedApplicationStorageAttempts))).scalars().all()
        assert len(attempts) == 2 and attempts[0].prefix != attempts[1].prefix
        assert any(attempt.state == "delete_requested" and attempt.held_bytes > 0 for attempt in attempts)
    with pytest.raises(HostedApplicationError) as error:
        await service.transfer(context, app["id"], version["version_id"], 1, version["upload_etag"], data(b"x" * 20))
    assert error.value.code == "UPLOAD_GENERATION_CONFLICT"


@pytest.mark.asyncio
async def test_quota_is_reserved_before_any_storage_write(upload_service, identity):
    service, repo, _ = upload_service
    context = identity()
    app = await service.create_app(
        context, AppCreate(name="Visual", description="Sample", project="demo"), str(uuid4())
    )
    repo.quota_bytes = 1
    with pytest.raises(HostedApplicationError) as error:
        await service.begin_version(
            context,
            app["id"],
            VersionCreate(filename="x.html", artifact_kind="html", byte_size=1),
            app["etag"],
            str(uuid4()),
        )
    assert error.value.code == "QUOTA_EXCEEDED"
    assert list(service.storage.base.rglob("original")) == []


@pytest.mark.asyncio
async def test_stored_upload_cannot_reset_or_overwrite(upload_service, identity):
    service, _, _ = upload_service
    context = identity()
    original = b"<html>Example</html>"
    app, version = await staged(service, context, size=len(original))
    receipt = await service.transfer(
        context, app["id"], version["version_id"], 1, version["upload_etag"], data(original)
    )
    with pytest.raises(HostedApplicationError):
        await service.reset_upload(
            context, app["id"], version["version_id"], UploadReset(generation=1), receipt["upload_etag"], str(uuid4())
        )
    with pytest.raises(HostedApplicationError) as error:
        await service.transfer(context, app["id"], version["version_id"], 1, receipt["upload_etag"], data(original))
    assert error.value.code == "UPLOAD_IMMUTABLE"


@pytest.mark.asyncio
async def test_expired_sessions_are_terminal(upload_service, identity):
    service, repo, _ = upload_service
    context = identity()
    app, version = await staged(service, context)
    async with repo.sessions() as session, session.begin():
        record = await session.get(HostedApplicationVersions, version["version_id"])
        record.upload_expires_at -= timedelta(days=2)
    with pytest.raises(HostedApplicationError) as error:
        await service.reset_upload(
            context, app["id"], version["version_id"], UploadReset(generation=1), version["upload_etag"], str(uuid4())
        )
    assert error.value.code == "UPLOAD_EXPIRED"


@pytest.mark.asyncio
async def test_foreign_version_returns_same_not_found(upload_service, identity):
    service, _, _ = upload_service
    app, version = await staged(service, identity())
    for actor in (identity("viewer"),):
        with pytest.raises(HostedApplicationError) as error:
            await service.get_version(actor, app["id"], version["version_id"])
        assert error.value.status == 404


@pytest.mark.asyncio
async def test_configured_quota_applies_to_existing_users_on_next_write(upload_service, identity):
    service, repo, _ = upload_service
    context = identity()
    await service.create_app(context, AppCreate(name="Visual", description="Sample", project="demo"), str(uuid4()))
    repo.quota_bytes = 3 * 1024**3
    await service.create_app(context, AppCreate(name="Second", description="Sample", project="demo"), str(uuid4()))
    async with repo.sessions() as session:
        state = await session.get(HostedApplicationUserState, "owner")
        assert state.byte_limit == 3 * 1024**3
    repo.quota_bytes = 1024**2
    async with repo.transaction(*context) as tx:
        assert (await tx.read_quota(context.principal_id)).byte_limit == 1024**2, "Reads report the configured quota"


@pytest.mark.asyncio
async def test_upload_lease_uses_the_total_upload_budget(upload_service, identity):
    from codemie.service.hosted_applications import receiving

    service, repo, _ = upload_service
    context = identity()
    app, version = await staged(service, context)
    claim = await service._claim_receive(context, app["id"], version["version_id"], 1, version["upload_etag"])
    async with repo.sessions() as session:
        attempt = await session.get(HostedApplicationStorageAttempts, claim["id"])
        window = attempt.write_deadline - attempt.updated_at
    assert receiving.UPLOAD_TIMEOUT_SECONDS == 900
    assert window == timedelta(seconds=900), "A 100 MiB upload on a slow link needs the whole budget"


@pytest.mark.asyncio
async def test_stalled_upload_stream_fails_with_a_clear_timeout(upload_service, identity, monkeypatch):
    import asyncio

    from codemie.service.hosted_applications import receiving

    service, repo, _ = upload_service
    context = identity()
    app, version = await staged(service, context)
    monkeypatch.setattr(receiving, "UPLOAD_IDLE_SECONDS", 0.05)

    async def stalled():
        yield b"<html>"
        await asyncio.sleep(1)
        yield b"x" * 14

    with pytest.raises(HostedApplicationError) as error:
        await service.transfer(context, app["id"], version["version_id"], 1, version["upload_etag"], stalled())
    assert error.value.code == "UPLOAD_TIMEOUT" and error.value.status == 408
    current = await service.get_version(context, app["id"], version["version_id"])
    assert current["upload"]["can_reset"]


class RecordingStorage(HostedFileStorage):
    """Real filesystem adapter that records the lease handed to the writer and every renewal."""

    def __init__(self, base):
        super().__init__(base)
        self.opened_lease = None
        self.renewed = []

    @asynccontextmanager
    async def writer(self, prefix, *, byte_limit, object_limit, lease=None):
        self.opened_lease = lease
        async with super().writer(prefix, byte_limit=byte_limit, object_limit=object_limit, lease=lease) as writer:
            real = writer.renew

            def renew(value):
                self.renewed.append(value)
                real(value)

            writer.renew = renew
            yield writer


def test_writer_lease_ends_at_the_earlier_of_lease_and_write_deadline():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    near, far = now + timedelta(seconds=30), now + timedelta(seconds=900)
    assert build_writer_lease(near, far, now, 7.5) == WriterLease(until=near, database_now=now, received_at=7.5)
    assert build_writer_lease(far, near, now, 8.5).until == near


@pytest.mark.asyncio
async def test_upload_writer_gets_claim_lease_and_renewals(upload_service, identity, tmp_path, monkeypatch):
    from codemie.service.hosted_applications import receiving

    service, repo, _ = upload_service
    service.storage = storage = RecordingStorage(tmp_path / "recording-storage")
    ticks = iter(range(1000, 2000))
    issued = []

    def monotonic():
        issued.append(float(next(ticks)))
        return issued[-1]

    service.monotonic = monotonic
    monkeypatch.setattr(receiving, "HEARTBEAT_SECONDS", 0.01)
    claims, beats, claim_rows, beat_rows = [], [], [], []
    claim_receive, heartbeat = service._claim_receive, service._receive_heartbeat

    async def attempt_lease_end(attempt_id):
        # The lease the writer may act on ends at the earlier of the attempt's lease and its write deadline.
        async with repo.sessions() as session:
            attempt = await session.get(HostedApplicationStorageAttempts, attempt_id)
            return min(attempt.lease_until, attempt.write_deadline)

    async def recording_claim(*args):
        claims.append(await claim_receive(*args))
        claim_rows.append(await attempt_lease_end(claims[-1]["id"]))
        return claims[-1]

    async def recording_heartbeat(*args):
        beats.append(await heartbeat(*args))
        beat_rows.append(await attempt_lease_end(args[3]["id"]))
        return beats[-1]

    service._claim_receive, service._receive_heartbeat = recording_claim, recording_heartbeat
    original = b"<html>Example</html>"
    context = identity()
    app, version = await staged(service, context, size=len(original))

    async def slow():
        await asyncio.sleep(0.15)
        yield original

    await service.transfer(context, app["id"], version["version_id"], 1, version["upload_etag"], slow())
    assert storage.opened_lease == claims[0]["lease"]
    assert claims[0]["lease"].received_at in issued
    assert len(beats) >= 3, "the post-lock check and at least two loop heartbeats"
    assert storage.renewed == beats
    assert all(lease.received_at in issued for lease in beats)
    assert [lease.received_at for lease in beats] == sorted(lease.received_at for lease in beats)
    # The writer signs from these values: each must be the row's lease end, never a longer window.
    assert claims[0]["lease"].until == claim_rows[0]
    assert [lease.until for lease in beats] == beat_rows
    assert all(lease.until - lease.database_now <= timedelta(seconds=30) for lease in [claims[0]["lease"], *beats])


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", CROSS_ADAPTERS)
async def test_declared_checksum_mismatch_is_refused_and_stores_nothing(upload_service, identity, tmp_path, adapter):
    service, repo, _ = upload_service
    service.storage = storage = adapter(tmp_path / "checksum-storage")
    context = identity()
    declared, received = b"<html>expected</html>", b"<html>other!!!</html>"
    assert len(declared) == len(received)
    app = await service.create_app(
        context, AppCreate(name="Visual", description="Sample", project="demo"), str(uuid4())
    )
    version = await service.begin_version(
        context,
        app["id"],
        VersionCreate(
            filename="visual.html",
            artifact_kind="html",
            byte_size=len(declared),
            sha256=hashlib.sha256(declared).hexdigest(),
        ),
        app["etag"],
        str(uuid4()),
    )
    with pytest.raises(HostedApplicationError) as error:
        await service.transfer(
            context, app["id"], version["version_id"], version["generation"], version["upload_etag"], data(received)
        )
    assert error.value.code == "UPLOAD_CHECKSUM_MISMATCH"
    current = await service.get_version(context, app["id"], version["version_id"])
    assert current["upload"]["state"] != "stored"
    async with repo.sessions() as session:
        attempt = (await session.execute(select(HostedApplicationStorageAttempts))).scalars().one()
    assert attempt.state != "stored" and attempt.receipt_digest is None
    with pytest.raises(HostedApplicationError):
        await storage.read_small(attempt.prefix + "/original", maximum=1024)


# Statement-level tests: a recording session stands in for PostgreSQL, so they run without a database. The
# locking behaviour against a real PostgreSQL is covered by the database-backed tests above.


class UploadSession(RecordingSession):
    """The management transaction's session, which also answers the count and max lookups of begin_version."""

    async def execute(self, statement):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        if "count(" in sql or "max(" in sql:
            return Result(0)
        return await super().execute(statement)


class FakeDirectory:
    async def require(self, actor):
        return {"principal_id": actor.user.id}


def upload_catalog(session):
    @asynccontextmanager
    async def sessions():
        yield session

    repository = HostedApplicationRepository(sessions, secret=SECRET, quota_bytes=QUOTA)
    service = HostedApplicationCatalog(repository, FakeDirectory())
    service.storage = SimpleNamespace(provider="fs", bind_namespace=lambda name: name)
    return service


def hosted_actor_of(user):
    return HostedActor(user, make_identity(user.id))


async def start_upload(service, session, app, user, size=20):
    session.admitted.add((app.id, user.id))
    app.revision, app.selected_version_id = 1, None
    request = VersionCreate(filename="visual.html", artifact_kind="html", byte_size=size)
    return await service.begin_version(
        hosted_actor_of(user), app.id, request, etag("app", app.id, app.revision), str(uuid4()) + "-key-0000"
    )


def attempts_added(session):
    return [row for row in session.added if isinstance(row, HostedApplicationStorageAttempts)]


@pytest.mark.asyncio
async def test_project_admins_upload_is_charged_to_the_creator_and_held_for_the_same_holder():
    session = UploadSession()
    app = seed_app(session, creator="u-creator")
    seed_state(session, "u-creator")
    admin = make_user("u-padmin", project_names=["proj-a"], admin_project_names=["proj-a"])

    projection = await start_upload(upload_catalog(session), session, app, admin)

    (attempt,) = attempts_added(session)
    assert attempt.quota_principal_id == "u-creator"
    assert session.states["u-creator"].reserved_bytes == attempt.held_bytes > 0
    assert projection["limits"]["byte_size"] == 20
    assert "u-padmin" not in session.states and attempt.prefix.startswith(f"a-{app.id.hex}/")


@pytest.mark.asyncio
async def test_a_suspended_apps_upload_can_still_begin():
    session = UploadSession()
    app = seed_app(session, creator="u-creator")
    app.security_state = "suspended"
    seed_state(session, "u-creator")

    await start_upload(upload_catalog(session), session, app, make_user("u-creator"))

    assert len(attempts_added(session)) == 1


@pytest.mark.asyncio
async def test_managers_uploading_to_each_others_apps_lock_one_quota_row_then_one_app_in_the_same_order():
    session = UploadSession()
    app_a, app_b = seed_app(session, creator="u-a"), seed_app(session, creator="u-b")
    seed_state(session, "u-a"), seed_state(session, "u-b")
    manager_a = make_user("u-a", project_names=["proj-a"], admin_project_names=["proj-a"])
    manager_b = make_user("u-b", project_names=["proj-a"], admin_project_names=["proj-a"])
    service = upload_catalog(session)

    await start_upload(service, session, app_b, manager_a)
    await start_upload(service, session, app_a, manager_b)

    # Each transaction takes the app creator's quota row, then that app: never the caller's own row, so no
    # two transactions can wait on each other's rows.
    assert session.locks == [
        ("quota", "u-b", "FOR UPDATE"),
        ("app", app_b.id, "FOR NO KEY UPDATE"),
        ("quota", "u-a", "FOR UPDATE"),
        ("app", app_a.id, "FOR NO KEY UPDATE"),
    ]
    assert [row.quota_principal_id for row in attempts_added(session)] == ["u-b", "u-a"]


class AttemptSession:
    """Answers one attempt lookup and records the statement."""

    def __init__(self, attempt):
        self.attempt, self.statements = attempt, []

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.statements.append((sql, statement.compile(dialect=postgresql.dialect()).params))
        return Result(NOW if "clock_timestamp" in sql else self.attempt)


@pytest.mark.asyncio
async def test_a_stopped_upload_of_a_non_creator_manager_is_marked_uncertain():
    fence = uuid4()
    attempt = HostedApplicationStorageAttempts(
        id=uuid4(), app_id=uuid4(), quota_principal_id="u-creator", state="writing", writer_fence=fence, revision=1
    )
    session = AttemptSession(attempt)

    @asynccontextmanager
    async def sessions():
        yield session

    service = HostedApplicationCatalog(SimpleNamespace(sessions=sessions), FakeDirectory())

    await service._mark_writer_stopped({"id": attempt.id, "app_id": attempt.app_id, "fence": fence})

    assert (attempt.state, attempt.revision) == ("uncertain", 2)
    sql, params = session.statements[-1]
    condition = sql.split("WHERE")[1]
    assert "owner_id" not in condition and "quota_principal_id" not in condition
    assert attempt.app_id in params.values() and attempt.id in params.values()


def version_row(**changes):
    values = {
        'id': uuid4(),
        'sequence': 1,
        'original_filename': "visual.html",
        'artifact_kind': "html",
        'expected_bytes': 20,
        'validation_state': "accepted",
        'security_state': "clear",
        'revision': 1,
        'configuration_revision': 1,
        'requested_configuration': {},
        'candidate_manifest_digest': None,
        'upload_id': uuid4(),
        'upload_generation': 1,
        'upload_state': "completed",
        'upload_revision': 1,
        'upload_expires_at': NOW,
        'created_at': NOW,
        'entrypoint_path': "index.html",
        'site_root': "",
        'routing_mode': "static",
        'file_count': 1,
        'expanded_bytes': 1,
        'accepted_at': NOW,
    }
    return HostedApplicationVersions(**{**values, **changes})


@pytest.mark.parametrize(
    "security_state,eligible,expected",
    [
        ("clear", True, True),
        ("suspended", True, False),
        ("clear", False, False),
        ("suspended", False, False),
    ],
)
def test_can_activate_is_eligible_and_not_refused_for_activate_by_a_suspension(security_state, eligible, expected):
    service = HostedApplicationCatalog(None, None)
    app = SimpleNamespace(security_state=security_state)
    attempt = HostedApplicationStorageAttempts(confirmed_bytes=20, receipt_digest=None)

    projection = service._version_projection(version_row(), attempt, None, app=app, eligible=eligible)

    assert projection["can_activate"] is expected
    assert projection["can_preview"] is eligible


@pytest.mark.asyncio
async def test_an_upload_concurrent_with_the_worker_finishing_a_validation_ends_without_a_server_error(
    processing, identity
):
    # Database-backed: both paths take the creator's quota row before the application row.
    from tests.codemie.service.hosted_applications.test_worker import queued

    service, _, _, worker, _ = processing
    context = identity()
    app, _ = await queued(service, context)
    current = await service.get_app(context, app["id"])

    async def second_upload():
        version = await service.begin_version(
            context,
            app["id"],
            VersionCreate(filename="next.html", artifact_kind="html", byte_size=5),
            current["etag"],
            str(uuid4()),
        )
        return await service.transfer(
            context, app["id"], version["version_id"], 1, version["upload_etag"], data(b"12345")
        )

    results = await asyncio.gather(second_upload(), worker.run_one(), return_exceptions=True)

    for result in results:
        assert not isinstance(result, Exception) or (
            isinstance(result, HostedApplicationError) and result.status < 500
        ), result
