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

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from codemie.triggers.bindings.cron import Cron, CronTrigger, Job, invoke_assistant, invoke_workflow, reindex_code
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
    ):
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
