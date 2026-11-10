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

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from codemie.service.hosted_applications.observability import HostedApplicationsMonitoring
from tests.codemie.service.hosted_applications.test_worker import queued


@pytest.mark.asyncio
async def test_metrics_report_real_queue_and_charges_without_identity_labels(processing, identity):
    service, repo, _, worker, _ = processing
    await queued(service, identity())
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monitoring = HostedApplicationsMonitoring(repo.sessions, meter=provider.get_meter('hosted-test'))
    await monitoring.refresh()
    data = reader.get_metrics_data()
    values = {
        metric.name: metric
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    queued_points = values['hosted_applications_queue_jobs'].data.data_points
    assert sum(point.value for point in queued_points if point.attributes['state'] == 'ready') == 1
    assert values['hosted_applications_reserved_bytes'].data.data_points[0].value > 0
    assert await monitoring.run(worker.run_one, 'validation')
    await monitoring.refresh()
    data = reader.get_metrics_data()
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                for point in metric.data.data_points:
                    assert set(point.attributes).issubset({'state', 'kind', 'operation'})
                    assert 'owner' not in str(point.attributes)
    provider.shutdown()


def test_content_directory_and_scanner_signals_have_fixed_labels():
    from codemie.service.hosted_applications.observability import HostedRequestMonitoring

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monitor = HostedRequestMonitoring(provider.get_meter('request-test'))
    monitor.content(401, 23, 0.01)
    monitor.content(200, 100, 0.02, bootstrap=True)
    monitor.directory(0.01, False)
    monitor.validation(False)
    values = {
        metric.name: metric
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert values['hosted_applications_content_denials'].data.data_points[0].value == 1
    assert sum(point.value for point in values['hosted_applications_content_bytes'].data.data_points) == 123
    for metric in values.values():
        for point in metric.data.data_points:
            assert set(point.attributes).issubset({'status', 'route', 'result', 'kind'})
    provider.shutdown()


def _metrics(reader):
    return {
        metric.name: metric
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def _by_result(metric, value=lambda point: point.value):
    totals = {}
    for point in metric.data.data_points:
        totals[point.attributes['result']] = totals.get(point.attributes['result'], 0) + value(point)
    return totals


def test_throttling_and_outages_are_unavailable_not_denials():
    from codemie.service.hosted_applications.observability import HostedRequestMonitoring

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monitor = HostedRequestMonitoring(provider.get_meter('request-test'))
    for status in (200, 401, 403, 404, 429, 503, 500):
        monitor.content(status, 0, 0.01)
    values = _metrics(reader)
    assert _by_result(values['hosted_applications_content_denials']) == {'denied': 2, 'rejected': 1}
    assert _by_result(values['hosted_applications_content_unavailable']) == {'throttled': 1, 'unavailable': 2}
    assert sum(_by_result(values['hosted_applications_content_requests']).values()) == 7
    provider.shutdown()


def test_directory_failures_are_split_into_denied_and_unavailable():
    from codemie.core.hosted_applications.errors import HostedApplicationError
    from codemie.service.hosted_applications.observability import HostedRequestMonitoring

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monitor = HostedRequestMonitoring(provider.get_meter('request-test'))
    monitor.directory(0.01, True)
    monitor.directory(0.01, False, HostedApplicationError('NOT_FOUND', 'Application not found', 404))
    monitor.directory(0.01, False, HostedApplicationError('AUTHORIZATION_UNAVAILABLE', 'Unavailable', 503))
    monitor.directory(0.01, False, RuntimeError('database down'))
    for error in (HostedApplicationError('NOT_FOUND', 'Application not found', 404), TimeoutError()):
        with pytest.raises(type(error)):
            try:
                raise error
            finally:
                monitor.directory(0.01, False)  # the call site in directory.py passes no error
    histogram = _metrics(reader)['hosted_applications_directory_seconds']
    assert _by_result(histogram, lambda point: point.count) == {'allowed': 1, 'denied': 2, 'unavailable': 3}
    provider.shutdown()


def test_logout_revocation_results_use_fixed_labels():
    from codemie.service.hosted_applications.observability import HostedRequestMonitoring

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monitor = HostedRequestMonitoring(provider.get_meter('request-test'))
    for result in ('revoked', 'failed', 'timeout', 'user-1@example.com'):
        monitor.logout(result)
    counter = _metrics(reader)['hosted_applications_logout_revocations']
    assert _by_result(counter) == {'revoked': 1, 'failed': 1, 'timeout': 1}
    provider.shutdown()
