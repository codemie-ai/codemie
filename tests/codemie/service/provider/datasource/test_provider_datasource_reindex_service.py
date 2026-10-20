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

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.service.provider.datasource import provider_datasource_reindex_service as module
from codemie.service.provider.datasource.provider_datasource_reindex_service import (
    ProviderDatasourceReindexService,
)

TS = datetime(2026, 10, 19, 12, 0, 0)


@pytest.fixture
def calls() -> list:
    return []


@pytest.fixture
def datasource(calls) -> MagicMock:
    ds = MagicMock(spec=IndexInfo)
    ds.id = "ds-1"
    ds.project_name = "project"
    ds.save.side_effect = lambda: calls.append("clear_error")
    return ds


@pytest.fixture
def adapter(calls) -> MagicMock:
    adapter = MagicMock()
    adapter.reindex.side_effect = lambda **_: (calls.append("reindex"), MagicMock(status="Completed", errors=None))[1]
    return adapter


@pytest.fixture
def update_service(calls) -> MagicMock:
    service = MagicMock()
    service.run.side_effect = lambda: (calls.append("update"), True)[1]
    return service


@pytest.fixture
def make_service(datasource, adapter, update_service, calls):
    """Build the service with every collaborator patched; returns (service, stamp_mock)."""
    patches = [
        patch.object(module.Provider, "get_by_id"),
        patch.object(module, "ProviderDatasourceSchemaService"),
        patch.object(module, "ProviderDatasourceAdapter", return_value=adapter),
        patch.object(module, "ProviderDatasourceUpdateService", return_value=update_service),
        patch.object(module, "decrypt_datasource_provider_fields"),
    ]
    stamp = patch.object(
        IndexInfo, "stamp_reindex_triggered_at", side_effect=lambda _id: (calls.append("stamp"), TS)[1]
    )
    for p in patches:
        p.start()
    stamp_mock = stamp.start()

    def _make(values=None):
        service = ProviderDatasourceReindexService(datasource=datasource, user=MagicMock(spec=User), values=values)
        service._find_toolkit = MagicMock()
        return service, stamp_mock

    yield _make
    patch.stopall()


def test_run_stamps_after_clear_error_and_before_adapter_reindex(make_service, datasource, calls):
    service, stamp = make_service()

    service.run()

    stamp.assert_called_once_with("ds-1")
    assert calls == ["clear_error", "stamp", "reindex"]
    assert datasource.last_reindex_triggered_at == TS


def test_run_with_values_stamps_after_update_service(make_service, calls):
    service, stamp = make_service(values={"a": 1})

    service.run()

    stamp.assert_called_once_with("ds-1")
    assert calls == ["clear_error", "update", "stamp", "reindex"]


def test_run_update_failure_does_not_stamp(make_service, update_service):
    update_service.run.side_effect = None
    update_service.run.return_value = False
    service, stamp = make_service(values={"a": 1})

    service.run()

    stamp.assert_not_called()


def test_run_sync_success_completes_progress(make_service, datasource):
    service, _ = make_service()

    service.run()

    datasource.complete_progress.assert_called_once_with()
    datasource.start_progress.assert_not_called()


def test_run_async_started_starts_progress_and_does_not_complete(make_service, datasource, adapter):
    adapter.reindex.side_effect = None
    adapter.reindex.return_value = MagicMock(status="Started", errors=None)
    service, _ = make_service()

    service.run()

    datasource.start_progress.assert_called_once()
    datasource.complete_progress.assert_not_called()


def test_run_in_progress_starts_progress_and_does_not_complete(make_service, datasource, adapter):
    adapter.reindex.side_effect = None
    adapter.reindex.return_value = MagicMock(status="InProgress", errors=None)
    service, _ = make_service()

    service.run()

    datasource.start_progress.assert_called_once()
    datasource.complete_progress.assert_not_called()


def test_run_response_errors_calls_handle_error_no_complete(make_service, datasource, adapter):
    adapter.reindex.side_effect = None
    adapter.reindex.return_value = MagicMock(status="Failed", errors="boom")
    service, stamp = make_service()
    service._handle_error = MagicMock()

    service.run()

    stamp.assert_called_once_with("ds-1")
    service._handle_error.assert_called_once_with(datasource, "boom")
    datasource.complete_progress.assert_not_called()


def test_run_adapter_exception_calls_handle_error(make_service, datasource, adapter):
    error = RuntimeError("down")
    adapter.reindex.side_effect = error
    service, stamp = make_service()
    service._handle_error = MagicMock()

    service.run()

    stamp.assert_called_once_with("ds-1")
    service._handle_error.assert_called_once_with(datasource, error)
    datasource.complete_progress.assert_not_called()
