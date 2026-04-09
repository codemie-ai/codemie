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

import multiprocessing
import contextlib
import threading

from datetime import datetime
from typing import List, Optional, Any

from codemie.core.constants import CodeIndexType
from codemie.core.models import GitRepo
from codemie.configs import logger
from codemie.datasource.base_datasource_processor import BaseDatasourceProcessor
from codemie.datasource.datasources_config import CODE_CONFIG
from codemie.datasource.loader.git_loader import GitBatchLoader
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.service.settings.settings import SettingsService

process_semaphore = threading.Semaphore(CODE_CONFIG.max_subprocesses)


class CodeDatasourceProcessor(BaseDatasourceProcessor):
    loader: Optional[Any] = None

    def __init__(
        self,
        repo: GitRepo,
        user: User,
        index: Optional[IndexInfo] = None,
        request_uuid: Optional[str] = None,
        guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
    ):
        super().__init__(
            datasource_name=repo.name,
            index=index,
            user=user,
            request_uuid=request_uuid,
            guardrail_assignments=guardrail_assignments,
        )
        self.repo = repo

    @property
    def _index_name(self) -> str:
        return self.repo.get_identifier()

    @property
    def _processing_batch_size(self) -> int:
        return CODE_CONFIG.loader_batch_size

    @classmethod
    def create_processor(
        cls,
        git_repo: GitRepo,
        user: User | None = None,
        index: Optional[IndexInfo] = None,
        request_uuid: Optional[str] = None,
        guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
    ):
        if git_repo.index_type == CodeIndexType.CODE:
            return cls(
                repo=git_repo,
                user=user,
                index=index,
                request_uuid=request_uuid,
                guardrail_assignments=guardrail_assignments,
            )
        elif git_repo.index_type == CodeIndexType.SUMMARY:
            from codemie.datasource.code.code_summary_datasource_processor import CodeSummaryDatasourceProcessor

            return CodeSummaryDatasourceProcessor(
                repo=git_repo,
                user=user,
                index=index,
                request_uuid=request_uuid,
                guardrail_assignments=guardrail_assignments,
            )
        elif git_repo.index_type == CodeIndexType.CHUNK_SUMMARY:
            from codemie.datasource.code.code_summary_datasource_processor import CodeChunkSummaryDatasourceProcessor

            return CodeChunkSummaryDatasourceProcessor(
                repo=git_repo,
                user=user,
                index=index,
                request_uuid=request_uuid,
                guardrail_assignments=guardrail_assignments,
            )
        else:
            # Raise exception if unsupported CodeIndexType is passed to method.
            raise NotImplementedError

    def _on_process_start(self):
        self.repo.save()

    def _on_process_end(self):
        self.repo.last_indexed_commit = str(self.loader.repo.head.commit)
        self.repo.save()

    def _init_loader(self) -> GitBatchLoader:
        creds = SettingsService.get_git_creds(
            user_id=self.user.id,
            project_name=self.index.project_name,
            repo_link=self.repo.link,
            setting_id=self.index.setting_id,
        )
        return GitBatchLoader.create_loader(self.repo, creds, request_uuid=self.request_uuid)

    def _init_index(self):
        if not self.index:
            self.index = IndexInfo.create_from_repo(self.repo, self.user)
        else:
            # If user runs re-index, set index creation date as current date for tracking purpose
            self.index.date = datetime.now()

        self._assign_and_sync_guardrails()


def _run_in_subprocess(process_func, git_repo_name) -> None:
    acquired = process_semaphore.acquire(blocking=False)
    if not acquired:
        logger.info(
            f"Max concurrent multiprocessing processes reached ({CODE_CONFIG.max_subprocesses}).\n"
            f"Running synchronously for CodeDatasource {git_repo_name}"
        )
        process_func()
        return

    try:
        timeout = CODE_CONFIG.processing_timeout if CODE_CONFIG.processing_timeout != -1 else None
        with contextlib.suppress(RuntimeError):
            multiprocessing.set_start_method('fork', force=True)
        p = multiprocessing.Process(target=process_func)
        logger.info(f"Started background processing in multiprocessing for CodeDatasource {git_repo_name}")
        p.start()
        p.join(timeout)
        if p.is_alive():
            logger.info(f"CodeDatasource {git_repo_name} processing exceeded timeout of {timeout} seconds.")
            p.terminate()
    finally:
        process_semaphore.release()


def run_in_background(process_func, git_repo_name, background_tasks):
    from codemie.datasource.datasource_concurrency_manager import datasource_concurrency_manager

    def do_work():
        if CODE_CONFIG.enable_multiprocessing:
            _run_in_subprocess(process_func, git_repo_name)
        else:
            process_func()

    def task():
        # index_info=None: code processors create IndexInfo lazily, so queued DB status
        # is not surfaced for this type; throttling still applies.
        datasource_concurrency_manager.run(do_work)

    background_tasks.add_task(task)


def index_code_datasource_in_background(
    request_uuid,
    git_repo,
    user,
    background_tasks,
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
    cron_expression: Optional[str] = None,
):
    def process():
        datasource_processor = CodeDatasourceProcessor.create_processor(
            git_repo=git_repo,
            user=user,
            request_uuid=request_uuid,
            guardrail_assignments=guardrail_assignments,
        )
        datasource_processor.process()

        # Create scheduler if cron_expression was provided
        datasource_processor._create_or_update_scheduler(cron_expression)

    run_in_background(process, git_repo.name, background_tasks)


def update_code_datasource_in_background(
    request_uuid,
    git_repo,
    user,
    app_name,
    repo_name,
    background_tasks,
    resume_indexing: bool,
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
    cron_expression: Optional[str] = None,
):
    def process():
        index = IndexInfo.filter_by_project_and_repo(project_name=app_name, repo_name=repo_name)[0]
        datasource_processor = CodeDatasourceProcessor.create_processor(
            git_repo=git_repo,
            user=user,
            index=index,
            request_uuid=request_uuid,
            guardrail_assignments=guardrail_assignments,
        )
        if resume_indexing:
            datasource_processor.resume()
        else:
            datasource_processor.reprocess()

        # Update scheduler if cron_expression was provided in the update request
        datasource_processor._create_or_update_scheduler(cron_expression)

    run_in_background(process, git_repo.name, background_tasks)
