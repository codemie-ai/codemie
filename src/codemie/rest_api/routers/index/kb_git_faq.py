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

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, status, Depends
from starlette.requests import Request

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    BaseResponse,
)
from codemie.datasource.git_faq.git_faq_datasource_processor import GitFaqConfig, GitFaqDatasourceProcessor
from codemie.rest_api.models.index import (
    IndexInfo,
    KnowledgeBaseIndexInfo,
    IndexKnowledgeBaseGitFaqRequest,
    UpdateKnowledgeBaseGitFaqRequest,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

from codemie.rest_api.routers.index._common import (
    INDEX_NOT_FOUND_MESSAGE,
    INDEX_NOT_FOUND_HELP,
    EDIT_SUCCESSFUL,
    _update_datasource_scheduler,
    _index_unique_check,
    _kb_demo_user_check,
    _validate_git_credentials,
    _validate_project_change,
)


router = APIRouter()


@router.post("/index/knowledge_base/git_faq", status_code=status.HTTP_200_OK)
def index_knowledge_base_faq(
    request: IndexKnowledgeBaseGitFaqRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
) -> BaseResponse:
    _index_unique_check(request.project_name, request.name)
    _kb_demo_user_check(raw_request.state.user)

    user = raw_request.state.user

    _validate_git_credentials(
        user_id=user.id,
        project_name=request.project_name,
        repo_link=request.link,
        setting_id=request.setting_id,
    )

    datasource_processor = GitFaqDatasourceProcessor(
        datasource_name=request.name,
        user=user,
        project_name=request.project_name,
        git_config=GitFaqConfig(
            repo_link=request.link,
            branch=request.branch,
            files_filter=request.files_filter,
            setting_id=request.setting_id,
        ),
        description=request.description,
        project_space_visible=bool(request.project_space_visible),
        request_uuid=raw_request.state.uuid,
        embedding_model=request.embedding_model,
        guardrail_assignments=request.guardrail_assignments,
        cron_expression=request.cron_expression,
    )

    datasource_processor.schedule(background_tasks)
    return BaseResponse(message=f"Indexing of datasource {request.name} has been started in the background")


_GIT_FAQ_SOURCE_FIELDS = ("link", "branch", "files_filter", "setting_id")


def _build_git_faq_processor_from_index(
    index_info: IndexInfo,
    user: User,
    request_uuid: str,
    cron_expression: Optional[str] = None,
) -> GitFaqDatasourceProcessor:
    return GitFaqDatasourceProcessor(
        datasource_name=index_info.repo_name,
        user=user,
        project_name=index_info.project_name,
        git_config=GitFaqConfig(
            repo_link=index_info.link,
            branch=index_info.branch or "main",
            files_filter=index_info.files_filter,
            setting_id=index_info.setting_id,
        ),
        embedding_model=index_info.embeddings_model,
        cron_expression=cron_expression,
        index_info=index_info,
        request_uuid=request_uuid,
    )


def _git_faq_update_params(request) -> dict:
    """Build the update_params dict for the Git FAQ update route."""
    params = {}
    if request.description is not None:
        params["description"] = request.description
    if request.project_space_visible is not None:
        params["project_space_visible"] = request.project_space_visible
    if request.new_project_name:
        params["project_name"] = request.new_project_name
    if request.guardrail_assignments is not None:
        params["guardrail_assignments"] = request.guardrail_assignments
    if request.link is not None:
        params["link"] = request.link
    if request.branch is not None:
        params["branch"] = request.branch
    if request.files_filter is not None:
        params["files_filter"] = request.files_filter
    if request.setting_id is not None:
        # An empty string means "integration cleared in the UI" — store no integration.
        params["setting_id"] = request.setting_id or None
    return params


@router.put("/index/knowledge_base/git_faq", status_code=status.HTTP_200_OK)
def update_knowledge_base_faq(
    request: UpdateKnowledgeBaseGitFaqRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
    full_reindex: bool = False,
    user: User = Depends(authenticate),
) -> BaseResponse:
    cron_expression_provided = 'cron_expression' in request.model_fields_set

    kb_index = KnowledgeBaseIndexInfo.filter_by_project_and_repo(
        project_name=request.project_name, repo_name=request.name
    )

    if not kb_index:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=INDEX_NOT_FOUND_MESSAGE,
            details=f"The index with name '{request.name}' in project '{request.project_name}' could not be found.",
            help=INDEX_NOT_FOUND_HELP,
        )

    # Check if project change is requested
    _validate_project_change(request.new_project_name, request.project_name, request.name, user)

    provided_source_fields = [field for field in _GIT_FAQ_SOURCE_FIELDS if getattr(request, field) is not None]
    if request.new_project_name:
        provided_source_fields.append("new_project_name")
    if provided_source_fields and not full_reindex:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="FAQ source fields require a full reindex",
            details=(
                f"Fields {provided_source_fields} change where FAQ articles are read from or where they live. "
                "Pass full_reindex=true to apply them."
            ),
            help="Re-send the request with full_reindex=true to update the FAQ source configuration.",
        )

    # Update the index - only update the fields that are provided
    update_params = _git_faq_update_params(request)

    if update_params:
        kb_index[0].update_index(
            user=user,
            **update_params,
        )

    if full_reindex:
        logger.info(f"Reindexing FAQ knowledge base {kb_index[0]}")

        index_info = kb_index[0]
        if index_info.setting_id:
            _validate_git_credentials(
                user_id=user.id,
                project_name=index_info.project_name,
                repo_link=index_info.link,
                setting_id=index_info.setting_id,
            )

        cron_expression = request.cron_expression if cron_expression_provided else None
        datasource_processor = _build_git_faq_processor_from_index(
            index_info, user, raw_request.state.uuid, cron_expression=cron_expression
        )
        datasource_processor.schedule(background_tasks, datasource_processor.reprocess)
        return BaseResponse(message=f"Indexing of datasource {index_info.repo_name} has been started in the background")

    else:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, kb_index[0], request.cron_expression, timezone=request.timezone)
        return BaseResponse(message=EDIT_SUCCESSFUL)


@router.put("/index/knowledge_base/git_faq/reindex", status_code=status.HTTP_200_OK)
def reindex_knowledge_base_faq(
    request: UpdateKnowledgeBaseGitFaqRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(authenticate),
) -> BaseResponse:
    kb_index = KnowledgeBaseIndexInfo.filter_by_project_and_repo(
        project_name=request.project_name, repo_name=request.name
    )
    if not kb_index:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=INDEX_NOT_FOUND_MESSAGE,
            details=f"The index with name '{request.name}' in project '{request.project_name}' could not be found.",
            help=INDEX_NOT_FOUND_HELP,
        )

    index_info = kb_index[0]
    if index_info.setting_id:
        _validate_git_credentials(
            user_id=user.id,
            project_name=index_info.project_name,
            repo_link=index_info.link,
            setting_id=index_info.setting_id,
        )

    datasource_processor = _build_git_faq_processor_from_index(index_info, user, raw_request.state.uuid)
    datasource_processor.schedule(background_tasks, datasource_processor.reprocess)
    return BaseResponse(message=f"Indexing of datasource {index_info.repo_name} has been started in the background")
