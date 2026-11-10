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

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select
from codemie.rest_api.models.hosted_application import (
    HostedApplications as App,
    HostedApplicationVersions as Version,
    HostedApplicationStorageAttempts as Attempt,
    HostedApplicationOperations as Operation,
    HostedApplicationUserState as State,
    HostedApplicationViewSessions as Credential,
    HostedApplicationJobs as Job,
)
from codemie.rest_api.models.permission import ResourceType, ShareLink
from codemie.rest_api.models.hosted_application_requests import (
    AppCreate,
    VersionCreate,
    UploadComplete,
    UploadReset,
    VersionSelection,
)
from codemie.service.hosted_applications.maintenance import MaintenanceWorker
from codemie.service.hosted_applications.retention import SYSTEM_ACTOR, Actor, RetentionWorker, retire_version
from tests.codemie.service.hosted_applications.test_worker import queued
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.models import CreatedByUser


@pytest.mark.asyncio
async def test_retention_keeps_latest_and_active_selected_until_physical_cleanup(processing, identity):
    service, repo, _, worker, _ = processing
    context = identity()
    app, first = await queued(service, context)
    await worker.run_one()
    ids = [UUID(first['version_id'])]
    data = b'<html>Next retained visual</html>'
    for _ in range(4):
        current = await service.get_app(context, app['id'])
        staged = await service.begin_version(
            context,
            app['id'],
            VersionCreate(filename='next.html', artifact_kind='html', byte_size=len(data)),
            current['etag'],
            str(uuid4()),
        )

        async def chunks():
            yield data

        receipt = await service.transfer(context, app['id'], staged['version_id'], 1, staged['upload_etag'], chunks())
        await service.complete_version(
            context,
            app['id'],
            staged['version_id'],
            UploadComplete(upload_id=staged['upload_id'], generation=1, sha256=receipt['sha256']),
            receipt['version_etag'],
            str(uuid4()),
        )
        await worker.run_one()
        ids.append(UUID(staged['version_id']))
    current = await service.get_app(context, app['id'])
    activated = await service.activate_version(
        context, app['id'], VersionSelection(version_id=ids[1]), current['etag'], str(uuid4())
    )
    # Activation moves the working version too; select the first one again so live and working differ.
    await service.select_version(
        context, app['id'], VersionSelection(version_id=ids[0]), activated['etag'], str(uuid4())
    )
    retention = RetentionWorker(repo, accepted=2)
    assert await retention.schedule_version_one()
    async with repo.sessions() as session:
        attempts = (await session.execute(select(Attempt).where(Attempt.version_id == ids[2]))).scalars().all()
        assert attempts and all(item.state == 'delete_requested' for item in attempts)
        assert await session.get(Version, ids[2]) is not None
    maintenance = MaintenanceWorker(repo, service.storage)
    assert not await retention.finish_version_one()
    while await maintenance.cleanup_one():
        pass
    assert await retention.finish_version_one()
    async with repo.sessions() as session:
        assert await session.get(Version, ids[2]) is None
        for retained in [ids[0], ids[1], ids[3], ids[4]]:
            assert await session.get(Version, retained) is not None
    assert not await retention.schedule_version_one()


@pytest.mark.asyncio
async def test_expired_replays_scrub_body_but_preserve_audit(processing, identity):
    service, repo, _, _, _ = processing
    app, _ = await queued(service, identity())
    async with repo.sessions() as session, session.begin():
        operations = (
            (
                await session.execute(
                    select(Operation).where(Operation.app_id == app['id'], Operation.replay_body.is_not(None))
                )
            )
            .scalars()
            .all()
        )
        for op in operations:
            op.replay_expires_at = op.created_at - timedelta(seconds=1)
    assert await RetentionWorker(repo).gc()
    async with repo.sessions() as session:
        operations = (await session.execute(select(Operation).where(Operation.app_id == app['id']))).scalars().all()
        assert operations and all(op.replay_body is None and op.action for op in operations)


async def chunks(data):
    yield data


async def add_version(service, worker, context, app, data, reset=False):
    current = await service.get_app(context, app['id'])
    staged = await service.begin_version(
        context,
        app['id'],
        VersionCreate(filename='visual.html', artifact_kind='html', byte_size=len(data)),
        current['etag'],
        str(uuid4()),
    )
    if reset:
        with pytest.raises(HostedApplicationError):
            await service.transfer(context, app['id'], staged['version_id'], 1, staged['upload_etag'], chunks(b'short'))
        current = await service.get_version(context, app['id'], staged['version_id'])
        staged = await service.reset_upload(
            context, app['id'], staged['version_id'], UploadReset(generation=1), current['upload']['etag'], str(uuid4())
        )
    receipt = await service.transfer(
        context, app['id'], staged['version_id'], staged['generation'], staged['upload_etag'], chunks(data)
    )
    await service.complete_version(
        context,
        app['id'],
        staged['version_id'],
        UploadComplete(upload_id=staged['upload_id'], generation=staged['generation'], sha256=receipt['sha256']),
        receipt['version_etag'],
        str(uuid4()),
    )
    assert await worker.run_one()
    return UUID(staged['version_id'])


@pytest.mark.asyncio
async def test_reset_cleanup_does_not_permanently_pin_accepted_version(processing, identity):
    service, repo, _, worker, _ = processing
    context = identity()
    app = await service.create_app(
        context, AppCreate(name='Reset retention', description='Test', project='demo'), str(uuid4())
    )
    data = b'<html>Valid after reset</html>'
    await add_version(service, worker, context, app, data, reset=True)
    maintenance = MaintenanceWorker(repo, service.storage)
    while await maintenance.cleanup_one():
        pass
    retention = RetentionWorker(repo, accepted=1)
    await retention.finish_version_one()
    second = await add_version(service, worker, context, app, data)
    current = await service.get_app(context, app['id'])
    await service.select_version(context, app['id'], VersionSelection(version_id=second), current['etag'], str(uuid4()))
    assert await retention.schedule_version_one(), 'Reset cleanup job permanently pins an old accepted version'


@pytest.mark.asyncio
async def test_gc_keeps_logout_binding_past_short_credential_expiry(launch, identity):
    handoff, service, worker, _ = launch
    context = identity(sid='retained-session')
    app, _ = await queued(service, context)
    await worker.run_one()
    await handoff.issue(context, app['id'], purpose='owner_preview')
    await handoff.logout(context.identity)
    now = datetime.now(timezone.utc)
    async with service.repository.sessions() as session, session.begin():
        records = (await session.execute(select(Credential))).scalars().all()
        for row in records:
            row.created_at = now - timedelta(minutes=20)
            row.expires_at = now - timedelta(minutes=19, seconds=50)
            if row.kind == 'auth_context':
                row.binding_valid_until = now + timedelta(hours=1)
    await RetentionWorker(service.repository).gc()
    async with service.repository.sessions() as session:
        records = (await session.execute(select(Credential))).scalars().all()
        assert len(records) == 1 and records[0].kind == 'auth_context' and records[0].revoked_at
    with pytest.raises(HostedApplicationError):
        await handoff.issue(context, app['id'], purpose='owner_preview')


@pytest.mark.asyncio
async def test_delete_and_physical_charge_barrier(processing, identity):
    service, repo, _, worker, _ = processing
    context = identity()
    app, _ = await queued(service, context)
    await worker.run_one()
    current = await service.get_app(context, app['id'])
    await service.delete(context, app['id'], current['etag'], str(uuid4()))
    maintenance = MaintenanceWorker(repo, service.storage)
    assert not await maintenance.finish_purge_one()
    async with repo.sessions() as session:
        state = await session.get(State, 'owner')
        assert state.stored_bytes > 0
    while await maintenance.cleanup_one():
        pass
    assert await maintenance.finish_purge_one()
    async with repo.sessions() as session:
        state = await session.get(State, 'owner')
        assert state.stored_bytes == 0 and state.reserved_bytes == 0


@pytest.mark.asyncio
async def test_version_retention_rechecks_lifecycle_after_candidate_lock(processing, identity):
    from contextlib import asynccontextmanager

    service, repo, _, worker, _ = processing
    context = identity()
    app = await service.create_app(
        context, AppCreate(name='Lifecycle race', description='Test', project='demo'), str(uuid4())
    )
    data = b'<html>Retained visual</html>'
    await add_version(service, worker, context, app, data)
    second = await add_version(service, worker, context, app, data)
    current = await service.get_app(context, app['id'])
    await service.select_version(context, app['id'], VersionSelection(version_id=second), current['etag'], str(uuid4()))
    original_sessions = repo.sessions
    fired = False

    class Proxy:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, key):
            return getattr(self.inner, key)

        async def execute(self, stmt, *args, **kwargs):
            nonlocal fired
            result = await self.inner.execute(stmt, *args, **kwargs)
            if (
                not fired
                and getattr(stmt, '_for_update_arg', None) is not None
                and str(stmt).startswith('SELECT hosted_application_versions.id')
            ):
                fired = True
                latest = await service.get_app(context, app['id'])
                await service.delete(context, app['id'], latest['etag'], str(uuid4()))
            return result

    @asynccontextmanager
    async def sessions():
        async with original_sessions() as session:
            yield Proxy(session)

    repo.sessions = sessions
    scheduled = await RetentionWorker(repo, accepted=1).schedule_version_one()
    assert fired, 'Concurrency seam did not fire'
    assert not scheduled, 'Retention scheduled deletion after app was deleted'


@pytest.mark.asyncio
async def test_retention_does_not_duplicate_jobs_while_absence_pending(processing, identity):
    service, repo, _, worker, _ = processing
    context = identity()
    app = await service.create_app(
        context, AppCreate(name='Pending retention', description='Test', project='demo'), str(uuid4())
    )
    data = b'<html>Retained visual</html>'
    first = await add_version(service, worker, context, app, data)
    second = await add_version(service, worker, context, app, data)
    current = await service.get_app(context, app['id'])
    await service.select_version(context, app['id'], VersionSelection(version_id=second), current['etag'], str(uuid4()))
    async with repo.sessions() as session, session.begin():
        row = await session.get(Version, first)
        row.created_at = datetime.now(timezone.utc) - timedelta(days=31)
    retention = RetentionWorker(repo, accepted=1)
    assert await retention.schedule_version_one()
    assert (
        not await retention.schedule_version_one()
    ), 'Physical absence pending must not create duplicate version-retention jobs'


@pytest.mark.asyncio
async def test_version_capabilities_track_current_policy_and_provenance(processing, identity, monkeypatch):
    from codemie.core.hosted_applications import policy
    from codemie.core.hosted_applications.policy import POLICY_VERSION

    service, repo, _, worker, _ = processing
    context = identity()
    app, staged = await queued(service, context)
    await worker.run_one()
    version = await service.get_version(context, app['id'], staged['version_id'])
    assert version['can_preview'] and version['can_activate']
    with monkeypatch.context() as withdrawn:
        withdrawn.delitem(policy.PERMITTED_POLICIES, POLICY_VERSION)
        version = await service.get_version(context, app['id'], staged['version_id'])
        assert not version['can_preview'] and not version['can_activate']
    version = await service.get_version(context, app['id'], staged['version_id'])
    assert version['can_preview'] and version['can_activate']
    async with repo.sessions() as session, session.begin():
        row = await session.get(Version, UUID(staged['version_id']))
        job = await session.get(Job, row.accepted_job_id)
        job.configuration_digest = b'x' * 32
    version = await service.get_version(context, app['id'], staged['version_id'])
    assert not version['can_preview'] and not version['can_activate']


@pytest.mark.asyncio
async def test_version_page_batches_authority_and_provenance_for_all_versions(processing, identity):
    from unittest.mock import AsyncMock

    service, _, directory, worker, _ = processing
    context = identity()
    app, _ = await queued(service, context)
    await worker.run_one()
    await add_version(service, worker, context, app, b'<html>Second version</html>')
    directory.require = AsyncMock(wraps=directory.require)
    page = await service.list_versions(context, app['id'])
    assert len(page['items']) == 2
    assert all(item['can_preview'] and item['can_activate'] for item in page['items'])
    assert directory.require.await_count == 1


@pytest.mark.asyncio
async def test_keyless_operations_store_sql_null_and_replay_scrub_terminates(processing, identity):
    from sqlalchemy import text

    service, repo, _, _, _ = processing
    app, _ = await queued(service, identity())
    async with repo.sessions() as session:
        json_null, sql_null = (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE replay_body = 'null'::jsonb), "
                    "count(*) FILTER (WHERE replay_body IS NULL) FROM hosted_application_operations"
                )
            )
        ).one()
    assert json_null == 0 and sql_null >= 1, 'Keyless audit rows must store SQL NULL, never JSON null'
    async with repo.sessions() as session, session.begin():
        for op in (await session.execute(select(Operation).where(Operation.replay_body.is_not(None)))).scalars().all():
            op.replay_expires_at = op.created_at - timedelta(seconds=1)
    retention = RetentionWorker(repo)
    assert await retention.gc()
    assert not await retention.gc(), 'Scrubbed replay bodies must not be selected again on every cycle'
    async with repo.sessions() as session:
        remaining = (
            await session.execute(
                text("SELECT count(*) FROM hosted_application_operations WHERE replay_body IS NOT NULL")
            )
        ).scalar_one()
    assert remaining == 0


@pytest.mark.asyncio
async def test_canceled_version_needs_no_bookkeeping_job_and_is_retained_until_due(processing, identity):
    service, repo, _, _, _ = processing
    context = identity()
    app, staged = await queued(service, context)
    version = await service.get_version(context, app['id'], staged['version_id'])
    await service.version_action(context, app['id'], version['id'], 'cancel', version['etag'], str(uuid4()))
    async with repo.sessions() as session:
        jobs = (await session.execute(select(Job).where(Job.version_id == UUID(staged['version_id'])))).scalars().all()
        assert {job.kind for job in jobs} == {'validate'} and all(job.state == 'canceled' for job in jobs)
    maintenance = MaintenanceWorker(repo, service.storage)
    while await maintenance.cleanup_one():
        pass
    async with repo.sessions() as session:
        attempts = (
            (await session.execute(select(Attempt).where(Attempt.version_id == UUID(staged['version_id']))))
            .scalars()
            .all()
        )
        state = await session.get(State, 'owner')
    assert attempts and all(attempt.state == 'deleted' for attempt in attempts)
    assert (state.stored_bytes, state.reserved_bytes) == (0, 0), 'Cleanup alone refunds a canceled version'
    retention = RetentionWorker(repo)
    assert not await retention.schedule_version_one(), 'A canceled version is kept until its retention is due'
    async with repo.sessions() as session, session.begin():
        row = await session.get(Version, UUID(staged['version_id']))
        row.created_at = datetime.now(timezone.utc) - timedelta(days=31)
    assert await retention.schedule_version_one()
    assert not await retention.schedule_version_one(), 'The version-retention job makes scheduling idempotent'


@pytest.mark.asyncio
async def test_gc_prunes_every_audit_record_older_than_the_audit_period_in_batches(processing, identity):
    service, repo, _, _, _ = processing
    context = identity()
    app = await service.create_app(
        context, AppCreate(name='Audit retention', description='Test', project='demo'), str(uuid4())
    )
    now = datetime.now(timezone.utc)

    def audit(action, age):
        return Operation(
            id=uuid4(),
            actor_id='viewer',
            actor_kind='user',
            app_id=UUID(app['id']),
            target_id=app['id'],
            action=action,
            state='completed',
            response_status=200,
            audit_details={'purpose': 'owner_preview'},
            created_at=now - age,
            completed_at=now - age,
        )

    async with repo.sessions() as session, session.begin():
        session.add_all([audit('app.launch', timedelta(days=200)) for _ in range(5)])
        session.add_all([audit('app.launch', timedelta(days=1)) for _ in range(2)])
        session.add(audit('app.update', timedelta(days=400)))
    assert await RetentionWorker(repo, audit_days=180, batch_size=2).gc()
    async with repo.sessions() as session:
        rows = (await session.execute(select(Operation).where(Operation.app_id == UUID(app['id'])))).scalars().all()
    launches = [row for row in rows if row.action == 'app.launch']
    assert len(launches) == 2 and all(row.created_at > now - timedelta(days=2) for row in launches)
    # The fresh create record stays; the 400-day-old update record is gone like the old launches.
    assert {row.action for row in rows} == {'app.create', 'app.launch'}


@pytest.mark.asyncio
async def test_gc_drains_expired_view_credentials_in_bounded_batches(launch, identity):
    handoff, service, worker, _ = launch
    context = identity()
    app, _ = await queued(service, context)
    assert await worker.run_one()
    for _ in range(5):
        await handoff.issue(context, app['id'], purpose='owner_preview')
    now = datetime.now(timezone.utc)
    async with service.repository.sessions() as session, session.begin():
        for row in (await session.execute(select(Credential).where(Credential.kind != 'auth_context'))).scalars():
            row.created_at = now - timedelta(minutes=20)
            row.expires_at = now - timedelta(minutes=19, seconds=50)
    assert await RetentionWorker(service.repository, batch_size=2).gc()
    async with service.repository.sessions() as session:
        remaining = (await session.execute(select(Credential).where(Credential.kind != 'auth_context'))).scalars().all()
    assert remaining == [], 'One GC cycle must drain every expired credential in bounded batches'


@pytest.mark.asyncio
async def test_gc_removes_purged_application_rows_after_the_audit_period(processing, identity):
    from sqlalchemy import update

    service, repo, _, _, _ = processing
    context = identity()
    old = await service.create_app(
        context, AppCreate(name='Old purge', description='Test', project='demo'), str(uuid4())
    )
    recent = await service.create_app(
        context, AppCreate(name='Recent purge', description='Test', project='demo'), str(uuid4())
    )
    now = datetime.now(timezone.utc)
    async with repo.sessions() as session, session.begin():
        for app_id, age in ((old['id'], timedelta(days=200)), (recent['id'], timedelta(days=10))):
            await session.execute(
                update(App)
                .where(App.id == UUID(app_id))
                .values(
                    lifecycle_state='purged',
                    created_by=CreatedByUser(id=context.principal_id),
                    name=None,
                    description=None,
                    search_text='',
                    deleted_at=now - age,
                    purged_at=now - age,
                )
            )
    assert await RetentionWorker(repo, audit_days=180).gc()
    async with repo.sessions() as session:
        remaining = set((await session.execute(select(App.id))).scalars().all())
        audits = (await session.execute(select(Operation.app_id))).scalars().all()
    # The old tombstone goes with its audit records; the recent one stays for the audit period.
    assert UUID(old['id']) not in remaining and UUID(recent['id']) in remaining
    assert UUID(old['id']) not in audits and UUID(recent['id']) in audits


class PurgeSession:
    """Answers the stale-app lookup and records every statement the purge executes."""

    def __init__(self, stale):
        self.stale, self.statements = stale, []

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        return SimpleNamespace(
            scalar_one=lambda: NOW,
            scalars=lambda: SimpleNamespace(all=lambda: self.stale),
            rowcount=len(self.stale),
        )


@pytest.mark.asyncio
async def test_purged_application_deletion_removes_its_share_links_before_the_application_row():
    session = PurgeSession([uuid4()])

    @asynccontextmanager
    async def sessions():
        yield session

    worker = RetentionWorker(SimpleNamespace(sessions=sessions), audit_days=180)

    assert await worker._delete_purged_applications() == 1

    deletes = [s for s in session.statements if s.startswith('DELETE FROM')]
    tables = [s.split()[2] for s in deletes]
    assert 'share_links' in tables
    assert tables.index('share_links') < tables.index('hosted_applications')
    link_delete = next(s for s in deletes if s.split()[2] == 'share_links')
    assert 'share_links.resource_type' in link_delete and 'share_links.resource_id IN' in link_delete


@pytest.mark.asyncio
async def test_gc_deletes_a_purged_application_together_with_its_revoked_share_link(processing, identity):
    from sqlalchemy import update

    service, repo, _, _, _ = processing
    context = identity()
    old = await service.create_app(
        context, AppCreate(name='Linked purge', description='Test', project='demo'), str(uuid4())
    )
    app_id = UUID(old['id'])
    now = datetime.now(timezone.utc)
    async with repo.sessions() as session, session.begin():
        await session.execute(
            update(App)
            .where(App.id == app_id)
            .values(
                lifecycle_state='purged',
                created_by=CreatedByUser(id=context.principal_id),
                name=None,
                description=None,
                search_text='',
                deleted_at=now - timedelta(days=200),
                purged_at=now - timedelta(days=200),
            )
        )
        session.add(
            ShareLink(
                id=uuid4(),
                resource_type=ResourceType.HOSTED_APPLICATION,
                resource_id=app_id,
                token='t',
                token_sha256=uuid4().hex,
                created_by=CreatedByUser(id=context.principal_id),
                created_at=now - timedelta(days=201),
                revoked_at=now - timedelta(days=200),
                revoked_by=CreatedByUser(id=context.principal_id),
            )
        )
    assert await RetentionWorker(repo, audit_days=180).gc()
    async with repo.sessions() as session:
        assert app_id not in set((await session.execute(select(App.id))).scalars().all())
        assert not (await session.execute(select(ShareLink.id).where(ShareLink.resource_id == app_id))).scalars().all()


NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


class RetireSession:
    """Answers retire_version's one job lookup and records what it writes; it can take no lock."""

    def __init__(self, busy=False):
        self.busy, self.added, self.statements = busy, [], []

    async def execute(self, statement):
        sql = str(statement)
        assert 'FOR UPDATE' not in sql, 'retire_version must take no locks of its own'
        self.statements.append(sql)
        return SimpleNamespace(first=lambda: (uuid4(),) if self.busy else None, rowcount=1)

    def add(self, row):
        self.added.append(row)


def retire_setup(*, active=False, selected=False, busy=False):
    version = Version(id=uuid4(), app_id=uuid4(), validation_state='accepted', security_state='clear', revision=1)
    app = App(
        id=version.app_id,
        active_version_id=version.id if active else None,
        selected_version_id=version.id if selected else None,
    )
    session = RetireSession(busy=busy)
    return SimpleNamespace(session=session, now=NOW), app, version


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'kwargs',
    [
        pytest.param({'active': True}, id='active'),
        pytest.param({'selected': True}, id='selected'),
        pytest.param({'busy': True}, id='running-job'),
    ],
)
async def test_retire_version_refuses_a_version_in_use_with_409(kwargs):
    tx, app, version = retire_setup(**kwargs)

    with pytest.raises(HostedApplicationError) as error:
        await retire_version(tx, app, version, SYSTEM_ACTOR)

    assert error.value.status == 409
    assert version.validation_state == 'accepted' and tx.session.added == []


@pytest.mark.asyncio
async def test_retire_version_blocks_the_version_queues_cleanup_and_audits_the_actor():
    tx, app, version = retire_setup()

    await retire_version(tx, app, version, Actor('u-admin', 'user'))

    assert (version.validation_state, version.security_state, version.revision) == ('canceled', 'blocked', 2)
    job, audit = tx.session.added
    assert (job.kind, job.state, job.phase, job.version_id) == (
        'cleanup_upload',
        'ready',
        'version_retention',
        version.id,
    )
    assert (audit.actor_id, audit.actor_kind, audit.action, audit.target_id) == (
        'u-admin',
        'user',
        'version.retention',
        str(version.id),
    )
    update = tx.session.statements[-1]
    assert 'owner_id' not in update and 'app_id' in update
