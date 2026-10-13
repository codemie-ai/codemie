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
from codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor import (
    AzureDevOpsWikiDatasourceProcessor,
)
from codemie.datasource.azure_devops_work_item.azure_devops_work_item_datasource_processor import (
    AzureDevOpsWorkItemDatasourceProcessor,
)
from codemie.rest_api.models.index import (
    IndexKnowledgeBaseAzureDevOpsWikiRequest,
    IndexKnowledgeBaseAzureDevOpsWorkItemRequest,
    KnowledgeBaseIndexInfo,
    UpdateKnowledgeBaseAzureDevOpsWikiRequest,
    UpdateKnowledgeBaseAzureDevOpsWorkItemRequest,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.settings.settings import SettingsService

from codemie.rest_api.routers.index._common import (
    INDEX_NOT_FOUND_MESSAGE,
    INDEX_NOT_FOUND_HELP,
    EDIT_SUCCESSFUL,
    _update_datasource_scheduler,
    _index_unique_check,
    _kb_demo_user_check,
    _validate_project_change,
)


router = APIRouter()


@router.post("/index/knowledge_base/azure_devops_wiki", status_code=status.HTTP_200_OK)
def index_knowledge_base_azure_devops_wiki(
    request: IndexKnowledgeBaseAzureDevOpsWikiRequest, raw_request: Request, background_tasks: BackgroundTasks
):
    _index_unique_check(request.project_name, request.name)
    _kb_demo_user_check(raw_request.state.user)

    azure_devops_creds = SettingsService.get_azure_devops_creds(
        user_id=raw_request.state.user.id,
        project_name=request.project_name,
    )

    datasource_processor = AzureDevOpsWikiDatasourceProcessor(
        datasource_name=request.name,
        user=raw_request.state.user,
        project_name=request.project_name,
        credentials=azure_devops_creds,
        wiki_query=request.wiki_query,
        wiki_name=request.wiki_name,
        description=request.description,
        project_space_visible=request.project_space_visible,
        setting_id=request.setting_id,
        request_uuid=raw_request.state.uuid,
        embedding_model=request.embedding_model,
        guardrail_assignments=request.guardrail_assignments,
        cron_expression=request.cron_expression,
    )

    datasource_processor.schedule(background_tasks)
    return BaseResponse(message=f"Indexing of datasource {request.name} has been started in the background")


@router.put("/index/knowledge_base/azure_devops_wiki", status_code=status.HTTP_200_OK)
def update_knowledge_base_azure_devops_wiki(
    request: UpdateKnowledgeBaseAzureDevOpsWikiRequest,
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
        wiki_query=request.wiki_query,
        reset_error=False,
        setting_id=request.setting_id,
        project_name=request.new_project_name,
        guardrail_assignments=request.guardrail_assignments,
    )

    if not full_reindex and not incremental_reindex:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, kb_index, request.cron_expression, timezone=request.timezone)
        return BaseResponse(message=EDIT_SUCCESSFUL)

    azure_devops_creds = SettingsService.get_azure_devops_creds(
        user_id=user.id,
        project_name=request.project_name,
        setting_id=request.setting_id or kb_index.setting_id,
    )
    project_space_visible = (
        request.project_space_visible if request.project_space_visible is not None else kb_index.project_space_visible
    )

    datasource_processor = AzureDevOpsWikiDatasourceProcessor(
        datasource_name=request.name,
        user=user,
        project_name=request.project_name,
        credentials=azure_devops_creds,
        wiki_query=request.wiki_query,
        wiki_name=request.wiki_name,
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


@router.post("/index/knowledge_base/azure_devops_work_item", status_code=status.HTTP_200_OK)
def index_knowledge_base_azure_devops_work_item(
    request: IndexKnowledgeBaseAzureDevOpsWorkItemRequest, raw_request: Request, background_tasks: BackgroundTasks
):
    _index_unique_check(request.project_name, request.name)
    _kb_demo_user_check(raw_request.state.user)

    azure_devops_creds = SettingsService.get_azure_devops_creds(
        user_id=raw_request.state.user.id,
        project_name=request.project_name,
    )

    datasource_processor = AzureDevOpsWorkItemDatasourceProcessor(
        datasource_name=request.name,
        user=raw_request.state.user,
        project_name=request.project_name,
        credentials=azure_devops_creds,
        wiql_query=request.wiql_query,
        description=request.description,
        project_space_visible=request.project_space_visible,
        setting_id=request.setting_id,
        request_uuid=raw_request.state.uuid,
        embedding_model=request.embedding_model,
        guardrail_assignments=request.guardrail_assignments,
        cron_expression=request.cron_expression,
    )

    datasource_processor.schedule(background_tasks)
    return BaseResponse(message=f"Indexing of datasource {request.name} has been started in the background")


@router.put("/index/knowledge_base/azure_devops_work_item", status_code=status.HTTP_200_OK)
def update_knowledge_base_azure_devops_work_item(
    request: UpdateKnowledgeBaseAzureDevOpsWorkItemRequest,
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

    _validate_project_change(request.new_project_name, request.project_name, request.name, user)

    kb_index.update_index(
        user=user,
        description=request.description,
        project_space_visible=request.project_space_visible,
        wiql_query=request.wiql_query,
        reset_error=False,
        setting_id=request.setting_id,
        project_name=request.new_project_name,
        guardrail_assignments=request.guardrail_assignments,
    )

    if not full_reindex and not incremental_reindex:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, kb_index, request.cron_expression, timezone=request.timezone)
        return BaseResponse(message=EDIT_SUCCESSFUL)

    azure_devops_creds = SettingsService.get_azure_devops_creds(
        user_id=user.id,
        project_name=request.project_name,
        setting_id=request.setting_id or kb_index.setting_id,
    )
    project_space_visible = (
        request.project_space_visible if request.project_space_visible is not None else kb_index.project_space_visible
    )

    datasource_processor = AzureDevOpsWorkItemDatasourceProcessor(
        datasource_name=request.name,
        user=user,
        project_name=request.project_name,
        credentials=azure_devops_creds,
        wiql_query=request.wiql_query
        or (kb_index.azure_devops_work_item.wiql_query if kb_index.azure_devops_work_item else None),
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
