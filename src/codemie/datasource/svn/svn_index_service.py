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

from typing import TYPE_CHECKING, List, Optional

from fastapi import BackgroundTasks, status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.settings.settings import SettingsService

if TYPE_CHECKING:
    from codemie.core.models import SVNRepo
    from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
    from codemie.rest_api.models.index import IndexInfo
    from codemie.rest_api.security.user import User


class SVNIndexService:
    @staticmethod
    def validate_credentials(
        user_id: str,
        project_name: str,
        repo_link: str,
        setting_id: Optional[str],
    ) -> None:
        if not setting_id:
            return
        try:
            SettingsService.get_svn_creds(
                user_id=user_id,
                project_name=project_name,
                repo_link=repo_link,
                setting_id=setting_id,
            )
        except Exception as e:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="SVN Integration Error",
                details=f"Failed to validate SVN credentials: {str(e)}",
                help="Please check your SVN integration settings.",
            ) from e

    @staticmethod
    def reindex(
        svn_repo: "SVNRepo",
        index_info: "IndexInfo",
        user: "User",
        app_name: str,
        repo_name: str,
        request_uuid: str,
        background_tasks: BackgroundTasks,
        resume_indexing: bool,
        guardrail_assignments: Optional[List["GuardrailAssignmentItem"]] = None,
        cron_expression: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> None:
        from codemie.datasource.code.code_datasource_processor import run_in_background
        from codemie.datasource.svn.svn_datasource_processor import SVNDatasourceProcessor
        from codemie.rest_api.models.index import IndexInfo as _IndexInfo

        SVNIndexService.validate_credentials(
            user_id=user.id,
            project_name=index_info.project_name,
            repo_link=svn_repo.link,
            setting_id=svn_repo.setting_id,
        )

        def process():
            index = _IndexInfo.filter_by_project_and_repo(project_name=app_name, repo_name=repo_name)[0]
            processor = SVNDatasourceProcessor.create_processor(
                svn_repo=svn_repo,
                user=user,
                index=index,
                request_uuid=request_uuid,
                guardrail_assignments=guardrail_assignments,
            )
            if resume_indexing:
                processor.resume()
            else:
                processor.reprocess()
            processor._create_or_update_scheduler(cron_expression, timezone=timezone)

        run_in_background(process, svn_repo.name, background_tasks)
