# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.index import XWikiIndexInfo
from codemie.triggers.actors.datasource import _RESUME_DISPATCH, reindex_xwiki
from codemie.triggers.trigger_models import XWikiReindexTask

_ACTORS = "codemie.triggers.actors.datasource"


def _payload():
    index_info = MagicMock()
    index_info.id = "idx-1"
    index_info.index_type = "knowledge_base_xwiki"
    index_info.xwiki = XWikiIndexInfo(space="KB", wiki="xwiki")
    index_info.description = "d"
    index_info.project_space_visible = True
    index_info.embeddings_model = "text-embedding-3-small"

    payload = MagicMock(spec=XWikiReindexTask)
    payload.index_info = index_info
    payload.project_name = "p1"
    payload.resource_id = "job-1"
    payload.resource_name = "kb_ds"
    payload.user = MagicMock(id="u1")
    return payload


@pytest.fixture(autouse=True)
def granted_datasource_job_lock():
    """Patch the DB-backed job lock so tests don't open a real Postgres connection."""

    @contextmanager
    def _granted(_datasource_id):
        yield True

    with patch("codemie.triggers.job_lock.datasource_job_lock", new=_granted):
        yield


def test_job_lock_is_applied_to_reindex_xwiki():
    """reindex_xwiki must hold the job lock; without it two pods entering reprocess()
    concurrently produce a torn or duplicated ES index."""

    @contextmanager
    def _not_acquired(_datasource_id):
        yield False

    with (
        patch("codemie.triggers.job_lock.datasource_job_lock", new=_not_acquired),
        patch(f"{_ACTORS}.IndexInfo.stamp_reindex_triggered_at"),
        patch(f"{_ACTORS}.SettingsService.get_xwiki_creds", return_value=MagicMock()),
        patch(f"{_ACTORS}.datasource_concurrency_manager") as manager,
    ):
        result = reindex_xwiki(_payload())

    assert result is None
    manager.run.assert_not_called()


def test_scheduled_reindex_is_a_full_rebuild():
    """Matches every comparable type: reprocess() deletes the ES index and rebuilds it."""
    processor = MagicMock()
    with (
        patch(f"{_ACTORS}.IndexInfo.stamp_reindex_triggered_at"),
        patch(f"{_ACTORS}.SettingsService.get_xwiki_creds", return_value=MagicMock()),
        patch(f"{_ACTORS}.XWikiDatasourceProcessor", return_value=processor),
        patch(f"{_ACTORS}.datasource_concurrency_manager") as manager,
    ):
        reindex_xwiki(_payload())
    manager.run.assert_called_once()
    assert manager.run.call_args.args[0] == processor.reprocess


def test_missing_index_info_aborts_without_running():
    payload = _payload()
    payload.index_info.xwiki = None
    with (
        patch(f"{_ACTORS}.IndexInfo.stamp_reindex_triggered_at"),
        patch(f"{_ACTORS}.datasource_concurrency_manager") as manager,
    ):
        reindex_xwiki(payload)
    manager.run.assert_not_called()


def test_missing_credentials_abort_without_running():
    with (
        patch(f"{_ACTORS}.IndexInfo.stamp_reindex_triggered_at"),
        patch(f"{_ACTORS}.SettingsService.get_xwiki_creds", return_value=None),
        patch(f"{_ACTORS}.datasource_concurrency_manager") as manager,
    ):
        reindex_xwiki(_payload())
    manager.run.assert_not_called()


def test_stale_job_watchdog_knows_about_xwiki():
    assert "knowledge_base_xwiki" in _RESUME_DISPATCH


def test_cron_binding_handles_the_xwiki_type():
    import inspect

    from codemie.triggers.bindings.cron import Cron

    source = inspect.getsource(Cron)
    assert "FullDatasourceTypes.XWIKI.value" in source
    assert "reindex_xwiki" in source


def test_scheduled_reindex_uses_the_datasource_integration():
    """Without setting_id a cron rebuild authenticates against whichever integration the project
    resolves by default - and reprocess() has already deleted the index by then."""
    payload = _payload()
    payload.index_info.setting_id = "setting-42"
    with (
        patch(f"{_ACTORS}.IndexInfo.stamp_reindex_triggered_at"),
        patch(f"{_ACTORS}.SettingsService.get_xwiki_creds", return_value=MagicMock()) as get_creds,
        patch(f"{_ACTORS}.XWikiDatasourceProcessor", return_value=MagicMock()),
        patch(f"{_ACTORS}.datasource_concurrency_manager"),
    ):
        reindex_xwiki(payload)
    assert get_creds.call_args.kwargs["setting_id"] == "setting-42"


def test_reindex_payload_tolerates_missing_index_info():
    """index_info.xwiki is Optional, so a required payload field would raise a ValidationError
    while the cron job is being registered."""
    field = XWikiReindexTask.model_fields["xwiki_index_info"]
    assert not field.is_required(), "xwiki_index_info must be optional; cron builds it from index_info.xwiki"
