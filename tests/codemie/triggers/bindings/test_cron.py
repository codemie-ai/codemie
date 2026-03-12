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
