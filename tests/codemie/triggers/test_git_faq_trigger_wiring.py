# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Trigger wiring for knowledge_base_git_faq: webhook dispatch, cron scheduling, resume classification."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.models import CreatedByUser
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.service.constants import FullDatasourceTypes
from codemie.triggers.actors.datasource import UNSUPPORTED_RESUME_TYPES, reindex_git_faq
from codemie.triggers.bindings.utils import validate_datasource
from codemie.triggers.trigger_models import GitFaqReindexTask

FAQ_TYPE = FullDatasourceTypes.GIT_FAQ.value


def make_faq_index(setting_id=None) -> IndexInfo:
    return IndexInfo(
        id="ds-id",
        created_by=CreatedByUser(id="user-1", username="tester", name="Tester"),
        repo_name="faq-ds",
        project_name="proj",
        index_type=FAQ_TYPE,
        link="https://git.example.com/team/docs.git",
        branch="main",
        files_filter="docs/**/*.md",
        setting_id=setting_id,
        description="",
        embeddings_model="ada",
    )


def make_user() -> User:
    return User(id="user-1", username="tester", name="Tester")


class TestValidateDatasource:
    def test_faq_datasource_is_accepted_without_setting_id(self):
        index = make_faq_index(setting_id=None)

        with patch("codemie.triggers.bindings.utils.IndexInfo.find_by_id", return_value=index):
            result = validate_datasource("ds-id")

        assert result is index

    def test_faq_datasource_is_accepted_with_setting_id(self):
        index = make_faq_index(setting_id="git-integration")

        with patch("codemie.triggers.bindings.utils.IndexInfo.find_by_id", return_value=index):
            result = validate_datasource("ds-id")

        assert result is index


class TestWebhookDispatch:
    @pytest.mark.asyncio
    async def test_faq_datasource_dispatches_reindex_git_faq(self):
        from codemie.triggers.bindings.webhook import WebhookService

        index = make_faq_index()
        background_tasks = MagicMock()
        setting = MagicMock()
        setting.project_name = "proj"

        with (
            patch("codemie.triggers.bindings.webhook.validate_datasource", return_value=index),
            patch("codemie.triggers.bindings.webhook.reindex_git_faq") as mock_reindex,
            patch("codemie.triggers.bindings.webhook.resolve_trigger_user", return_value=make_user()),
        ):
            WebhookService.handle_datasource(index.id, background_tasks, setting)

        mock_reindex.assert_not_called()  # called via background task, not directly
        assert background_tasks.add_task.call_count == 1
        task_func, task_payload = background_tasks.add_task.call_args.args
        assert task_func is mock_reindex
        assert isinstance(task_payload, GitFaqReindexTask)
        assert task_payload.index_info is index


class TestCronScheduling:
    def test_faq_type_schedules_reindex_git_faq_job(self):
        """The cron binder must create a job for knowledge_base_git_faq instead of the log-only no-op."""
        from codemie.triggers.bindings.cron import Cron

        index = make_faq_index()
        scheduler = MagicMock()
        binder = Cron.__new__(Cron)  # bypass __init__ (starts APScheduler)
        binder.scheduler = scheduler

        with (
            patch.object(Cron, "_Cron__get_index_info_cached", return_value=index),
            patch("codemie.triggers.bindings.cron.validate_datasource", return_value=index),
            patch("codemie.triggers.bindings.cron.resolve_trigger_user", return_value=make_user()),
            patch("codemie.triggers.bindings.cron.reindex_git_faq") as mock_reindex,
        ):
            result = binder._Cron__schedule_datasource_job(
                FAQ_TYPE, MagicMock(), "job-id", "ds-id", "user-1", "proj", "faq-ds", ""
            )

        assert result is scheduler.add_job.return_value
        scheduler.add_job.assert_called_once()
        assert scheduler.add_job.call_args.args[0] is mock_reindex
        assert isinstance(scheduler.add_job.call_args.kwargs["kwargs"]["payload"], GitFaqReindexTask)


class TestResumeClassification:
    def test_faq_is_unsupported_for_stale_resume(self):
        """The stale watchdog must skip FAQ instead of stuck-forever retry loops."""
        assert FAQ_TYPE in UNSUPPORTED_RESUME_TYPES


class TestReindexFaqActor:
    def test_actor_builds_processor_and_runs_reprocess(self):
        index = make_faq_index()
        payload = GitFaqReindexTask(
            project_name="proj",
            resource_id="job-id",
            resource_name="faq-ds",
            user=make_user(),
            index_info=index,
        )

        with (
            patch("codemie.triggers.actors.datasource.IndexInfo.stamp_reindex_triggered_at") as mock_stamp,
            patch("codemie.triggers.actors.datasource.GitFaqDatasourceProcessor") as mock_processor_cls,
            patch("codemie.triggers.actors.datasource.datasource_concurrency_manager") as mock_concurrency,
        ):
            instance = MagicMock()
            mock_processor_cls.return_value = instance
            reindex_git_faq.__wrapped__(payload)

        mock_stamp.assert_called_once_with(index.id)
        _, kwargs = mock_processor_cls.call_args
        assert kwargs["git_config"].repo_link == index.link
        assert kwargs["git_config"].branch == "main"
        # Regression check: the actor must forward files_filter, not silently drop it.
        assert kwargs["git_config"].files_filter == "docs/**/*.md"
        mock_concurrency.run.assert_called_once_with(instance.reprocess, instance.index)

    def test_actor_missing_link_logs_and_returns(self):
        index = make_faq_index()
        index.link = None
        payload = GitFaqReindexTask(
            project_name="proj",
            resource_id="job-id",
            resource_name="faq-ds",
            user=make_user(),
            index_info=index,
        )

        with (
            patch("codemie.triggers.actors.datasource.IndexInfo.stamp_reindex_triggered_at"),
            patch("codemie.triggers.actors.datasource.GitFaqDatasourceProcessor") as mock_processor_cls,
            patch("codemie.triggers.actors.datasource.datasource_concurrency_manager") as mock_concurrency,
            patch("codemie.triggers.actors.datasource.logger") as mock_logger,
        ):
            reindex_git_faq.__wrapped__(payload)

        mock_processor_cls.assert_not_called()
        mock_concurrency.run.assert_not_called()
        assert mock_logger.error.called


def test_faq_type_value():
    assert FullDatasourceTypes.GIT_FAQ.value == "knowledge_base_git_faq"
