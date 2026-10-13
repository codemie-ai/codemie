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

from fastapi import APIRouter, BackgroundTasks, status, Depends
from starlette.requests import Request

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    BaseResponse,
)
from codemie.datasource.confluence_datasource_processor import (
    IndexKnowledgeBaseConfluenceConfig,
    ConfluenceDatasourceProcessor,
)
from codemie.datasource.jira.jira_datasource_processor import JiraDatasourceProcessor
from codemie.datasource.xray.xray_datasource_processor import XrayDatasourceProcessor
from codemie.rest_api.models.index import (
    IndexKnowledgeBaseConfluenceRequest,
    IndexKnowledgeBaseJIRARequest,
    IndexKnowledgeBaseXrayRequest,
    KnowledgeBaseIndexInfo,
    UpdateKnowledgeBaseConfluenceRequest,
    UpdateKnowledgeBaseJiraRequest,
    UpdateKnowledgeBaseXrayRequest,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.settings.settings import SettingsService

from codemie.rest_api.routers.index._common import (
    INDEX_NOT_FOUND_MESSAGE,
    INDEX_NOT_FOUND_HELP,
    INVALID_INPUT_PARAMETERS_HELP,
    INCORRECT_DATASOURCE_SETUP_MESSAGE,
    EDIT_SUCCESSFUL,
    _update_datasource_scheduler,
    _index_unique_check,
    _kb_demo_user_check,
    _validate_project_change,
)


router = APIRouter()


@router.post(
    "/index/knowledge_base/confluence",
    status_code=status.HTTP_201_CREATED,
    response_model=BaseResponse,
)
def index_knowledge_base_confluence(
    request: IndexKnowledgeBaseConfluenceRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
):
    _index_unique_check(request.project_name, request.name)
    _kb_demo_user_check(raw_request.state.user)

    confluence_creds = SettingsService.get_confluence_creds(
        user_id=raw_request.state.user.id,
        project_name=request.project_name,
        setting_id=request.setting_id,
    )
    confluence_config = IndexKnowledgeBaseConfluenceConfig(
        cql=request.cql,
        include_restricted_content=request.include_restricted_content,
        include_archived_content=request.include_archived_content,
        include_attachments=request.include_attachments,
        include_comments=request.include_comments,
        keep_markdown_format=request.keep_markdown_format,
        keep_newlines=request.keep_newlines,
    )
    datasource_processor = ConfluenceDatasourceProcessor(
        confluence=confluence_creds,
        datasource_name=request.name,
        project_name=request.project_name,
        index_knowledge_base_config=confluence_config,
        description=request.description,
        project_space_visible=request.project_space_visible,
        user=raw_request.state.user,
        setting_id=request.setting_id,
        request_uuid=raw_request.state.uuid,
        embedding_model=request.embedding_model,
        guardrail_assignments=request.guardrail_assignments,
        cron_expression=request.cron_expression,
    )
    try:
        datasource_processor.check_confluence_query(cql=request.cql, confluence=confluence_creds)
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=INCORRECT_DATASOURCE_SETUP_MESSAGE,
            details=str(e),
            help=INVALID_INPUT_PARAMETERS_HELP,
        ) from e

    datasource_processor.schedule(background_tasks)
    return BaseResponse(message=f"Indexing of datasource {request.name} has been started in the background")


@router.post("/index/knowledge_base/jira", status_code=status.HTTP_200_OK)
def index_knowledge_base_jira(
    request: IndexKnowledgeBaseJIRARequest, raw_request: Request, background_tasks: BackgroundTasks
):
    _index_unique_check(request.project_name, request.name)
    _kb_demo_user_check(raw_request.state.user)

    jira_creds = SettingsService.get_jira_creds(
        user_id=raw_request.state.user.id,
        project_name=request.project_name,
        setting_id=request.setting_id,
    )

    datasource_processor = JiraDatasourceProcessor(
        datasource_name=request.name,
        user=raw_request.state.user,
        project_name=request.project_name,
        credentials=jira_creds,
        jql=request.jql,
        description=request.description,
        project_space_visible=request.project_space_visible,
        setting_id=request.setting_id,
        request_uuid=raw_request.state.uuid,
        embedding_model=request.embedding_model,
        guardrail_assignments=request.guardrail_assignments,
        cron_expression=request.cron_expression,
        custom_fields=request.custom_fields,
    )
    try:
        datasource_processor.check_jira_query(
            jql=request.jql, credentials=jira_creds, custom_fields=request.custom_fields
        )
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=INCORRECT_DATASOURCE_SETUP_MESSAGE,
            details=str(e),
            help=INVALID_INPUT_PARAMETERS_HELP,
        ) from e

    datasource_processor.schedule(background_tasks)
    return BaseResponse(message=f"Indexing of datasource {request.name} has been started in the background")


@router.post("/index/knowledge_base/xray", status_code=status.HTTP_200_OK)
def index_knowledge_base_xray(
    request: IndexKnowledgeBaseXrayRequest, raw_request: Request, background_tasks: BackgroundTasks
):
    _index_unique_check(request.project_name, request.name)
    _kb_demo_user_check(raw_request.state.user)

    xray_creds = SettingsService.get_xray_creds(
        user_id=raw_request.state.user.id,
        project_name=request.project_name,
        setting_id=request.setting_id,
    )

    datasource_processor = XrayDatasourceProcessor(
        datasource_name=request.name,
        user=raw_request.state.user,
        project_name=request.project_name,
        credentials=xray_creds,
        jql=request.jql,
        description=request.description,
        project_space_visible=request.project_space_visible,
        setting_id=request.setting_id,
        request_uuid=raw_request.state.uuid,
        embedding_model=request.embedding_model,
        guardrail_assignments=request.guardrail_assignments,
        cron_expression=request.cron_expression,
    )
    try:
        datasource_processor.check_xray_query(jql=request.jql, credentials=xray_creds)
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=INCORRECT_DATASOURCE_SETUP_MESSAGE,
            details=str(e),
            help=INVALID_INPUT_PARAMETERS_HELP,
        ) from e

    datasource_processor.schedule(background_tasks)
    return BaseResponse(message=f"Indexing of datasource {request.name} has been started in the background")


@router.put("/index/knowledge_base/confluence", status_code=status.HTTP_200_OK)
def update_knowledge_base_confluence(
    request: UpdateKnowledgeBaseConfluenceRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
    full_reindex: bool = False,
    resume_indexing: bool = False,
    user: User = Depends(authenticate),
):
    cron_expression_provided = 'cron_expression' in request.model_fields_set

    kb_index = KnowledgeBaseIndexInfo.filter_by_project_and_repo(
        project_name=request.project_name,
        repo_name=request.name,
    )

    if not kb_index:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=INDEX_NOT_FOUND_MESSAGE,
            details=f"The index with name '{request.name}' in project '{request.project_name}' could not be found.",
            help=INDEX_NOT_FOUND_HELP,
        )

    # Check if project change is requested
    if hasattr(request, 'new_project_name'):
        _validate_project_change(request.new_project_name, request.project_name, request.name, user)

    # Update the index
    if (
        request.description is not None
        or request.project_space_visible is not None
        or request.cql is not None
        or request.setting_id is not None
        or (hasattr(request, 'new_project_name') and request.new_project_name)
    ):
        kb_index[0].update_index(
            user=user,
            description=request.description,
            project_space_visible=request.project_space_visible,
            cql=request.cql,
            reset_error=False,
            setting_id=request.setting_id,
            project_name=request.new_project_name if hasattr(request, 'new_project_name') else None,
            guardrail_assignments=request.guardrail_assignments,
        )

    if full_reindex is not True and resume_indexing is not True:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, kb_index[0], request.cron_expression, timezone=request.timezone)
        return BaseResponse(message=EDIT_SUCCESSFUL)

    confluence_creds = SettingsService.get_confluence_creds(
        user_id=user.id,
        project_name=request.project_name,
        setting_id=request.setting_id or kb_index[0].setting_id,
    )

    datasource_processor = ConfluenceDatasourceProcessor(
        confluence=confluence_creds,
        datasource_name=request.name,
        project_name=request.project_name,
        user=user,
        index=kb_index[0],
        request_uuid=raw_request.state.uuid,
        cron_expression=request.cron_expression if cron_expression_provided else None,
    )
    if resume_indexing:
        logger.info(f"Resuming datasource indexing. Name={request.name}")
        datasource_processor.schedule(background_tasks, datasource_processor.resume)
        return BaseResponse(message=f"Indexing of datasource {request.name} has been resumed in the background")
    else:
        logger.info(f"Reindexing datasource. Name={request.name}")
        datasource_processor.schedule(background_tasks, datasource_processor.reprocess)
        return BaseResponse(message=f"Indexing of datasource {request.name} has been started in the background")


@router.put("/index/knowledge_base/jira", status_code=status.HTTP_200_OK)
def update_knowledge_base_jira(
    request: UpdateKnowledgeBaseJiraRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
    full_reindex: bool = False,
    incremental_reindex: bool = False,
    user: User = Depends(authenticate),
):
    cron_expression_provided = 'cron_expression' in request.model_fields_set

    kb_search_results = KnowledgeBaseIndexInfo.filter_by_project_and_repo(
        project_name=request.project_name,
        repo_name=request.name,
    )

    if not kb_search_results:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=INDEX_NOT_FOUND_MESSAGE,
            details=f"The index with name '{request.name}' in project '{request.project_name}' could not be found.",
            help=INDEX_NOT_FOUND_HELP,
        )

    kb_index = kb_search_results[0]

    # Check if project change is requested
    _validate_project_change(request.new_project_name, request.project_name, request.name, user)

    jira_creds = SettingsService.get_jira_creds(
        user_id=user.id,
        project_name=request.project_name,
        setting_id=request.setting_id or kb_index.setting_id,
    )

    # Validate as the create route does: an unknown or ambiguous field must not be accepted here
    # and then silently warn-skipped at index time.
    if request.custom_fields:
        try:
            JiraDatasourceProcessor.check_jira_query(
                jql=request.jql, credentials=jira_creds, custom_fields=request.custom_fields
            )
        except Exception as e:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message=INCORRECT_DATASOURCE_SETUP_MESSAGE,
                details=str(e),
                help=INVALID_INPUT_PARAMETERS_HELP,
            ) from e

    # Update the index
    kb_index.update_index(
        user=user,
        description=request.description,
        project_space_visible=request.project_space_visible,
        jql=request.jql,
        custom_fields=request.custom_fields,
        reset_error=False,
        setting_id=request.setting_id,
        project_name=request.new_project_name,
        guardrail_assignments=request.guardrail_assignments,
    )

    if not full_reindex and not incremental_reindex:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, kb_index, request.cron_expression, timezone=request.timezone)
        return BaseResponse(message=EDIT_SUCCESSFUL)

    project_space_visible = (
        request.project_space_visible if request.project_space_visible is not None else kb_index.project_space_visible
    )

    datasource_processor = JiraDatasourceProcessor(
        datasource_name=request.name,
        user=user,
        project_name=request.project_name,
        credentials=jira_creds,
        jql=request.jql,
        description=request.description,
        project_space_visible=project_space_visible,
        setting_id=request.setting_id,
        index_info=kb_index,
        request_uuid=raw_request.state.uuid,
        cron_expression=request.cron_expression if cron_expression_provided else None,
        custom_fields=request.custom_fields,
    )

    msg = f"of datasource {request.name} has been started in the background"
    if incremental_reindex:
        logger.info(f"Incremental reindexing of datasource. Name={request.name}")
        datasource_processor.schedule(background_tasks, datasource_processor.incremental_reindex)
        return BaseResponse(message=f"Incremental indexing {msg}")
    else:
        logger.info(f"Reindexing datasource. Name={request.name}")
        datasource_processor.schedule(background_tasks, datasource_processor.reprocess)
        return BaseResponse(message=f"Indexing {msg}")


@router.put("/index/knowledge_base/xray", status_code=status.HTTP_200_OK)
def update_knowledge_base_xray(
    request: UpdateKnowledgeBaseXrayRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
    full_reindex: bool = False,
    incremental_reindex: bool = False,
    user: User = Depends(authenticate),
):
    cron_expression_provided = 'cron_expression' in request.model_fields_set

    kb_search_results = KnowledgeBaseIndexInfo.filter_by_project_and_repo(
        project_name=request.project_name,
        repo_name=request.name,
    )

    if not kb_search_results:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=INDEX_NOT_FOUND_MESSAGE,
            details=f"The index with name '{request.name}' in project '{request.project_name}' could not be found.",
            help=INDEX_NOT_FOUND_HELP,
        )

    kb_index = kb_search_results[0]

    # Check if project change is requested
    _validate_project_change(request.new_project_name, request.project_name, request.name, user)

    # Update the index
    kb_index.update_index(
        user=user,
        description=request.description,
        project_space_visible=request.project_space_visible,
        jql=request.jql,
        reset_error=False,
        setting_id=request.setting_id,
        project_name=request.new_project_name,
        guardrail_assignments=request.guardrail_assignments,
    )

    if not full_reindex and not incremental_reindex:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, kb_index, request.cron_expression, timezone=request.timezone)
        return BaseResponse(message=EDIT_SUCCESSFUL)

    xray_creds = SettingsService.get_xray_creds(
        user_id=user.id,
        project_name=request.project_name,
        setting_id=request.setting_id or kb_index.setting_id,
    )
    project_space_visible = (
        request.project_space_visible if request.project_space_visible is not None else kb_index.project_space_visible
    )

    datasource_processor = XrayDatasourceProcessor(
        datasource_name=request.name,
        user=user,
        project_name=request.project_name,
        credentials=xray_creds,
        jql=request.jql,
        description=request.description,
        project_space_visible=project_space_visible,
        setting_id=request.setting_id,
        index_info=kb_index,
        request_uuid=raw_request.state.uuid,
        cron_expression=request.cron_expression if cron_expression_provided else None,
    )

    msg = f"of datasource {request.name} has been started in the background"
    if incremental_reindex:
        logger.info(f"Incremental reindexing of datasource. Name={request.name}")
        datasource_processor.schedule(background_tasks, datasource_processor.incremental_reindex)
        return BaseResponse(message=f"Incremental indexing {msg}")
    else:
        logger.info(f"Reindexing datasource. Name={request.name}")
        datasource_processor.schedule(background_tasks, datasource_processor.reprocess)
        return BaseResponse(message=f"Indexing {msg}")
