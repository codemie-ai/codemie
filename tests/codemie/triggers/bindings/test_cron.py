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

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import pytz
from codemie.triggers.bindings.cron import (
    Cron,
    CronTrigger,
    Job,
    _extract_datasource_metrics,
    _tracked_run_sync,
    invoke_assistant,
    invoke_workflow,
    reindex_code,
)
from codemie.triggers.bindings.utils import validate_datasource
from codemie.triggers.actors.datasource import resume_stale_datasource  # noqa: F401 — imported for patch path resolution


@pytest.fixture
def setup():
    scheduler = MagicMock()
    jobs = {}
    cron_expression = "0 12 * * 1"
    job_id = "test_job"
    resource_id = "resource_123"
    resource_name = "Test Resource"
    user_id = "user_123"
    index_type = "code"
    return scheduler, jobs, cron_expression, job_id, resource_id, resource_name, user_id, index_type


@patch('codemie.triggers.bindings.cron.CronTrigger')
def test_add_assistant_job(mock_cron_trigger, setup):
    scheduler, jobs, cron_expression, job_id, resource_id, resource_name, user_id, index_type = setup
    cron_trigger = mock_cron_trigger.return_value
    scheduler.add_job.return_value = MagicMock()

    # Simulate adding an assistant job
    minute, hour, day_of_month, month, day_of_week = cron_expression.split()
    cron_trigger = CronTrigger(
        minute=minute,
        hour=hour,
        day=day_of_month,
        month=month,
        day_of_week=day_of_week,
    )

    instance = scheduler.add_job(
        invoke_assistant,
        trigger=cron_trigger,
        id=job_id,
        replace_existing=True,
        kwargs={
            "assistant_id": resource_id,
            "user_id": user_id,
            "job_id": job_id,
            "trigger_source": "Scheduler",
        },
    )
    jobs[job_id] = Job(job_id=job_id, modified_at=datetime.now(), instance=instance)

    assert job_id in jobs
    assert jobs[job_id].id == job_id


@patch('codemie.triggers.bindings.cron.CronTrigger')
def test_add_workflow_job(mock_cron_trigger, setup):
    scheduler, jobs, cron_expression, job_id, resource_id, resource_name, user_id, index_type = setup
    cron_trigger = mock_cron_trigger.return_value
    scheduler.add_job.return_value = MagicMock()

    # Simulate adding a workflow job
    minute, hour, day_of_month, month, day_of_week = cron_expression.split()
    cron_trigger = CronTrigger(
        minute=minute,
        hour=hour,
        day=day_of_month,
        month=month,
        day_of_week=day_of_week,
    )

    instance = scheduler.add_job(
        invoke_workflow,
        trigger=cron_trigger,
        id=job_id,
        replace_existing=True,
        kwargs={
            "workflow_id": resource_id,
            "workflow_name": resource_name,
            "user_id": user_id,
            "job_id": job_id,
        },
    )
    jobs[job_id] = Job(job_id=job_id, modified_at=datetime.now(), instance=instance)

    assert job_id in jobs
    assert jobs[job_id].id == job_id


@patch('codemie.triggers.bindings.cron.CronTrigger')
def test_reindex_code_job(mock_cron_trigger, setup):
    scheduler, jobs, cron_expression, job_id, resource_id, resource_name, user_id, index_type = setup
    cron_trigger = mock_cron_trigger.return_value
    scheduler.add_job.return_value = MagicMock()

    # Simulate reindexing code job
    minute, hour, day_of_month, month, day_of_week = cron_expression.split()
    cron_trigger = CronTrigger(
        minute=minute,
        hour=hour,
        day=day_of_month,
        month=month,
        day_of_week=day_of_week,
    )

    instance = scheduler.add_job(
        reindex_code,
        trigger=cron_trigger,
        id=job_id,
        replace_existing=True,
        kwargs={
            "index_type": index_type,
            "resource_id": resource_id,
            "resource_name": resource_name,
            "user_id": user_id,
            "job_id": job_id,
        },
    )
    jobs[job_id] = Job(job_id=job_id, modified_at=datetime.now(), instance=instance)

    assert job_id in jobs
    assert jobs[job_id].id == job_id


@pytest.fixture
def cron_instance():
    return Cron()


@pytest.mark.asyncio
async def test_start_async(cron_instance):
    with patch('codemie.triggers.bindings.cron.AsyncIOScheduler'), patch('codemie.triggers.bindings.cron.logger'):
        await cron_instance.start_async()


def test__watch_settings(cron_instance):
    with (
        patch.object(cron_instance, '_Cron__get_settings', return_value=[]),
        patch.object(cron_instance.cache, 'clean_expired', return_value=0),
    ):
        cron_instance._Cron__watch_settings()


def test_remove_jobs_for_deleted_settings(cron_instance):
    cron_instance.jobs = {'job1': Job(job_id='job1', modified_at=datetime.now(), instance=None)}
    mock_scheduler = MagicMock()
    cron_instance.scheduler = mock_scheduler

    cron_instance.remove_jobs_for_deleted_settings([])
    mock_scheduler.remove_job.assert_called_once_with('job1')
    assert 'job1' not in cron_instance.jobs


def test__valid_schedule(cron_instance):
    assert cron_instance._Cron__valid_schedule("* * * * *")
    assert not cron_instance._Cron__valid_schedule("invalid")


def test_valid_datasource(cron_instance):
    mock_datasource = MagicMock()
    mock_datasource.repo_name = 'repo'
    mock_datasource.project_name = 'project'
    mock_datasource.index_type = 'code'
    mock_datasource.jira = MagicMock(jql='mock_jql')

    with patch('codemie.rest_api.models.index.IndexInfo.get_by_id', return_value=mock_datasource):
        result = validate_datasource("datasource_id")
        assert result.repo_name == 'repo'
        assert result.project_name == 'project'
        assert result.index_type == 'code'


def test_get_settings(cron_instance):
    mock_cred_type = MagicMock()
    with patch('codemie.triggers.bindings.cron.Settings.get_all_by_fields', return_value=[]):
        settings = cron_instance._Cron__get_settings(credential_type=mock_cred_type)
        assert isinstance(settings, list)


@pytest.fixture
def mock_setting():
    setting = MagicMock()
    setting.id = "test_setting"
    setting.update_date = datetime.now()
    setting.user_id = "user_123"
    setting.credential_values = [
        MagicMock(key="is_enabled", value=True),
        MagicMock(key="resource_type", value="assistant"),
        MagicMock(key="schedule", value="0 12 * * 1"),
        MagicMock(key="resource_id", value="resource_123"),
    ]
    return setting


def test_valid_assistant_setting(cron_instance, mock_setting):
    with (
        patch.object(cron_instance, '_Cron__updated_setting', return_value=True),
        patch.object(cron_instance, '_Cron__valid_schedule', return_value=True),
        patch('codemie.triggers.bindings.cron.validate_assistant') as mock_validate_assistant,
    ):
        mock_assistant = MagicMock()
        mock_assistant.name = "Test Assistant"
        mock_validate_assistant.return_value = mock_assistant
        result = cron_instance._Cron__valid_setting(mock_setting)
        assert result["resource_name"] == "Test Assistant"
        assert result["resource_type"] == "assistant"


def test_invalid_schedule(cron_instance, mock_setting):
    with (
        patch.object(cron_instance, '_Cron__updated_setting', return_value=True),
        patch.object(cron_instance, '_Cron__valid_schedule', return_value=False),
    ):
        result = cron_instance._Cron__valid_setting(mock_setting)
        assert result is False


def test_valid_datasource_setting(cron_instance, mock_setting):
    mock_setting.credential_values = [
        MagicMock(key="is_enabled", value=True),
        MagicMock(key="resource_type", value="datasource"),
        MagicMock(key="schedule", value="0 12 * * 1"),
        MagicMock(key="resource_id", value="resource_123"),
    ]
    with (
        patch.object(cron_instance, '_Cron__updated_setting', return_value=True),
        patch.object(cron_instance, '_Cron__valid_schedule', return_value=True),
        patch('codemie.triggers.bindings.cron.validate_datasource') as mock_validate_datasource,
    ):
        mock_datasource = MagicMock()
        mock_datasource.repo_name = "repo"
        mock_datasource.project_name = "project"
        mock_datasource.index_type = "code"
        mock_datasource.jira = None
        mock_validate_datasource.return_value = mock_datasource
        result = cron_instance._Cron__valid_setting(mock_setting)
        assert result["resource_name"] == "repo"
        assert result["index_type"] == "code"


def test_invalid_resource_type(cron_instance, mock_setting):
    mock_setting.credential_values = [
        MagicMock(key="is_enabled", value=True),
        MagicMock(key="resource_type", value="Assistant"),
        MagicMock(key="schedule", value="0 12 * * 1"),
        MagicMock(key="resource_id", value="resource_123"),
    ]
    with (
        patch.object(cron_instance, '_Cron__updated_setting', return_value=True),
        patch.object(cron_instance, '_Cron__valid_schedule', return_value=True),
        patch('codemie.triggers.bindings.cron.validate_assistant') as mock_validate_assistant,
    ):
        mock_assistant = MagicMock()
        mock_assistant.name = "Test Assistant"
        mock_validate_assistant.return_value = mock_assistant
        result = cron_instance._Cron__valid_setting(mock_setting)
        assert result is not False


# ---------------------------------------------------------------------------
# Tests for the stale-indexing watchdog (__watch_stale_indexing / __run_resume)
# ---------------------------------------------------------------------------


@pytest.fixture
def cron_watchdog():
    """Return a fresh Cron instance without starting the scheduler."""
    return Cron()


class TestWatchStaleIndexing:
    """Tests for Cron.__watch_stale_indexing."""

    def test_no_stale_jobs_does_nothing(self, cron_watchdog):
        """When no stale jobs are found, no work is done."""
        with patch("codemie.triggers.bindings.cron.IndexInfo.get_stale_in_progress", return_value=[]):
            cron_watchdog._Cron__watch_stale_indexing()

        assert len(cron_watchdog._resuming_ids) == 0

    def test_already_resuming_id_is_skipped(self, cron_watchdog):
        """An index already in _resuming_ids is skipped without a DB claim attempt."""
        mock_index = MagicMock()
        mock_index.id = "idx-already"
        cron_watchdog._resuming_ids.add("idx-already")

        with (
            patch("codemie.triggers.bindings.cron.IndexInfo.get_stale_in_progress", return_value=[mock_index]),
            patch("codemie.triggers.bindings.cron.IndexInfo.try_claim_for_resume") as mock_claim,
        ):
            cron_watchdog._Cron__watch_stale_indexing()

        mock_claim.assert_not_called()
        # id is still registered because it was pre-existing
        assert "idx-already" in cron_watchdog._resuming_ids

    def test_db_claim_failure_removes_id(self, cron_watchdog):
        """When DB claim raises, the id is removed from _resuming_ids."""
        mock_index = MagicMock()
        mock_index.id = "idx-fail"

        with (
            patch("codemie.triggers.bindings.cron.IndexInfo.get_stale_in_progress", return_value=[mock_index]),
            patch(
                "codemie.triggers.bindings.cron.IndexInfo.try_claim_for_resume",
                side_effect=Exception("DB error"),
            ),
        ):
            cron_watchdog._Cron__watch_stale_indexing()

        assert "idx-fail" not in cron_watchdog._resuming_ids

    def test_claim_lost_removes_id(self, cron_watchdog):
        """When another pod claims first (rowcount == 0), id is removed."""
        mock_index = MagicMock()
        mock_index.id = "idx-lost"

        with (
            patch("codemie.triggers.bindings.cron.IndexInfo.get_stale_in_progress", return_value=[mock_index]),
            patch("codemie.triggers.bindings.cron.IndexInfo.try_claim_for_resume", return_value=False),
        ):
            cron_watchdog._Cron__watch_stale_indexing()

        assert "idx-lost" not in cron_watchdog._resuming_ids

    def test_fresh_index_vanished_removes_id(self, cron_watchdog):
        """When IndexInfo disappears after claim, id is removed."""
        mock_index = MagicMock()
        mock_index.id = "idx-gone"

        with (
            patch("codemie.triggers.bindings.cron.IndexInfo.get_stale_in_progress", return_value=[mock_index]),
            patch("codemie.triggers.bindings.cron.IndexInfo.try_claim_for_resume", return_value=True),
            patch("codemie.triggers.bindings.cron.IndexInfo.find_by_id", return_value=None),
        ):
            cron_watchdog._Cron__watch_stale_indexing()

        assert "idx-gone" not in cron_watchdog._resuming_ids

    def test_successful_claim_submits_to_executor(self, cron_watchdog):
        """A successfully claimed stale job is submitted to the thread-pool executor."""
        mock_index = MagicMock()
        mock_index.id = "idx-ok"
        fresh_index = MagicMock()

        with (
            patch("codemie.triggers.bindings.cron.IndexInfo.get_stale_in_progress", return_value=[mock_index]),
            patch("codemie.triggers.bindings.cron.IndexInfo.try_claim_for_resume", return_value=True),
            patch("codemie.triggers.bindings.cron.IndexInfo.find_by_id", return_value=fresh_index),
            patch.object(cron_watchdog._watchdog_executor, "submit") as mock_submit,
        ):
            cron_watchdog._Cron__watch_stale_indexing()

        mock_submit.assert_called_once()
        # The id is still in _resuming_ids (removed only after __run_resume finishes)
        assert "idx-ok" in cron_watchdog._resuming_ids


class TestRunResume:
    """Tests for Cron.__run_resume."""

    def test_removes_id_on_success(self, cron_watchdog):
        """ID is removed from _resuming_ids after successful resume."""
        index_info = MagicMock()
        index_info.id = "idx-s1"
        cron_watchdog._resuming_ids.add("idx-s1")

        with patch("codemie.triggers.bindings.cron.resume_stale_datasource"):
            cron_watchdog._Cron__run_resume(index_info)

        assert "idx-s1" not in cron_watchdog._resuming_ids

    def test_removes_id_even_on_exception(self, cron_watchdog):
        """ID is removed from _resuming_ids even when resume_stale_datasource raises."""
        index_info = MagicMock()
        index_info.id = "idx-e1"
        cron_watchdog._resuming_ids.add("idx-e1")

        with patch(
            "codemie.triggers.bindings.cron.resume_stale_datasource",
            side_effect=Exception("resume failed"),
        ):
            cron_watchdog._Cron__run_resume(index_info)

        assert "idx-e1" not in cron_watchdog._resuming_ids

    def test_calls_resume_stale_datasource(self, cron_watchdog):
        """Delegates to resume_stale_datasource with the index_info."""
        index_info = MagicMock()
        index_info.id = "idx-d1"
        cron_watchdog._resuming_ids.add("idx-d1")

        with patch("codemie.triggers.bindings.cron.resume_stale_datasource") as mock_resume:
            cron_watchdog._Cron__run_resume(index_info)

        mock_resume.assert_called_once_with(index_info)


# ---------------------------------------------------------------------------
# Tests for __validate_resource exception handling (EPMCDME-12780)
# ---------------------------------------------------------------------------


def test_validate_resource_datasource_not_validated(cron_instance):
    from codemie.triggers.trigger_exceptions import DatasourceNotValidated

    with patch(
        "codemie.triggers.bindings.cron.validate_datasource",
        side_effect=DatasourceNotValidated("missing setting_id"),
    ):
        result = cron_instance._Cron__validate_resource("datasource", "ds-bad", "bad resource")

    assert result is None


def test_validate_resource_not_implemented_datasource(cron_instance):
    from codemie.triggers.trigger_exceptions import NotImplementedDatasource

    with patch(
        "codemie.triggers.bindings.cron.validate_datasource",
        side_effect=NotImplementedDatasource("unsupported index_type"),
    ):
        result = cron_instance._Cron__validate_resource("datasource", "ds-bad-type", "bad resource")

    assert result is None


def test_validate_resource_caches_none_skips_second_validation(cron_instance):
    from codemie.triggers.trigger_exceptions import DatasourceNotValidated

    with patch(
        "codemie.triggers.bindings.cron.validate_datasource",
        side_effect=DatasourceNotValidated("bad"),
    ) as mock_vd:
        cron_instance._Cron__validate_resource("datasource", "ds-cached", "bad resource")
        cron_instance._Cron__validate_resource("datasource", "ds-cached", "bad resource")

    mock_vd.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for __actualize_jobs per-setting isolation (EPMCDME-12780)
# ---------------------------------------------------------------------------


def test_actualize_jobs_bad_setting_does_not_block_others(cron_instance):
    good_setting_1 = MagicMock()
    good_setting_1.id = "s-good-1"
    bad_setting = MagicMock()
    bad_setting.id = "s-bad"
    good_setting_2 = MagicMock()
    good_setting_2.id = "s-good-2"

    valid_result = {
        "schedule": "0 8 * * *",
        "resource_type": "workflow",
        "resource_id": "wf-123",
        "is_enabled": True,
        "prompt": "Do it",
        "resource_name": "Test",
        "project_name": "",
        "index_type": "",
        "jql": "",
    }

    def fake_valid_setting(setting):
        if setting.id == "s-bad":
            raise RuntimeError("unexpected DB error")
        return valid_result

    with (
        patch.object(cron_instance, "_Cron__valid_setting", side_effect=fake_valid_setting),
        patch.object(cron_instance, "_Cron__actualize_cron_job") as mock_actualize,
    ):
        cron_instance._Cron__actualize_jobs([good_setting_1, bad_setting, good_setting_2])

    assert mock_actualize.call_count == 2


# ---------------------------------------------------------------------------
# Tests for validate_datasource with Xray and SharePoint types (EPMCDME-13171)
# ---------------------------------------------------------------------------


def test_validate_datasource_xray_type_is_supported():
    mock_ds = MagicMock()
    mock_ds.is_code_index.return_value = False
    mock_ds.index_type = "knowledge_base_xray"
    mock_ds.setting_id = "some-setting-id"

    with patch("codemie.triggers.bindings.utils.IndexInfo.find_by_id", return_value=mock_ds):
        result = validate_datasource("some-ds-id")

    assert result is mock_ds


def test_validate_datasource_sharepoint_type_is_supported():
    mock_ds = MagicMock()
    mock_ds.is_code_index.return_value = False
    mock_ds.index_type = "knowledge_base_sharepoint"
    mock_ds.setting_id = "some-setting-id"

    with patch("codemie.triggers.bindings.utils.IndexInfo.find_by_id", return_value=mock_ds):
        result = validate_datasource("some-ds-id")

    assert result is mock_ds


def test_validate_datasource_xwiki_type_is_supported():
    """xWiki datasource with a setting_id must be returned by validate_datasource (EPMCDME-14794)."""
    mock_ds = MagicMock()
    mock_ds.is_code_index.return_value = False
    mock_ds.index_type = "knowledge_base_xwiki"
    mock_ds.setting_id = "some-setting-id"

    with patch("codemie.triggers.bindings.utils.IndexInfo.find_by_id", return_value=mock_ds):
        result = validate_datasource("some-ds-id")

    assert result is mock_ds


def test_validate_datasource_xwiki_without_setting_id_raises():
    """xWiki datasource without setting_id must raise DatasourceNotValidated (EPMCDME-14794)."""
    from codemie.triggers.trigger_exceptions import DatasourceNotValidated

    mock_ds = MagicMock()
    mock_ds.is_code_index.return_value = False
    mock_ds.index_type = "knowledge_base_xwiki"
    mock_ds.setting_id = None

    with patch("codemie.triggers.bindings.utils.IndexInfo.find_by_id", return_value=mock_ds):
        with pytest.raises(DatasourceNotValidated):
            validate_datasource("some-ds-id")


# ---------------------------------------------------------------------------
# Tests for __schedule_datasource_job dispatch — Xray and SharePoint (EPMCDME-13171)
# ---------------------------------------------------------------------------


def _make_mock_index_info(index_type, repo_type=None):
    mock_info = MagicMock()
    mock_info.index_type = index_type
    mock_info.repo_type = repo_type or ""
    mock_info.setting_id = "setting-123"
    mock_info.project_name = "test-project"
    mock_info.repo_name = "test-repo"
    mock_info.xray = MagicMock(jql="test jql")
    mock_info.sharepoint = MagicMock(
        site_url="https://tenant.sharepoint.com/sites/test",
        include_pages=True,
        include_documents=True,
        include_lists=True,
        max_file_size_mb=50,
        files_filter="",
        auth_type="integration",
        oauth_client_id=None,
        oauth_tenant_id=None,
    )
    return mock_info


def test_schedule_datasource_job_xray_schedules_job(cron_instance):
    from codemie.triggers.actors.datasource import reindex_xray

    mock_index_info = _make_mock_index_info("knowledge_base_xray")
    mock_scheduler = MagicMock()
    cron_instance.scheduler = mock_scheduler
    cron_trigger = MagicMock()

    with (
        patch("codemie.triggers.bindings.cron.resolve_trigger_user", return_value=MagicMock()),
        patch.object(cron_instance, "_Cron__get_index_info_cached", return_value=mock_index_info),
        patch("codemie.triggers.bindings.cron.XrayReindexTask"),
    ):
        cron_instance._Cron__schedule_datasource_job(
            index_type="knowledge_base_xray",
            cron_trigger=cron_trigger,
            job_id="job-xray-1",
            resource_id="ds-xray-1",
            user_id="user-1",
            project_name="test-project",
            resource_name="xray-ds",
            jql="test jql",
        )

    mock_scheduler.add_job.assert_called_once()
    call_kwargs = mock_scheduler.add_job.call_args
    assert call_kwargs[0][0].__name__ == "_tracked_run_sync"
    assert call_kwargs[1]["kwargs"]["actor_fn"] is reindex_xray


def test_schedule_datasource_job_sharepoint_schedules_job(cron_instance):
    from codemie.triggers.actors.datasource import reindex_sharepoint

    mock_index_info = _make_mock_index_info("knowledge_base_sharepoint")
    mock_scheduler = MagicMock()
    cron_instance.scheduler = mock_scheduler
    cron_trigger = MagicMock()

    with (
        patch("codemie.triggers.bindings.cron.resolve_trigger_user", return_value=MagicMock()),
        patch.object(cron_instance, "_Cron__get_index_info_cached", return_value=mock_index_info),
        patch("codemie.triggers.bindings.cron.SharePointReindexTask"),
    ):
        cron_instance._Cron__schedule_datasource_job(
            index_type="knowledge_base_sharepoint",
            cron_trigger=cron_trigger,
            job_id="job-sp-1",
            resource_id="ds-sp-1",
            user_id="user-1",
            project_name="test-project",
            resource_name="sharepoint-ds",
            jql="",
        )

    mock_scheduler.add_job.assert_called_once()
    call_kwargs = mock_scheduler.add_job.call_args
    assert call_kwargs[0][0].__name__ == "_tracked_run_sync"
    assert call_kwargs[1]["kwargs"]["actor_fn"] is reindex_sharepoint


# ===================== Timezone threading in trigger engine (Task 7) =====================


def test_valid_setting_includes_timezone(cron_instance, mock_setting):
    """__valid_setting should extract the timezone credential and include it in the result."""
    mock_setting.credential_values = [
        MagicMock(key="schedule", value="0 9 * * *"),
        MagicMock(key="resource_type", value="assistant"),
        MagicMock(key="resource_id", value="res-1"),
        MagicMock(key="is_enabled", value=True),
        MagicMock(key="prompt", value="run it"),
        MagicMock(key="timezone", value="Europe/Warsaw"),
    ]
    mock_setting.update_date = datetime.now()
    cron_instance.jobs = {}

    with patch.object(
        cron_instance,
        "_Cron__validate_resource",
        return_value={"resource_name": "a", "project_name": "p", "index_type": "", "jql": ""},
    ):
        result = cron_instance._Cron__valid_setting(mock_setting)

    assert result is not False
    assert result["timezone"] == "Europe/Warsaw"


def test_valid_setting_timezone_missing_returns_none(cron_instance, mock_setting):
    """When there is no timezone credential the result dict should have timezone=None."""
    mock_setting.credential_values = [
        MagicMock(key="schedule", value="0 9 * * *"),
        MagicMock(key="resource_type", value="assistant"),
        MagicMock(key="resource_id", value="res-1"),
        MagicMock(key="is_enabled", value=True),
        MagicMock(key="prompt", value="run it"),
    ]
    mock_setting.update_date = datetime.now()
    cron_instance.jobs = {}

    with patch.object(
        cron_instance,
        "_Cron__validate_resource",
        return_value={"resource_name": "a", "project_name": "p", "index_type": "", "jql": ""},
    ):
        result = cron_instance._Cron__valid_setting(mock_setting)

    assert result is not False
    assert result.get("timezone") is None


def test_create_cron_trigger_applies_timezone():
    """__create_cron_trigger should pass the timezone to CronTrigger."""
    with patch("codemie.triggers.bindings.cron.CronTrigger") as mock_trigger:
        Cron._Cron__create_cron_trigger("0 9 * * *", timezone="America/New_York")

    mock_trigger.assert_called_once()
    _, kwargs = mock_trigger.call_args
    assert kwargs.get("timezone") == "America/New_York"


def test_create_cron_trigger_falls_back_to_config_timezone():
    """When no timezone given __create_cron_trigger should use config.TIMEZONE."""
    from codemie.configs import config

    with patch("codemie.triggers.bindings.cron.CronTrigger") as mock_trigger:
        Cron._Cron__create_cron_trigger("0 9 * * *")

    mock_trigger.assert_called_once()
    _, kwargs = mock_trigger.call_args
    assert kwargs.get("timezone") == config.TIMEZONE


# ---------------------------------------------------------------------------
# EPMCDME-13171 — scheduler reliability
# ---------------------------------------------------------------------------


def _setting(setting_id="s1", creds=None):
    setting = MagicMock()
    setting.id = setting_id
    setting.update_date = datetime.now()
    setting.user_id = "user_123"
    setting.credential_values = creds or []
    return setting


def test_absent_is_enabled_is_treated_as_disabled(cron_instance):
    """A scheduler setting saved without the is_enabled key must not run.

    The Integration form omits the flag when the user never touches the toggle, and the
    integration list renders such rows as Disabled — firing them enabled a schedule the
    user never enabled (EPMCDME-14128).
    """
    setting = _setting(
        creds=[
            MagicMock(key="resource_type", value="assistant"),
            MagicMock(key="schedule", value="0 12 * * 1"),
            MagicMock(key="resource_id", value="resource_123"),
        ]
    )
    with (
        patch.object(cron_instance, '_Cron__updated_setting', return_value=True),
        patch.object(cron_instance, '_Cron__valid_schedule', return_value=True),
        patch('codemie.triggers.bindings.cron.validate_assistant') as mock_validate,
    ):
        mock_validate.return_value = MagicMock(name="assistant")
        result = cron_instance._Cron__valid_setting(setting)

    assert result["is_enabled"] is False


def test_explicit_false_is_enabled_still_disables(cron_instance):
    """An explicit false must keep disabling the schedule."""
    setting = _setting(
        creds=[
            MagicMock(key="is_enabled", value=False),
            MagicMock(key="resource_type", value="assistant"),
            MagicMock(key="schedule", value="0 12 * * 1"),
            MagicMock(key="resource_id", value="resource_123"),
        ]
    )
    with (
        patch.object(cron_instance, '_Cron__updated_setting', return_value=True),
        patch.object(cron_instance, '_Cron__valid_schedule', return_value=True),
        patch('codemie.triggers.bindings.cron.validate_assistant') as mock_validate,
    ):
        mock_validate.return_value = MagicMock(name="assistant")
        result = cron_instance._Cron__valid_setting(setting)

    assert result["is_enabled"] is False


def test_malformed_setting_does_not_abort_the_pass(cron_instance):
    """One row that explodes while being scheduled must not starve the rows after it.

    The exception used to escape __watch_settings, so every setting after the bad one
    went unprocessed — on that pass and on every pass thereafter.
    """
    settings = [_setting("bad"), _setting("good_1"), _setting("good_2")]
    processed = []

    def actualize(**kwargs):
        if kwargs["job_id"] == "bad":
            raise ValueError("not enough values to unpack (expected 5)")
        processed.append(kwargs["job_id"])

    with (
        patch.object(cron_instance, '_Cron__valid_setting', side_effect=lambda s: {"schedule": "0 1 * * *"}),
        patch.object(cron_instance, '_Cron__actualize_cron_job', side_effect=actualize),
        patch('codemie.triggers.bindings.cron.logger'),
    ):
        cron_instance._Cron__actualize_jobs(settings=settings)

    assert processed == ["good_1", "good_2"]


def test_remove_job_tolerates_missing_job(cron_instance):
    """A job the scheduler no longer knows about must not kill the watcher pass."""
    from apscheduler.jobstores.base import JobLookupError

    cron_instance.scheduler = MagicMock()
    cron_instance.scheduler.remove_job.side_effect = JobLookupError("gone")
    cron_instance.jobs = {"orphan": MagicMock()}

    with patch('codemie.triggers.bindings.cron.logger'):
        cron_instance.remove_jobs_for_deleted_settings([])  # no settings -> remove everything

    assert cron_instance.jobs == {}


def test_jobs_fire_at_the_requested_time_without_jitter(cron_instance):
    """Bursts are absorbed by the executor queue, never by moving the fire time.

    CronTrigger.jitter picks the next slot *after* the previous jittered run, so a jitter
    window wider than the schedule period silently drops runs: at 3h an hourly datasource
    fires 82 times a week instead of 168. Concurrency is controlled by
    CRON_SCHEDULER_MAX_WORKERS; spreading fire times does not help throughput.
    """
    trigger = cron_instance._Cron__create_cron_trigger("0 * * * *", timezone="UTC")

    assert trigger.jitter is None


def test_datasource_and_assistant_triggers_are_built_alike(cron_instance):
    """No resource type gets its fire time rewritten."""
    cron_instance.scheduler = MagicMock()
    captured = {}

    def fake_trigger(cron_expression, timezone=None):
        captured[cron_expression] = timezone
        return MagicMock()

    with (
        patch.object(cron_instance, '_Cron__create_cron_trigger', side_effect=fake_trigger),
        patch.object(cron_instance, '_Cron__schedule_job_by_type', return_value=None),
    ):
        cron_instance._Cron__actualize_cron_job(
            cron_expression="0 0 * * *",
            resource_id="r1",
            resource_type="datasource",
            job_id="j1",
            is_enabled=True,
            user_id="u1",
        )
        cron_instance._Cron__actualize_cron_job(
            cron_expression="0 9 * * *",
            resource_id="r2",
            resource_type="assistant",
            job_id="j2",
            is_enabled=True,
            user_id="u1",
        )

    assert set(captured) == {"0 0 * * *", "0 9 * * *"}


@pytest.mark.asyncio
async def test_scheduler_configured_with_misfire_grace(cron_instance):
    """Late jobs must run late rather than be discarded.

    misfire_grace_time=None tells APScheduler never to discard a queued job — it runs
    late instead of being silently dropped. A separate datasource executor is not needed:
    all jobs share 'default', sized by CRON_SCHEDULER_MAX_WORKERS.
    """
    with (
        patch('codemie.triggers.bindings.cron.AsyncIOScheduler') as mock_scheduler,
        patch('codemie.triggers.bindings.cron.logger'),
    ):
        await cron_instance.start_async()

    _, kwargs = mock_scheduler.call_args
    assert kwargs["job_defaults"]["misfire_grace_time"] is None
    assert kwargs["job_defaults"]["coalesce"] is True
    assert kwargs["job_defaults"]["max_instances"] == 1
    assert "datasource" not in kwargs["executors"]


@pytest.mark.asyncio
async def test_missed_and_failed_runs_are_logged(cron_instance):
    """A dropped run must leave a trace — it used to be indistinguishable from success."""
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED

    with (
        patch('codemie.triggers.bindings.cron.AsyncIOScheduler'),
        patch('codemie.triggers.bindings.cron.logger') as mock_logger,
    ):
        await cron_instance.start_async()
        listener = cron_instance.scheduler.add_listener.call_args[0][0]

        listener(MagicMock(code=EVENT_JOB_MISSED, job_id="j1", scheduled_run_time="00:00"))
        assert "MISSED" in mock_logger.warning.call_args[0][0]

        listener(MagicMock(code=EVENT_JOB_MAX_INSTANCES, job_id="j2"))
        assert "SKIPPED" in mock_logger.warning.call_args[0][0]

        listener(MagicMock(code=EVENT_JOB_ERROR, job_id="j3", scheduled_run_time="00:00", exception=ValueError("x")))
        assert "FAILED" in mock_logger.error.call_args[0][0]


@pytest.mark.asyncio
async def test_start_async_returns_while_scheduler_is_running(cron_instance):
    """start_async() must return while the APScheduler is still running.

    NodeController.start() holds the advisory lock by polling
    cron_instance.scheduler.running after start_async() returns. If start_async()
    blocked indefinitely the holding loop would never run; if it shut the scheduler
    down before returning the hold would be useless. This is the regression guard
    that would have caught the bug where NodeController tore down the scheduler
    immediately after starting it.
    """
    with patch('codemie.triggers.bindings.cron.AsyncIOScheduler') as mock_aps:
        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        mock_aps.return_value = mock_scheduler
        with patch('codemie.triggers.bindings.cron.logger'):
            await cron_instance.start_async()

    assert cron_instance.scheduler is mock_scheduler, "scheduler must be set after start_async"
    assert cron_instance.scheduler.running is True, "scheduler must still be running when start_async returns"


# --- day_of_week translation (EPMCDME-13782) -------------------------------------------
# APScheduler numbers weekdays 0=Monday, standard crontab numbers them 0=Sunday. These
# tests use a real CronTrigger and assert on the weekday actually fired, since a mocked
# trigger cannot catch an off-by-one in the value handed to it.


@pytest.mark.parametrize(
    "day_of_week,expected",
    [
        ("0", "sun"),
        ("1", "mon"),
        ("4", "thu"),
        ("6", "sat"),
        ("7", "sun"),  # crontab accepts both 0 and 7 for Sunday
        ("1-5", "mon,tue,wed,thu,fri"),
        ("0,6", "sun,sat"),
        ("0-2", "sun,mon,tue"),
        ("5-7", "fri,sat,sun"),  # range wrapping past Saturday
        ("6-0", "sat,sun"),
        ("0-6", "sun,mon,tue,wed,thu,fri,sat"),
        ("0-7", "sun,mon,tue,wed,thu,fri,sat"),  # whole week, not an empty wrap
        ("*/2", "sun,tue,thu,sat"),
        ("1-5/2", "mon,wed,fri"),
        ("2/3", "tue,fri"),  # lone number with a step runs to the end of the week
        ("1/2", "mon,wed,fri,sun"),  # ...and that end is Sunday-as-7, not Saturday
        ("0-3/2", "sun,tue"),
        ("*", "*"),
        ("mon-fri", "mon,tue,wed,thu,fri"),  # names expand the same way numbers do
        ("MON-FRI", "mon,tue,wed,thu,fri"),
        ("mon-fri/2", "mon,wed,fri"),  # APScheduler drops the step from a named range
        ("sun", "sun"),
        ("4#2", "4#2"),  # nth-weekday syntax - left for CronTrigger to parse
        ("8", "8"),  # out of range - left for CronTrigger to reject
        ("foo", "foo"),  # unknown name - left for CronTrigger to reject
    ],
)
def test_normalize_day_of_week(day_of_week, expected):
    """Crontab weekday numbers translate to the equivalent APScheduler weekday names."""
    assert Cron._Cron__normalize_day_of_week(day_of_week) == expected


@pytest.mark.parametrize(
    "cron_expression,expected_weekday",
    [
        ("41 * * * 0", "Sunday"),
        ("41 * * * 1", "Monday"),
        ("41 * * * 4", "Thursday"),  # the reported defect: fired on Friday before the fix
        ("41 * * * 6", "Saturday"),
        ("41 * * * 7", "Sunday"),
        ("41 * * * sun", "Sunday"),
    ],
)
def test_create_cron_trigger_fires_on_expected_weekday(cron_expression, expected_weekday):
    """A crontab day-of-week value fires on that day, not the day after."""
    trigger = Cron._Cron__create_cron_trigger(cron_expression, timezone="UTC")

    next_fire = trigger.get_next_fire_time(None, datetime(2026, 8, 3, tzinfo=pytz.utc))

    assert next_fire.strftime("%A") == expected_weekday
    assert next_fire.minute == 41


def test_create_cron_trigger_weekday_range_covers_working_days():
    """'1-5' means Monday through Friday, and never fires on the weekend."""
    trigger = Cron._Cron__create_cron_trigger("30 14 * * 1-5", timezone="UTC")

    fired = set()
    moment = datetime(2026, 8, 3, tzinfo=pytz.utc)
    for _ in range(5):
        moment = trigger.get_next_fire_time(None, moment)
        fired.add(moment.strftime("%A"))
        moment += timedelta(minutes=1)

    assert fired == {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}


def test_create_cron_trigger_weekday_step_fires_every_other_day():
    """'1-5/2' means every other working day - Monday, Wednesday, Friday."""
    trigger = Cron._Cron__create_cron_trigger("30 14 * * 1-5/2", timezone="UTC")

    fired = set()
    moment = datetime(2026, 8, 3, tzinfo=pytz.utc)
    for _ in range(3):
        moment = trigger.get_next_fire_time(None, moment)
        fired.add(moment.strftime("%A"))
        moment += timedelta(minutes=1)

    assert fired == {"Monday", "Wednesday", "Friday"}


def test_create_cron_trigger_without_day_of_week_fires_daily():
    """Expressions without a day-of-week restriction keep firing every day."""
    trigger = Cron._Cron__create_cron_trigger("41 * * * *", timezone="UTC")

    moment = datetime(2026, 8, 3, tzinfo=pytz.utc)
    for expected_hour in range(3):
        moment = trigger.get_next_fire_time(None, moment)
        assert moment.hour == expected_hour
        assert moment.minute == 41
        moment += timedelta(minutes=1)


# ── _tracked_run_sync tests ──────────────────────────────────────────────────


@patch("codemie.repository.scheduler_run_repository.SchedulerRunRepository")
@patch("codemie.rest_api.models.scheduler_run.SchedulerRun")
def test_tracked_run_sync_success(mock_run_cls, mock_repo_cls):
    mock_repo = mock_repo_cls.return_value
    mock_run = MagicMock(id="run-1")
    mock_repo.create.return_value = mock_run

    actor = MagicMock(return_value=None)
    _tracked_run_sync(actor, scheduler_id="sched-1", resource_id="res-1", payload=MagicMock())

    mock_run_cls.assert_called_once()
    ctor_kwargs = mock_run_cls.call_args[1]
    assert ctor_kwargs["scheduler_id"] == "sched-1"
    assert ctor_kwargs["status"] == "running"
    assert ctor_kwargs["input_data"] == {"resourceId": "res-1"}
    mock_repo.create.assert_called_once()

    mock_repo.update.assert_called_once()
    update_fields = mock_repo.update.call_args[0][1]
    assert update_fields["status"] == "completed"
    assert "finished_at" in update_fields
    assert "duration_ms" in update_fields


@patch("codemie.repository.scheduler_run_repository.SchedulerRunRepository")
@patch("codemie.rest_api.models.scheduler_run.SchedulerRun")
def test_tracked_run_sync_failure(mock_run_cls, mock_repo_cls):
    mock_repo = mock_repo_cls.return_value
    mock_run = MagicMock(id="run-2")
    mock_repo.create.return_value = mock_run

    actor = MagicMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        _tracked_run_sync(actor, scheduler_id="sched-2", resource_id="res-2")

    update_fields = mock_repo.update.call_args[0][1]
    assert update_fields["status"] == "failed"
    assert update_fields["error_data"] == {"message": "boom", "type": "RuntimeError"}
    assert "duration_ms" in update_fields


@patch("codemie.repository.scheduler_run_repository.SchedulerRunRepository")
@patch("codemie.rest_api.models.scheduler_run.SchedulerRun")
def test_tracked_run_sync_lock_skip_recorded_as_completed(mock_run_cls, mock_repo_cls):
    """Lock-skip actors return None — should still produce a completed record."""
    mock_repo = mock_repo_cls.return_value
    mock_run = MagicMock(id="run-3")
    mock_repo.create.return_value = mock_run

    actor = MagicMock(return_value=None)  # mimics lock-skip
    _tracked_run_sync(actor, scheduler_id="sched-3")

    update_fields = mock_repo.update.call_args[0][1]
    assert update_fields["status"] == "completed"


# ── _extract_datasource_metrics tests ───────────────────────────────────────


def test_extract_datasource_metrics_returns_none_when_no_index_info():
    payload = MagicMock(spec=[])  # no index_info attribute
    assert _extract_datasource_metrics(payload) is None


def test_extract_datasource_metrics_returns_none_when_payload_is_none():
    assert _extract_datasource_metrics(None) is None


@patch("codemie.rest_api.models.index.IndexInfo")
def test_extract_datasource_metrics_returns_metrics_from_index_info(mock_index_cls):
    tokens_usage = MagicMock(input_tokens=1000, output_tokens=0, money_spent=0.0002)
    fresh_index = MagicMock(tokens_usage=tokens_usage)
    mock_index_cls.get_by_id.return_value = fresh_index

    index_info = MagicMock(id="idx-1")
    payload = MagicMock(index_info=index_info)

    metrics = _extract_datasource_metrics(payload)

    mock_index_cls.get_by_id.assert_called_once_with("idx-1")
    assert metrics == {"inputTokens": 1000, "outputTokens": 0, "cost": 0.0002}


@patch("codemie.rest_api.models.index.IndexInfo")
def test_extract_datasource_metrics_returns_none_when_tokens_usage_none(mock_index_cls):
    fresh_index = MagicMock(tokens_usage=None)
    mock_index_cls.get_by_id.return_value = fresh_index

    payload = MagicMock(index_info=MagicMock(id="idx-2"))
    assert _extract_datasource_metrics(payload) is None


@patch("codemie.rest_api.models.index.IndexInfo")
def test_extract_datasource_metrics_swallows_db_errors(mock_index_cls):
    mock_index_cls.get_by_id.side_effect = RuntimeError("db down")

    payload = MagicMock(index_info=MagicMock(id="idx-3"))
    assert _extract_datasource_metrics(payload) is None


@patch(
    "codemie.triggers.bindings.cron._extract_datasource_metrics",
    return_value={"inputTokens": 500, "outputTokens": 0, "cost": 0.0001},
)
@patch("codemie.repository.scheduler_run_repository.SchedulerRunRepository")
@patch("codemie.rest_api.models.scheduler_run.SchedulerRun")
def test_tracked_run_sync_persists_metrics(mock_run_cls, mock_repo_cls, mock_extract):
    mock_repo = mock_repo_cls.return_value
    mock_run = MagicMock(id="run-4")
    mock_repo.create.return_value = mock_run

    _tracked_run_sync(MagicMock(), scheduler_id="sched-4", payload=MagicMock())

    update_fields = mock_repo.update.call_args[0][1]
    assert update_fields["metrics"] == {"inputTokens": 500, "outputTokens": 0, "cost": 0.0001}


# ── __schedule_datasource_job wiring tests ──────────────────────────────────


@patch("codemie.triggers.bindings.cron.SVNReindexTask")
@patch("codemie.triggers.bindings.cron.resolve_trigger_user")
@patch("codemie.triggers.bindings.cron.SVNRepo")
def test_schedule_datasource_svn_uses_tracked_run_sync(mock_svn_repo, mock_resolve_user, mock_svn_task):
    mock_resolve_user.return_value = MagicMock()
    index_info = MagicMock(repo_type="svn")
    svn_repo_mock = MagicMock()
    svn_repo_mock.name = "my-repo"
    mock_svn_repo.get_by_app_id.return_value = [svn_repo_mock]

    cron = Cron()
    cron.scheduler = MagicMock()
    cron.cache = MagicMock()
    cron.cache.fetch_with_cache.return_value = index_info

    cron._Cron__schedule_datasource_job(
        index_type="svn",
        cron_trigger=MagicMock(),
        job_id="job-1",
        resource_id="res-1",
        user_id="user-1",
        project_name="proj",
        resource_name="my-repo",
        jql=None,
    )

    fn_arg = cron.scheduler.add_job.call_args[0][0]
    assert fn_arg.__name__ == "_tracked_run_sync"
    kwargs = cron.scheduler.add_job.call_args[1]["kwargs"]
    assert kwargs["actor_fn"].__name__ == "reindex_svn"
    assert kwargs["scheduler_id"] == "job-1"
    assert kwargs["resource_id"] == "job-1"


@patch("codemie.triggers.bindings.cron.CodeReindexTask")
@patch("codemie.triggers.bindings.cron.resolve_trigger_user")
@patch("codemie.triggers.bindings.cron.GitRepo")
def test_schedule_datasource_code_uses_tracked_run_sync(mock_git_repo, mock_resolve_user, mock_code_task):
    mock_resolve_user.return_value = MagicMock()
    index_info = MagicMock(repo_type="git")
    mock_git_repo.identifier_from_fields.return_value = "repo-id-1"

    cron = Cron()
    cron.scheduler = MagicMock()
    cron.cache = MagicMock()
    cron.cache.fetch_with_cache.return_value = index_info

    cron._Cron__schedule_datasource_job(
        index_type="code",
        cron_trigger=MagicMock(),
        job_id="job-2",
        resource_id="res-2",
        user_id="user-2",
        project_name="proj",
        resource_name="my-repo",
        jql=None,
    )

    fn_arg = cron.scheduler.add_job.call_args[0][0]
    assert fn_arg.__name__ == "_tracked_run_sync"
    kwargs = cron.scheduler.add_job.call_args[1]["kwargs"]
    assert kwargs["actor_fn"].__name__ == "reindex_code"
    assert kwargs["scheduler_id"] == "job-2"
    assert kwargs["resource_id"] == "job-2"


# ── __schedule_knowledge_base_job wiring tests ──────────────────────────────


@pytest.mark.parametrize(
    "index_type_str,actor_name,task_cls_path",
    [
        ("knowledge_base_jira", "reindex_jira", "codemie.triggers.bindings.cron.JiraReindexTask"),
        ("knowledge_base_confluence", "reindex_confluence", "codemie.triggers.bindings.cron.ConfluenceReindexTask"),
        ("llm_routing_google", "reindex_google", "codemie.triggers.bindings.cron.GoogleReindexTask"),
        (
            "knowledge_base_azure_devops_wiki",
            "reindex_azure_devops_wiki",
            "codemie.triggers.bindings.cron.AzureDevOpsWikiReindexTask",
        ),
        (
            "knowledge_base_azure_devops_work_item",
            "reindex_azure_devops_work_item",
            "codemie.triggers.bindings.cron.AzureDevOpsWorkItemReindexTask",
        ),
        ("knowledge_base_xwiki", "reindex_xwiki", "codemie.triggers.bindings.cron.XWikiReindexTask"),
        ("knowledge_base_xray", "reindex_xray", "codemie.triggers.bindings.cron.XrayReindexTask"),
        ("knowledge_base_sharepoint", "reindex_sharepoint", "codemie.triggers.bindings.cron.SharePointReindexTask"),
    ],
)
def test_knowledge_base_jobs_use_tracked_run_sync(index_type_str, actor_name, task_cls_path):
    index_info = MagicMock(
        confluence=MagicMock(),
        google_doc_link="http://gdoc",
        azure_devops_wiki=MagicMock(),
        azure_devops_work_item=MagicMock(),
        xwiki=MagicMock(),
    )

    cron = Cron()
    cron.scheduler = MagicMock()

    with patch(task_cls_path):
        cron._Cron__schedule_knowledge_base_job(
            index_type=index_type_str,
            index_type_str=index_type_str,
            cron_trigger=MagicMock(),
            job_id="job-kb",
            user=MagicMock(),
            index_info=index_info,
            project_name="proj",
            resource_name="res",
            jql="project=X",
        )

    fn_arg = cron.scheduler.add_job.call_args[0][0]
    assert (
        fn_arg.__name__ == "_tracked_run_sync"
    ), f"{index_type_str}: expected _tracked_run_sync, got {fn_arg.__name__}"
    kwargs = cron.scheduler.add_job.call_args[1]["kwargs"]
    assert kwargs["actor_fn"].__name__ == actor_name
    assert kwargs["scheduler_id"] == "job-kb"
    assert kwargs["resource_id"] == "job-kb"
