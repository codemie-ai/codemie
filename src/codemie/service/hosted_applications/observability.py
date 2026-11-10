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

"""Bounded operational metrics. No identities, URLs, filenames or artifact data become labels."""

from __future__ import annotations

import asyncio
import sys
from functools import lru_cache
from contextlib import suppress
from time import monotonic
from opentelemetry import metrics
from opentelemetry.metrics import Observation
from sqlalchemy import func, select
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.models.hosted_application import HostedApplicationJobs as Job, HostedApplicationUserState as State

OPERATIONS = frozenset(('validation', 'cleanup', 'retry', 'purge', 'retention'))
# Best-effort revocation after platform logout.
LOGOUT_RESULTS = frozenset(('revoked', 'failed', 'timeout'))


def response_result(status: int) -> str:
    """Fixed result class of a hosted response; capacity and outages are never counted as denials."""
    if status < 400:
        return 'served'
    if status in (401, 403):
        return 'denied'
    if status == 429:
        return 'throttled'
    if status >= 500:
        return 'unavailable'
    return 'rejected'


def failure_result(error: BaseException | None) -> str:
    """'denied' when the identity or request was refused, 'unavailable' for outages and unexpected errors."""
    if isinstance(error, HostedApplicationError) and response_result(error.status) in ('denied', 'rejected'):
        return 'denied'
    return 'unavailable'


class HostedApplicationsMonitoring:
    def __init__(self, sessions, *, meter=None):
        self.sessions = sessions
        self.snapshot = {}
        self.last_refresh = None
        meter = meter or metrics.get_meter('codemie-business-metrics')
        self.completed = meter.create_counter(
            'hosted_applications_completed_work', description='Completed durable worker steps'
        )
        self.deferred = meter.create_counter(
            'hosted_applications_deferred_work', description='Worker steps deferred by exceptions'
        )
        self.duration = meter.create_histogram('hosted_applications_work_seconds', unit='s')
        for name in ('queue_jobs', 'oldest_queue_seconds', 'stored_bytes', 'reserved_bytes'):
            meter.create_observable_gauge('hosted_applications_' + name, callbacks=[self.callback(name)])
        meter.create_observable_gauge('hosted_applications_snapshot_age_seconds', callbacks=[self.snapshot_age])

    def callback(self, name):
        def collect(_):
            return tuple(Observation(value, attributes) for value, attributes in self.snapshot.get(name, ()))

        return collect

    def snapshot_age(self, _):
        return () if self.last_refresh is None else (Observation(max(0, monotonic() - self.last_refresh)),)

    async def refresh(self):
        async with asyncio.timeout(5), self.sessions() as session:
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            queue = (
                await session.execute(
                    select(Job.kind, Job.state, func.count(), func.min(Job.available_at))
                    .where(Job.state.in_(('ready', 'running')))
                    .group_by(Job.kind, Job.state)
                )
            ).all()
            stored, reserved = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(State.stored_bytes), 0), func.coalesce(func.sum(State.reserved_bytes), 0)
                    )
                )
            ).one()
        self.snapshot = {
            'queue_jobs': tuple((count, {'kind': kind, 'state': state}) for kind, state, count, _ in queue),
            'oldest_queue_seconds': tuple(
                (max(0, (now - oldest).total_seconds()), {'kind': kind, 'state': state})
                for kind, state, _, oldest in queue
            ),
            'stored_bytes': ((stored, {}),),
            'reserved_bytes': ((reserved, {}),),
        }
        self.last_refresh = monotonic()

    async def run(self, step, operation):
        if operation not in OPERATIONS:
            raise ValueError('Unknown hosted monitoring operation')
        started = monotonic()
        try:
            completed = await step()
        except Exception:
            with suppress(Exception):
                self.deferred.add(1, {'operation': operation})
            raise
        if completed:
            with suppress(Exception):
                self.completed.add(1, {'operation': operation})
                self.duration.record(monotonic() - started, {'operation': operation})
        return completed


class HostedRequestMonitoring:
    """Fixed-cardinality signals for the content tree, live directory and logout revocation.

    Every point carries a fixed `result` label. Denials count only requests refused for the caller
    (denied: 401/403, rejected: other 4xx); throttling (429) and outages (5xx) are counted as unavailable,
    so capacity incidents never hide inside, or masquerade as, authorization failures.
    """

    def __init__(self, meter=None):
        meter = meter or metrics.get_meter('codemie-business-metrics')
        self.requests = meter.create_counter('hosted_applications_content_requests')
        self.denials = meter.create_counter(
            'hosted_applications_content_denials', description='Content requests refused for the caller'
        )
        self.unavailable = meter.create_counter(
            'hosted_applications_content_unavailable', description='Content requests not served: throttled or outage'
        )
        self.content_seconds = meter.create_histogram('hosted_applications_content_seconds', unit='s')
        self.bandwidth = meter.create_counter('hosted_applications_content_bytes', unit='By')
        self.directory_seconds = meter.create_histogram('hosted_applications_directory_seconds', unit='s')
        self.validation_failures = meter.create_counter('hosted_applications_validation_failures')
        self.logout_revocations = meter.create_counter(
            'hosted_applications_logout_revocations', description='Best-effort hosted revocation after platform logout'
        )

    def content(self, status, size, duration, *, bootstrap=False):
        result = response_result(status)
        attributes = {'status': status, 'route': 'bootstrap' if bootstrap else 'asset', 'result': result}
        with suppress(Exception):
            self.requests.add(1, attributes)
            if result in ('denied', 'rejected'):
                self.denials.add(1, attributes)
            elif result in ('throttled', 'unavailable'):
                self.unavailable.add(1, attributes)
            self.content_seconds.record(duration, attributes)
            self.bandwidth.add(max(0, size), attributes)

    def directory(self, duration, allowed, error=None):
        """Lookup latency by result. Called from a `finally`, the in-flight exception classifies a failure."""
        with suppress(Exception):
            failure = error if error is not None else sys.exception()
            result = 'allowed' if allowed else failure_result(failure)
            self.directory_seconds.record(duration, {'result': result})

    def validation(self, hard):
        with suppress(Exception):
            self.validation_failures.add(1, {'kind': 'artifact_rejected' if hard else 'service_unavailable'})

    def logout(self, result):
        with suppress(Exception):
            if result in LOGOUT_RESULTS:
                self.logout_revocations.add(1, {'result': result})


@lru_cache(maxsize=1)
def hosted_request_monitoring():
    return HostedRequestMonitoring()
