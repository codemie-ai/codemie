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

"""Fresh linked attempts for bounded transient retries; hard findings never enter this queue."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import exists, func, select
from sqlalchemy.orm import aliased
from codemie.core.hosted_applications.policy import POLICY_DIGEST, POLICY_VERSION
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs as Job,
    HostedApplicationVersions as Version,
    HostedApplicationStorageAttempts as Attempt,
    HostedApplicationUserState as State,
    HostedApplications as App,
    HostedApplicationOperations as Operation,
)
from codemie.service.hosted_applications.uploads import expansion_bound, new_attempt, REPORT_HEADROOM
from codemie.service.hosted_applications.worker import RETRYABLE_ERROR_CODES

TRANSIENT_CODES = RETRYABLE_ERROR_CODES


class RetryWorker:
    def __init__(self, repository, storage, namespace):
        self.repository, self.storage, self.namespace = repository, storage, namespace

    async def run_one(self):
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            newer = aliased(Job)
            source_candidate = aliased(Attempt)
            # Retries are fresh jobs bound to the current policy, so failures under any older policy qualify.
            previous = (
                await session.execute(
                    select(Job)
                    .join(App, (App.id == Job.app_id))
                    .join(Version, (Version.id == Job.version_id))
                    .join(source_candidate, source_candidate.id == Version.source_attempt_id)
                    .where(
                        App.lifecycle_state == "active",
                        Version.security_state == "clear",
                        Version.validation_state == "failed",
                        Version.configuration_revision == Job.configuration_revision,
                        source_candidate.state == "stored",
                        source_candidate.receipt_digest == Job.source_digest,
                        source_candidate.provider == self.storage.provider,
                        source_candidate.namespace == self.namespace,
                        Job.kind == 'validate',
                        Job.state == 'failed',
                        Job.available_at <= now,
                        Job.safe_details['error_code'].astext.in_(TRANSIENT_CODES),
                        ~exists(
                            select(newer.id).where(
                                newer.app_id == Job.app_id,
                                newer.version_id == Job.version_id,
                                (newer.created_at > Job.created_at) | (newer.previous_job_id == Job.id),
                            )
                        ),
                    )
                    .order_by(Job.available_at)
                    .limit(1)
                    .with_for_update(of=Job, skip_locked=True)
                )
            ).scalar_one_or_none()
            if previous is None:
                return False
            retries = previous.safe_details.get('retry_count', 0)
            if type(retries) is not int or not 0 <= retries < 3:
                # Remove exhausted work from the scheduling index without altering its terminal report.
                previous.available_at = now + timedelta(days=3650)
                return False
            app = (
                await session.execute(
                    select(App)
                    .where(App.id == previous.app_id)
                    # FOR NO KEY UPDATE: launch tickets and audit rows keep their foreign-key locks on the app.
                    .with_for_update(key_share=True, skip_locked=True)
                )
            ).scalar_one_or_none()
            if app is None or app.lifecycle_state != 'active':
                return False
            state = (
                await session.execute(
                    select(State).where(State.principal_id == app.created_by.id).with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            version = (
                await session.execute(
                    select(Version).where(Version.id == previous.version_id).with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if state is None or version is None or version.configuration_revision != previous.configuration_revision:
                return False
            if version.security_state != 'clear' or version.validation_state != 'failed':
                return False
            source = await session.get(Attempt, version.source_attempt_id, with_for_update=True)
            if source is None or source.state != 'stored' or source.receipt_digest != previous.source_digest:
                return False
            active = (
                await session.execute(
                    select(func.count())
                    .select_from(Job)
                    .join(App, (App.id == Job.app_id))
                    .where(
                        App.created_by['id'].as_string() == app.created_by.id,
                        Job.kind == 'validate',
                        Job.state.in_(('ready', 'running')),
                    )
                )
            ).scalar_one()
            reserve = expansion_bound(version) + REPORT_HEADROOM
            self.repository.sync_quota(state, now)
            if active >= 2 or state.stored_bytes + state.reserved_bytes + reserve > state.byte_limit:
                previous.available_at = now + timedelta(seconds=30)
                return False
            state.reserved_bytes += reserve
            state.revision += 1
            state.updated_at = now
            output = new_attempt(
                SimpleNamespace(now=now, quota=state),
                app,
                version,
                kind='validation',
                upper_bound=reserve,
                held_bytes=reserve,
                provider=self.storage.provider,
                namespace=self.namespace,
            )
            session.add(output)
            await session.flush()
            linked = Job(
                id=uuid4(),
                app_id=app.id,
                version_id=version.id,
                kind='validate',
                state='ready',
                phase='retry_queued',
                output_attempt_id=output.id,
                previous_job_id=previous.id,
                configuration_revision=version.configuration_revision,
                source_digest=source.receipt_digest,
                policy_version=POLICY_VERSION,
                policy_definition_digest=POLICY_DIGEST,
                safe_details={'retry_count': retries + 1},
                available_at=now,
                created_at=now,
            )
            session.add(linked)
            version.validation_state = 'queued'
            version.revision += 1
            session.add(
                Operation(
                    id=uuid4(),
                    actor_kind='system',
                    actor_id='hosted-validation',
                    app_id=app.id,
                    target_id=str(version.id),
                    action='version.validation.retry',
                    state='completed',
                    response_status=202,
                    audit_details={
                        'previous_job_id': str(previous.id),
                        'job_id': str(linked.id),
                        'retry_count': retries + 1,
                    },
                    created_at=now,
                    completed_at=now,
                )
            )
            return True
