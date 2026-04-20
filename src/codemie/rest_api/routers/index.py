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

import base64
import json
import io
import time
from typing import List, Optional

from elasticsearch.exceptions import NotFoundError
from fastapi import APIRouter, BackgroundTasks, status, Depends
from starlette.requests import Request
from starlette.responses import StreamingResponse

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import Application, BaseResponse, GitRepo, BaseGitRepo
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem, GuardrailEntity
from codemie.rest_api.models.index import IndexKnowledgeBaseFileTypes
from codemie.datasource.code.code_datasource_processor import (
    index_code_datasource_in_background,
    update_code_datasource_in_background,
)
from codemie.datasource.file.file_datasource_processor import FileDatasourceProcessor, FILE_PATH_DATA_NT
from codemie.datasource.confluence_datasource_processor import (
    IndexKnowledgeBaseConfluenceConfig,
    ConfluenceDatasourceProcessor,
)
from codemie.datasource.datasources_config import STORAGE_CONFIG
from codemie.datasource.loader.git_loader import GitBatchLoader
from codemie.service.aws_bedrock.bedrock_knowledge_base_service import BedrockKnowledgeBaseService
from codemie.service.provider.datasource import (
    ProviderDatasourceCreationService,
    ProviderDatasourceUpdateService,
    ProviderDatasourceDeletionService,
    ProviderDatasourceReindexService,
    PROVIDER_INDEX_TYPE,
)
from codemie.datasource.jira.jira_datasource_processor import JiraDatasourceProcessor
from codemie.datasource.xray.xray_datasource_processor import XrayDatasourceProcessor
from codemie.datasource.google_doc.google_doc_datasource_processor import GoogleDocDatasourceProcessor
from codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor import (
    AzureDevOpsWikiDatasourceProcessor,
)
from codemie.datasource.azure_devops_work_item.azure_devops_work_item_datasource_processor import (
    AzureDevOpsWorkItemDatasourceProcessor,
)
from codemie.datasource.sharepoint.sharepoint_datasource_processor import (
    SharePointDatasourceProcessor,
    SharePointProcessorConfig,
)
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.rest_api.models.assistant import Assistant, AssistantListResponse
from codemie.rest_api.models.index import (
    CronExpressionValidatorMixin,
    DatasourceHealthCheckRequest,
    IndexInfo,
    CodeIndexInfo,
    IndexKnowledgeBaseConfluenceRequest,
    IndexKnowledgeBaseJIRARequest,
    IndexKnowledgeBaseXrayRequest,
    IndexKnowledgeBaseAzureDevOpsWikiRequest,
    IndexKnowledgeBaseAzureDevOpsWorkItemRequest,
    IndexKnowledgeBaseSharePointRequest,
    UpdateKnowledgeBaseSharePointRequest,
    IndexKnowledgeBaseFileRequest,
    UpdateIndexRequest,
    KnowledgeBaseIndexInfo,
    IndexKnowledgeBaseGoogleRequest,
    ReIndexKnowledgeBaseRequest,
    UpdateKnowledgeBaseGoogleRequest,
    ElasticsearchStatsResponse,
    UpdateKnowledgeBaseFilesRequest,
    UpdateKnowledgeBaseConfluenceRequest,
    UpdateKnowledgeBaseJiraRequest,
    UpdateKnowledgeBaseXrayRequest,
    UpdateKnowledgeBaseAzureDevOpsWikiRequest,
    UpdateKnowledgeBaseAzureDevOpsWorkItemRequest,
    GetIndexInfoIDResponse,
    SortOrder,
    SortKey,
)
from codemie.rest_api.models.provider import Provider
from codemie.rest_api.models.tool import (
    ToolInvokeResponse,
    DatasourceSearchInvokeRequest,
)
from codemie.rest_api.security.authentication import authenticate, application_access_check
from codemie.rest_api.security.user import User
from codemie.rest_api.utils.default_applications import ensure_application_exists
from codemie.rest_api.routers.utils import (
    raise_access_denied,
    raise_forbidden,
    raise_unprocessable_entity,
    raise_not_found,
)
from codemie.service.index.index_service import IndexStatusService
from codemie.service.index.datasource_health_check_service import IndexHealthCheckService
from codemie.service.monitoring.agent_monitoring_service import AgentMonitoringService
from codemie.service.guardrail.guardrail_service import GuardrailService
from codemie.service.request_summary_manager import request_summary_manager
from codemie.rest_api.models.settings import SharePointCredentials
from codemie.service.settings.settings import SettingsService, Settings
from codemie.service.tools.tool_execution_service import ToolExecutionService
from codemie.service.index.index_encrypted_settings_service import (
    IndexEncryptedSettingsService,
    IndexEncryptedSettingsError,
)


def _parse_jwt_exp(token: str) -> int:
    """Decode a JWT payload without signature verification and return the exp claim.

    Falls back to one hour from now if the token cannot be decoded or has no exp claim.
    """
    try:
        payload_b64 = token.split(".")[1]
        # Add padding so base64 doesn't complain about incorrect lengths.
        payload = json.loads(base64.b64decode(payload_b64 + "=="))
        return int(payload["exp"])
    except Exception:
        return int(time.time()) + 3600


# Error message constants
CHECK_PERMISSIONS_MESSAGE = "Please check your user permissions or contact an administrator for assistance."
CHECK_USER_PERMISSIONS_HELP = "Please check your user permissions."
ACCESS_DENIED_MESSAGE = "Access denied"
INDEX_NOT_FOUND_MESSAGE = "Index not found"
INDEX_NOT_FOUND_HELP = (
    "Please verify the index name and project name. If you believe this index should exist, "
    "check your project configuration or contact support."
)
APPLICATION_NOT_FOUND_MESSAGE = "Application not found"
APPLICATION_NOT_FOUND_HELP = (
    "Please verify the application name and ensure it exists. If you believe this is an error, contact support."
)
INVALID_INPUT_PARAMETERS_HELP = "Ensure that the submitted parameters are correct"
INCORRECT_DATASOURCE_SETUP_MESSAGE = "Datasource setup is incorrect"
EDIT_SUCCESSFUL = "Edit successful"

# Project change related constants
INDEX_EXISTS_MESSAGE = "Index already exists"
INDEX_EXISTS_HELP = "Please choose a different name for your index or use the existing index."
PROJECT_CHANGE_LOG_MESSAGE = "Project association changed for index"


router = APIRouter(tags=["Indexing"], prefix="/v1", dependencies=[Depends(authenticate)])


@router.get(
    "/index",
    status_code=status.HTTP_200_OK,
)
def index_indexes_progress(
    _request: Request,
    user: User = Depends(authenticate),
    filters: Optional[str] = None,
    sort_key: Optional[SortKey] = SortKey.UPDATE_DATE,
    sort_order: Optional[SortOrder] = SortOrder.DESC,
    page: int = 0,
    per_page: int = 10,
    full_response: bool = False,
):
    try:
        parsed_filters = json.loads(filters) if filters else None
    except json.JSONDecodeError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid filters",
            details="Filters must be a valid encoded JSON object.",
            help="Please check the filters and ensure they are in the correct format. ",
        )
    return IndexStatusService.get_index_info_list(
        user=user,
        filters=parsed_filters,
        per_page=per_page,
        page=page,
        sort_key=sort_key,
        sort_order=sort_order,
        full_response=full_response,
    )


@router.get(
    "/index/users",
    status_code=status.HTTP_200_OK,
)
def get_index_users(user: User = Depends(authenticate)):
    return IndexStatusService.get_users(user)


@router.get(
    "/index/find_id",
    status_code=status.HTTP_200_OK,
)
def get_index_info_id(
    name: str, index_type: str, project_name: str | None = None, user: User = Depends(authenticate)
) -> GetIndexInfoIDResponse:
    """Find IndexInfo ID by name and type, optionally scoped to a project"""
    index = IndexInfo.find_by_name_and_type(name=name, index_type=index_type, project_name=project_name)

    if not index:
        raise_not_found(resource_type="Datasource", resource_id=f"{name}/{index_type}")

    if not Ability(user).can(Action.READ, index):
        raise_forbidden("read")

    return GetIndexInfoIDResponse(id=index.id)


@router.get(
    "/index/{index_id}",
    status_code=status.HTTP_200_OK,
)
def get_index_info(request: Request, index_id: str, user: User = Depends(authenticate)):
    index = _get_index_by_id_or_raise(index_id)

    _validate_remote_entities_and_raise(index)

    if not Ability(user).can(Action.READ, index):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Access is denied",
            details=f"You don't have permission to get the index with ID '{index_id}'.",
            help=CHECK_USER_PERMISSIONS_HELP,
        )
    index.user_abilities = Ability(user).list(index)
    index.processed_files.sort(key=lambda x: x.lower())

    if len(index.processed_files) > STORAGE_CONFIG.processed_documents_threshold:
        index.processed_files = index.processed_files[: STORAGE_CONFIG.processed_documents_threshold - 1]
        index.processed_files.append("and more files...")

    if index.processing_info and index.processing_info.get(GitBatchLoader.FILTERED_DOCUMENTS_KEY):
        index.processing_info[GitBatchLoader.FILTERED_DOCUMENTS_KEY] = None

    # Enrich with guardrail assignments
    index.guardrail_assignments = GuardrailService.get_entity_guardrail_assignments(
        user,
        GuardrailEntity.KNOWLEDGEBASE,
        str(index.id),
    )

    # Enrich with cron_expression and return as dict
    return IndexStatusService.enrich_index_with_schedule(index, user)


@router.post(
    "/index/{datasource_id}/search",
    status_code=status.HTTP_200_OK,
    response_model=ToolInvokeResponse,
)
def invoke_datasource_search(
    request: DatasourceSearchInvokeRequest, datasource_id: str, user: User = Depends(authenticate)
):
    kb_search_result = _get_index_by_id_or_raise(datasource_id)

    if not Ability(user).can(Action.READ, kb_search_result):
        raise_access_denied("search")

    try:
        output = ToolExecutionService.invoke_datasource_search(kb_search_result, request)
        return ToolInvokeResponse(output=str(output))
    except Exception as e:
        return ToolInvokeResponse(error=f"An error occurred while search over datasource: {str(e)}")


@router.get(
    "/index/{index_id}/export",
    status_code=status.HTTP_200_OK,
)
def get_index_info_export(index_id: str, user: User = Depends(authenticate)):
    index = IndexInfo.get_by_id(index_id)

    if not Ability(user).can(Action.READ, index):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Access is denied",
            details=f"You don't have permission to get the index with ID '{index_id}'.",
            help=CHECK_USER_PERMISSIONS_HELP,
        )

    # Create the content to be returned
    content = IndexStatusService.get_index_status_markdown(index)

    # Create a BytesIO stream
    file_stream = io.BytesIO(content.encode('utf-8'))

    # Return the file content as a streaming response
    return StreamingResponse(
        file_stream, media_type='text/markdown', headers={"Content-Disposition": f"attachment; filename={index_id}.md"}
    )


@router.get("/index/{index_id}/settings", status_code=status.HTTP_200_OK, response_model=Settings)
def get_shared_encrypted_sessings(index_id: str, x_request_id: str, user: User = Depends(authenticate)):
    """Get encrypted settings to be used in an external application"""
    try:
        index = IndexInfo.get_by_id(index_id)
    except Exception:
        raise_not_found(resource_type="Datasource", resource_id=index_id)

    if not Ability(user).can(Action.READ, index, check_resource_permissions=True):
        raise_forbidden("get encrypted settings for")

    try:
        return IndexEncryptedSettingsService(index=index, user=user, x_request_id=x_request_id).run()
    except IndexEncryptedSettingsError as e:
        raise raise_unprocessable_entity("get encrypted settings", "index", e)


@router.get(
    "/knowledge_bases",
    status_code=status.HTTP_200_OK,
    response_model=list[IndexInfo],
)
def index_knowledge_bases(request: Request):
    """
    Get all knowledge bases
    """
    if request.state.user.is_admin_or_maintainer:
        return KnowledgeBaseIndexInfo.get_all()

    return KnowledgeBaseIndexInfo.filter_by_names_or_user(
        project_names=request.state.user.project_names, user=request.state.user
    )


@router.get(
    "/index/{index_id}/assistants",
    status_code=status.HTTP_200_OK,
    response_model=List[AssistantListResponse],
)
def get_assistants_using_index(index_id: str, request: Request):
    index = IndexInfo.get_by_id(index_id)
    return Assistant.by_datasource_run(datasource=index)


@router.get(
    "/index/{index_id}/elasticsearch",
    status_code=status.HTTP_200_OK,
    response_model=ElasticsearchStatsResponse,
)
def get_index_elasticsearch_stats(index_id: str, user: User = Depends(authenticate)) -> ElasticsearchStatsResponse:
    """
    Get Elasticsearch statistics for a specific datasource index.

    Returns:
        ElasticsearchStatsResponse with Elasticsearch statistics including:
        - index_name: Name of the index in Elasticsearch
        - size_in_bytes: Size of the index in bytes
    """
    index = _get_index_by_id_or_raise(index_id)

    if not Ability(user).can(Action.READ, index):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ACCESS_DENIED_MESSAGE,
            details=f"You don't have permission to get Elasticsearch statistics for index with ID '{index_id}'.",
            help=CHECK_USER_PERMISSIONS_HELP,
        )

    if index.index_type.startswith("platform"):
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Platform datasources are not supported",
            details=f"Elasticsearch statistics are not available for platform datasources (type: {index.index_type}). "
            f"This feature is only supported for regular datasources.",
            help="Platform datasources use a different indexing mechanism that does not provide Elasticsearch "
            "statistics.",
        )

    stats = _get_elasticsearch_stats(index)

    if stats is None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Elasticsearch statistics not available",
            details=f"Unable to retrieve Elasticsearch statistics for index '{index_id}'. "
            f"The index may not exist in Elasticsearch yet.",
            help="Ensure the datasource has been indexed successfully. "
            "If the datasource was recently created, wait for indexing to complete.",
        )

    return stats


@router.delete(
    "/index/{index_id}",
    status_code=status.HTTP_200_OK,
)
def delete_index(request: Request, index_id: str, user: User = Depends(authenticate)):
    index = IndexInfo.get_by_id(index_id)

    if not Ability(user).can(Action.DELETE, index):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ACCESS_DENIED_MESSAGE,
            details=f"You don't have permission to delete the index with ID '{index_id}'.",
            help=CHECK_PERMISSIONS_MESSAGE,
        )

    try:
        if index.index_type == PROVIDER_INDEX_TYPE:
            ProviderDatasourceDeletionService(datasource=index, user=user).run()
        else:
            index.delete()

        GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.KNOWLEDGEBASE, str(index.id))

        AgentMonitoringService.send_count_metric(
            name="delete_datasource",
            attributes={
                "datasource_type": index.index_type,
                "project": index.project_name,
                "repo_name": index.repo_name,
                "user_name": user.username,
                "user_id": user.id,
            },
        )

        return BaseResponse(message=f"'Index {index.repo_name} was deleted successfully")
    except NotFoundError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=INDEX_NOT_FOUND_MESSAGE,
            details=f"Index {index.repo_name} could not be found in the system.",
            help=INDEX_NOT_FOUND_HELP,
        ) from e


class CreateIndexRequest(CronExpressionValidatorMixin, BaseGitRepo):
    """
    Request model for creating a new code index with optional guardrail assignments.

    Solves circular import issue within the BaseGitRepo file.
    """

    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None


@router.post(
    "/application/{app_name}/index",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(application_access_check)],
    response_model=BaseResponse,
)
def create_index_application(
    app_name: str,
    create_git_repo_request: CreateIndexRequest,
    request: Request,
    tasks: BackgroundTasks,
    user: User = Depends(authenticate),
):
    request_uuid = request.state.uuid
    request_summary_manager.create_request_summary(
        request_id=request_uuid,
        project_name=app_name,
        user=user.as_user_model(),
    )
    cron_expression_provided = 'cron_expression' in create_git_repo_request.model_fields_set

    if request.state.user.is_demo_user:
        existing_repos = CodeIndexInfo.get_by_user_id(user_id=request.state.user.id)

        if len(existing_repos) >= 1:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Repository limit reached",
                details="Demo users are allowed to index only one repository. You have already reached this limit.",
                help="To index additional repositories, please upgrade your account or "
                "remove your existing indexed repository.",
            )

    _index_unique_check(app_name, create_git_repo_request.name)

    # Ensure Application exists for the project (auto-create if needed)
    ensure_application_exists(app_name)

    # Get the application (now guaranteed to exist)
    application = Application.get_by_id(app_name)

    _validate_git_credentials(
        user_id=user.id,
        project_name=app_name,
        repo_link=create_git_repo_request.link,
        setting_id=create_git_repo_request.setting_id,
    )

    application.git_repos = [
        GitRepo(**create_git_repo_request.model_dump(exclude={"guardrail_assignments"}), app_id=application.name)
    ]

    index_code_datasource_in_background(
        request_uuid=request_uuid,
        git_repo=application.git_repos[0],
        user=request.state.user,
        background_tasks=tasks,
        guardrail_assignments=create_git_repo_request.guardrail_assignments,
        cron_expression=create_git_repo_request.cron_expression if cron_expression_provided else None,
    )

    return BaseResponse(
        message=f"Indexing of datasource {create_git_repo_request.name} has been started in the background"
    )


def _handle_index_and_repository_update(
    index_info: IndexInfo,
    request: UpdateIndexRequest,
    user: User,
    app_name: str,
    repo_name: str,
    repository: GitRepo,
) -> None:
    """Handle index information update, metrics, and repository field updates."""
    _validate_project_change(request.new_project_name, app_name, repo_name, user)

    index_info.update_index(
        user=user,
        description=request.description,
        prompt=request.prompt,
        project_space_visible=request.projectSpaceVisible,
        docs_generation=request.docsGeneration,
        embeddings_model=request.embeddingsModel,
        files_filter=request.filesFilter,
        branch=request.branch,
        link=request.link,
        reset_error=False,
        setting_id=request.setting_id,
        project_name=request.new_project_name,
        guardrail_assignments=request.guardrail_assignments,
    )
    AgentMonitoringService.send_count_metric(
        name="update_datasource",
        attributes={
            "datasource_type": index_info.index_type,
            "project": index_info.project_name,
            "repo_name": index_info.repo_name,
            "user_name": user.username,
            "user_id": user.id,
        },
    )

    repository.branch = request.branch
    repository.files_filter = request.filesFilter
    repository.link = request.link.strip() if request.link else request.link
    repository.setting_id = request.setting_id
    repository.docs_generation = request.docsGeneration
    repository.embeddings_model = request.embeddingsModel
    repository.project_space_visible = request.projectSpaceVisible
    repository.update()


def _get_repository_with_project_change(
    application: Application,
    repo_name: str,
    index_info: IndexInfo,
    request: UpdateIndexRequest,
) -> GitRepo:
    """Get repository and handle project change if needed."""
    app_repositories = GitRepo.get_by_app_id(application.name)
    repository = next((repo for repo in app_repositories if repo.name == repo_name), None)

    if repository and request.new_project_name and repository.app_id != request.new_project_name:
        try:
            new_repo = GitRepo(
                **repository.model_dump(exclude={"app_id", "id", "original_storage"}),
                app_id=request.new_project_name,
            )
            new_repo.id = new_repo.get_identifier()
            new_repo.original_storage = repository.get_identifier()
            new_repo.save()
            repository = new_repo
        except Exception:
            logger.error(f"Repository already exist for new project name {request.new_project_name}")

    if not repository:
        all_repos_with_name = GitRepo.get_all_by_fields({"name": repo_name})
        repository = next(
            (r for r in all_repos_with_name if r.link == index_info.link),
            None,
        )

    if not repository:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Repository not found",
            details=f"The repository with name '{repo_name}' could not be found "
            "in the list of application repositories.",
            help="Please verify the repository name and ensure it exists within the application."
            " If you believe this is an error, check the application configuration or contact support.",
        )

    return repository


@router.put(
    "/application/{app_name}/index/{repo_name}",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(application_access_check)],
    response_model=BaseResponse,
)
def update_index_application(
    app_name: str,
    repo_name: str,
    tasks: BackgroundTasks,
    request: UpdateIndexRequest,
    raw_request: Request,
    full_reindex: bool = False,
    skip_reindex: bool = False,
    resume_indexing: bool = False,
    user: User = Depends(authenticate),
):
    """
    Endpoint for updating index for application
    By default it will be incremental update (only new commits will be indexed)
    If full_reindex is set to True, then all repository will be reindexed
    """
    request_uuid = raw_request.state.uuid
    request_summary_manager.create_request_summary(
        request_id=request_uuid,
        project_name=app_name,
        user=user.as_user_model(),
    )
    cron_expression_provided = 'cron_expression' in request.model_fields_set

    index_info = IndexInfo.filter_by_project_and_repo(project_name=app_name, repo_name=repo_name)[0]

    if not Ability(user).can(Action.WRITE, index_info):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ACCESS_DENIED_MESSAGE,
            details="You don't have permission to update the index.",
            help=CHECK_PERMISSIONS_MESSAGE,
        )

    try:
        application = Application.get_by_id(app_name)
    except (NotFoundError, KeyError) as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=APPLICATION_NOT_FOUND_MESSAGE,
            details=f"The application with name '{app_name}' could not be found in the system.",
            help=APPLICATION_NOT_FOUND_HELP,
        ) from e

    repository = _get_repository_with_project_change(application, repo_name, index_info, request)

    if request.name:
        _handle_index_and_repository_update(index_info, request, user, app_name, repo_name, repository)

    _validate_git_credentials(
        user_id=user.id,
        project_name=index_info.project_name,
        repo_link=repository.link,
        setting_id=repository.setting_id,
    )

    if skip_reindex:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, index_info, request.cron_expression)
        return BaseResponse(message=f"{repo_name} updated successfully!")

    update_code_datasource_in_background(
        request_uuid=request_uuid,
        git_repo=repository,
        user=user,
        app_name=app_name,
        repo_name=repo_name,
        background_tasks=tasks,
        resume_indexing=resume_indexing,
        guardrail_assignments=request.guardrail_assignments,
        cron_expression=request.cron_expression if cron_expression_provided else None,
    )

    if resume_indexing:
        return BaseResponse(message=f"Indexing of datasource {repo_name} has been resumed in the background")

    reindex_type = "Full" if full_reindex else "Incremental"
    return BaseResponse(
        message=f"{reindex_type} reindexing of datasource {request.name} has been started in the background"
    )


@router.get(
    "/application/{app_name}",
    status_code=status.HTTP_200_OK,
    response_model=Application,
    dependencies=[Depends(application_access_check)],
)
def get_application(app_name: str, request: Request):
    try:
        application = Application.get_by_id(app_name)
    except (NotFoundError, KeyError) as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=APPLICATION_NOT_FOUND_MESSAGE,
            details=f"The application with name '{app_name}' could not be found in the system.",
            help=APPLICATION_NOT_FOUND_HELP,
        ) from e

    if request.state.user.is_admin:
        index_info = CodeIndexInfo.get_all()
    else:
        index_info = CodeIndexInfo.filter_by_names_or_user(
            project_names=request.state.user.project_names, user=request.state.user
        )

    index_info_repos = [info.repo_name for info in index_info]
    git_repos = GitRepo.get_by_app_id(app_id=application.name)

    git_repos = [repo for repo in git_repos if repo.name in index_info_repos]

    application.git_repos = git_repos

    return application


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
    )
    try:
        datasource_processor.check_jira_query(jql=request.jql, credentials=jira_creds)
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


@router.post("/index/knowledge_base/sharepoint", status_code=status.HTTP_201_CREATED, response_model=BaseResponse)
def index_knowledge_base_sharepoint(
    request: IndexKnowledgeBaseSharePointRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
) -> BaseResponse:
    user = raw_request.state.user
    _index_unique_check(request.project_name, request.name)
    _kb_demo_user_check(user)

    auth_type = request.auth_type or "integration"
    if auth_type in ("oauth_codemie", "oauth_custom"):
        if not request.access_token:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message=INCORRECT_DATASOURCE_SETUP_MESSAGE,
                details="access_token is required when auth_type is 'oauth_codemie' or 'oauth_custom'",
                help=INVALID_INPUT_PARAMETERS_HELP,
            )
        sharepoint_creds = SharePointCredentials(
            auth_type="oauth",
            access_token=request.access_token,
            expires_at=_parse_jwt_exp(request.access_token),
        )
    else:
        sharepoint_creds = SettingsService.get_sharepoint_creds(
            user_id=user.id,
            project_name=request.project_name,
            setting_id=request.setting_id,
        )

    try:
        SharePointDatasourceProcessor.check_sharepoint_connection(
            credentials=sharepoint_creds,
            site_url=request.site_url,
            include_pages=request.include_pages,
            include_documents=request.include_documents,
            include_lists=request.include_lists,
        )
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=INCORRECT_DATASOURCE_SETUP_MESSAGE,
            details=str(e),
            help=INVALID_INPUT_PARAMETERS_HELP,
        ) from e

    datasource_processor = SharePointDatasourceProcessor(
        datasource_name=request.name,
        user=user,
        project_name=request.project_name,
        credentials=sharepoint_creds,
        sp_config=SharePointProcessorConfig(
            site_url=request.site_url,
            include_pages=request.include_pages,
            include_documents=request.include_documents,
            include_lists=request.include_lists,
            max_file_size_mb=request.max_file_size_mb,
            files_filter=request.files_filter or "",
            description=request.description,
            project_space_visible=request.project_space_visible,
            auth_type=auth_type,
            oauth_client_id=request.oauth_client_id,
            oauth_tenant_id=request.oauth_tenant_id,
        ),
        setting_id=request.setting_id,
        request_uuid=raw_request.state.uuid,
        embedding_model=request.embedding_model,
        guardrail_assignments=request.guardrail_assignments,
        cron_expression=request.cron_expression,
    )

    datasource_processor.schedule(background_tasks)
    return BaseResponse(message=f"Indexing of datasource {request.name} has been started in the background")


@router.post("/index/knowledge_base/google", status_code=status.HTTP_200_OK)
def index_knowledge_base_google_doc(
    request: IndexKnowledgeBaseGoogleRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
):
    _index_unique_check(request.project_name, request.name)

    try:
        GoogleDocDatasourceProcessor.check_google_doc(
            product_id=GoogleDocDatasourceProcessor._parse_google_doc_id(request.googleDoc)
        )
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=INCORRECT_DATASOURCE_SETUP_MESSAGE,
            details=str(e),
            help=INVALID_INPUT_PARAMETERS_HELP,
        ) from e

    datasource_processor = GoogleDocDatasourceProcessor(
        datasource_name=request.name,
        user=raw_request.state.user,
        project_name=request.project_name,
        google_doc=request.googleDoc,
        description=request.description,
        project_space_visible=request.project_space_visible,
        request_uuid=raw_request.state.uuid,
        embedding_model=request.embedding_model,
        guardrail_assignments=request.guardrail_assignments,
        cron_expression=request.cron_expression,
    )

    datasource_processor.schedule(background_tasks)
    return BaseResponse(message=f"Indexing of datasource {request.name} has been started in the background")


@router.put("/index/knowledge_base/google/reindex", status_code=status.HTTP_200_OK)
def reindex_knowledge_base_google(
    request: ReIndexKnowledgeBaseRequest, raw_request: Request, background_tasks: BackgroundTasks
):
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
    logger.info(f"Reindexing knowledge base {kb_index[0]}")

    index_info = kb_index[0]
    datasource_processor = GoogleDocDatasourceProcessor(
        datasource_name=index_info.repo_name,
        user=raw_request.state.user,
        project_name=index_info.project_name,
        google_doc=index_info.google_doc_link,
        description="",
        request_uuid=raw_request.state.uuid,
        index_info=index_info,
    )

    datasource_processor.schedule(background_tasks, datasource_processor.reprocess)
    return BaseResponse(message=f"Indexing of datasource {index_info.repo_name} has been started in the background")


@router.put("/index/knowledge_base/google", status_code=status.HTTP_200_OK)
def update_knowledge_base_google(
    request: UpdateKnowledgeBaseGoogleRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
    full_reindex: bool = False,
    user: User = Depends(authenticate),
):
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
    _validate_project_change(request.new_project_name, request.project_name, request.name, raw_request.state.user)

    # Update the index - only update the fields that are provided
    update_params = {}
    if request.description is not None:
        update_params["description"] = request.description
    if request.project_space_visible is not None:
        update_params["project_space_visible"] = request.project_space_visible
    if request.new_project_name:
        update_params["project_name"] = request.new_project_name
    if request.guardrail_assignments is not None:
        update_params["guardrail_assignments"] = request.guardrail_assignments

    if update_params:
        kb_index[0].update_index(
            user=user,
            **update_params,
        )

    if full_reindex:
        logger.info(f"Reindexing knowledge base {kb_index[0]}")

        index_info = kb_index[0]
        datasource_processor = GoogleDocDatasourceProcessor(
            datasource_name=index_info.repo_name,
            user=raw_request.state.user,
            project_name=index_info.project_name,
            google_doc=index_info.google_doc_link,
            description="",
            request_uuid=raw_request.state.uuid,
            index_info=index_info,
            cron_expression=request.cron_expression if cron_expression_provided else None,
        )

        datasource_processor.schedule(background_tasks, datasource_processor.reprocess)
        return BaseResponse(message=f"Indexing of datasource {index_info.repo_name} has been started in the background")

    else:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, kb_index[0], request.cron_expression)
        return BaseResponse(message=EDIT_SUCCESSFUL)


@router.put("/index/knowledge_base/file", status_code=status.HTTP_200_OK)
def update_knowledge_base_files(request: UpdateKnowledgeBaseFilesRequest, user: User = Depends(authenticate)):
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

    # Update the index
    kb_index[0].update_index(
        user=user,
        description=request.description,
        project_space_visible=request.project_space_visible,
        project_name=request.new_project_name,
        guardrail_assignments=request.guardrail_assignments,
    )
    return BaseResponse(message=EDIT_SUCCESSFUL)


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
            _update_datasource_scheduler(user.id, kb_index[0], request.cron_expression)
        return BaseResponse(message=EDIT_SUCCESSFUL)

    confluence_creds = SettingsService.get_confluence_creds(
        user_id=user.id,
        project_name=request.project_name,
        setting_id=request.setting_id,
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
            _update_datasource_scheduler(user.id, kb_index, request.cron_expression)
        return BaseResponse(message=EDIT_SUCCESSFUL)

    jira_creds = SettingsService.get_jira_creds(
        user_id=user.id,
        project_name=request.project_name,
        setting_id=request.setting_id,
    )
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
            _update_datasource_scheduler(user.id, kb_index, request.cron_expression)
        return BaseResponse(message=EDIT_SUCCESSFUL)

    xray_creds = SettingsService.get_xray_creds(
        user_id=user.id,
        project_name=request.project_name,
        setting_id=request.setting_id,
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
            _update_datasource_scheduler(user.id, kb_index, request.cron_expression)
        return BaseResponse(message=EDIT_SUCCESSFUL)

    azure_devops_creds = SettingsService.get_azure_devops_creds(
        user_id=user.id,
        project_name=request.project_name,
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
            _update_datasource_scheduler(user.id, kb_index, request.cron_expression)
        return BaseResponse(message=EDIT_SUCCESSFUL)

    azure_devops_creds = SettingsService.get_azure_devops_creds(
        user_id=user.id,
        project_name=request.project_name,
    )
    project_space_visible = (
        request.project_space_visible if request.project_space_visible is not None else kb_index.project_space_visible
    )

    datasource_processor = AzureDevOpsWorkItemDatasourceProcessor(
        datasource_name=request.name,
        user=user,
        project_name=request.project_name,
        credentials=azure_devops_creds,
        wiql_query=request.wiql_query,
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


def _get_sharepoint_creds_for_reindex(
    request: UpdateKnowledgeBaseSharePointRequest,
    user: User,
    kb_index: KnowledgeBaseIndexInfo,
) -> tuple[SharePointCredentials, str]:
    """Resolve SharePoint credentials and effective auth type for reindex."""
    stored_auth_type = kb_index.sharepoint.auth_type or "integration"
    effective_auth_type = request.auth_type if request.auth_type is not None else stored_auth_type
    if effective_auth_type in ("oauth_codemie", "oauth_custom"):
        if not request.access_token:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message=INCORRECT_DATASOURCE_SETUP_MESSAGE,
                details="access_token is required when auth_type is 'oauth_codemie' or 'oauth_custom'",
                help=INVALID_INPUT_PARAMETERS_HELP,
            )
        creds = SharePointCredentials(
            auth_type="oauth",
            access_token=request.access_token,
            expires_at=_parse_jwt_exp(request.access_token),
        )
    else:
        creds = SettingsService.get_sharepoint_creds(
            user_id=user.id,
            project_name=request.project_name,
            setting_id=request.setting_id if request.setting_id is not None else kb_index.setting_id,
        )
    return creds, effective_auth_type


def _create_sharepoint_processor_for_reindex(
    request: UpdateKnowledgeBaseSharePointRequest,
    user: User,
    kb_index: KnowledgeBaseIndexInfo,
    credentials: SharePointCredentials,
    effective_auth_type: str,
    cron_expression_provided: bool,
    request_uuid: str,
) -> SharePointDatasourceProcessor:
    """Construct a SharePointDatasourceProcessor for a reindex run, merging request and stored values."""
    stored_files_filter = kb_index.sharepoint.files_filter or ""
    return SharePointDatasourceProcessor(
        datasource_name=request.name,
        user=user,
        project_name=request.project_name,
        credentials=credentials,
        sp_config=SharePointProcessorConfig(
            site_url=request.site_url if request.site_url is not None else kb_index.sharepoint.site_url,
            include_pages=(
                request.include_pages if request.include_pages is not None else kb_index.sharepoint.include_pages
            ),
            include_documents=(
                request.include_documents
                if request.include_documents is not None
                else kb_index.sharepoint.include_documents
            ),
            include_lists=(
                request.include_lists if request.include_lists is not None else kb_index.sharepoint.include_lists
            ),
            max_file_size_mb=(
                request.max_file_size_mb
                if request.max_file_size_mb is not None
                else kb_index.sharepoint.max_file_size_mb
            ),
            files_filter=request.files_filter if request.files_filter is not None else stored_files_filter,
            description=request.description if request.description is not None else kb_index.description,
            project_space_visible=(
                request.project_space_visible
                if request.project_space_visible is not None
                else kb_index.project_space_visible
            ),
            auth_type=effective_auth_type,
            oauth_client_id=(
                request.oauth_client_id if request.oauth_client_id is not None else kb_index.sharepoint.oauth_client_id
            ),
            oauth_tenant_id=(
                request.oauth_tenant_id if request.oauth_tenant_id is not None else kb_index.sharepoint.oauth_tenant_id
            ),
        ),
        setting_id=request.setting_id if request.setting_id is not None else kb_index.setting_id,
        index_info=kb_index,
        request_uuid=request_uuid,
        cron_expression=request.cron_expression if cron_expression_provided else None,
    )


@router.put("/index/knowledge_base/sharepoint", status_code=status.HTTP_200_OK, response_model=BaseResponse)
def update_knowledge_base_sharepoint(
    request: UpdateKnowledgeBaseSharePointRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
    full_reindex: bool = False,
) -> BaseResponse:
    user = raw_request.state.user
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
        site_url=request.site_url,
        include_pages=request.include_pages,
        include_documents=request.include_documents,
        include_lists=request.include_lists,
        max_file_size_mb=request.max_file_size_mb,
        files_filter=request.files_filter,
        auth_type=request.auth_type,
        oauth_client_id=request.oauth_client_id,
        oauth_tenant_id=request.oauth_tenant_id,
        reset_error=False,
        setting_id=request.setting_id,
        embeddings_model=request.embedding_model,
        project_name=request.new_project_name,
        guardrail_assignments=request.guardrail_assignments,
    )

    if not full_reindex:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, kb_index, request.cron_expression)
        return BaseResponse(message=EDIT_SUCCESSFUL)

    if kb_index.sharepoint is None:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=INCORRECT_DATASOURCE_SETUP_MESSAGE,
            details="SharePoint configuration not found for this datasource. Provide all required fields explicitly.",
            help=INVALID_INPUT_PARAMETERS_HELP,
        )

    credentials, effective_auth_type = _get_sharepoint_creds_for_reindex(request, user, kb_index)
    datasource_processor = _create_sharepoint_processor_for_reindex(
        request, user, kb_index, credentials, effective_auth_type, cron_expression_provided, raw_request.state.uuid
    )

    logger.info(f"Reindexing SharePoint datasource. Name={request.name}")
    datasource_processor.schedule(background_tasks, datasource_processor.reprocess)
    return BaseResponse(message=f"Indexing of datasource {request.name} has been started in the background")


@router.post("/index/knowledge_base/file", status_code=status.HTTP_200_OK)
def index_knowledge_base_files(
    background_tasks: BackgroundTasks,
    raw_request: Request,
    request: IndexKnowledgeBaseFileRequest = Depends(),
):
    files_paths = []
    _index_unique_check(request.project_name, request.name)

    _kb_demo_user_check(raw_request.state.user)

    parsed_guardrail_assignments = None
    if request.guardrail_assignments:
        try:
            assignments_list = json.loads(request.guardrail_assignments)
            parsed_guardrail_assignments = [GuardrailAssignmentItem.model_validate(item) for item in assignments_list]
        except (json.JSONDecodeError, ValueError, TypeError):
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Invalid guardrail_assignments parameter",
                details="Failed to parse guardrail_assignments.",
                help="Ensure guardrail_assignments is a valid JSON array.",
            )

    file_repo = FileRepositoryFactory.get_current_repository()

    for file in request.files:
        content = file.file.read()

        file_object = file_repo.write_file(
            name=file.filename,
            mime_type=file.headers["content-type"],
            owner=raw_request.state.user.id,
            content=content,
        )
        if file.filename.split(".")[-1] == IndexKnowledgeBaseFileTypes.JSON.value:
            validate_json_file(file.filename, content)
        files_paths.append(FILE_PATH_DATA_NT(name=file_object.name, owner=file_object.owner))
    file_data_source_processor = FileDatasourceProcessor(
        datasource_name=request.name,
        project_name=request.project_name,
        files_paths=files_paths,
        description=request.description,
        project_space_visible=request.project_space_visible,
        csv_separator=request.csv_separator,
        csv_start_row=request.csv_start_row,
        csv_rows_per_document=request.csv_rows_per_document,
        request_uuid=raw_request.state.uuid,
        user=raw_request.state.user,
        embedding_model=request.embedding_model,
        guardrail_assignments=parsed_guardrail_assignments,
    )

    file_data_source_processor.schedule(background_tasks)
    return BaseResponse(message=file_data_source_processor.started_message)


@router.post("/index/provider", status_code=status.HTTP_200_OK)
def create_provider_datasource_index(
    background_tasks: BackgroundTasks,
    raw_request: Request,
    toolkit_id: str,
    provider_name: str,
    request: dict,
):
    _index_unique_check(request['project_name'], request['name'])
    provider = Provider.get_by_fields({"name.keyword": provider_name})

    if not provider:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Provider not found",
            details=f"The provider with name '{provider_name}' could not be found in the system.",
            help="Please verify the provider name and ensure it exists. "
            "If you believe this is an error, contact support.",
        )

    service = ProviderDatasourceCreationService(
        provider=provider,
        toolkit_id=toolkit_id,
        values=request,
        user=raw_request.state.user,
    )

    background_tasks.add_task(service.run)
    return BaseResponse(message=service.started_message)


@router.put("/index/provider/{index_info_id}/reindex", status_code=status.HTTP_200_OK)
def reindex_provider_datasource_index(
    background_tasks: BackgroundTasks,
    raw_request: Request,
    index_info_id: str,
    request: dict,
    user: User = Depends(authenticate),
):
    index_info = IndexInfo.find_by_id(index_info_id)

    if not index_info:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=INDEX_NOT_FOUND_MESSAGE,
            details=f"The index with ID '{index_info_id}' could not be found in the system.",
            help="Please verify the index ID and ensure it exists. If you believe this is an error, contact support.",
        )

    service = ProviderDatasourceReindexService(
        datasource=index_info,
        values=request,
        user=user,
    )
    background_tasks.add_task(service.run)
    return BaseResponse(message=service.started_message)


@router.put("/index/provider/{index_info_id}", status_code=status.HTTP_200_OK)
def update_provider_datasource_index(
    background_tasks: BackgroundTasks,
    raw_request: Request,
    index_info_id: str,
    request: dict,
    user: User = Depends(authenticate),
):
    index_info = IndexInfo.find_by_id(index_info_id)

    if not index_info:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=INDEX_NOT_FOUND_MESSAGE,
            details=f"The index with ID '{index_info_id}' could not be found in the system.",
            help="Please verify the index ID and ensure it exists. If you believe this is an error, contact support.",
        )

    if not Ability(user).can(Action.WRITE, index_info):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ACCESS_DENIED_MESSAGE,
            details="You don't have permission to update the index.",
            help=CHECK_PERMISSIONS_MESSAGE,
        )

    try:
        provider = Provider.get_by_id(index_info.provider_fields.provider_id)
    except NotFoundError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Provider not found",
            details=f"The provider with name '{index_info.provider_id}' could not be found in the system.",
            help="Please verify the provider name and ensure it exists."
            "If you believe this is an error, contact support.",
        ) from e

    service = ProviderDatasourceUpdateService(datasource=index_info, values=request, provider=provider, user=user)
    service.run()

    return BaseResponse(message=service.updated_message)


@router.post("/index/health", status_code=status.HTTP_200_OK)
def health_check_datasource(
    raw_request: Request,
    request: DatasourceHealthCheckRequest,
):
    try:
        return IndexHealthCheckService.health_check_datasource(request=request, user_id=raw_request.state.user.id)
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot validate provided datasource",
            details=f"An error occurred while trying to check the datasource: {str(e)}",
            help="Please check provided data on form or contact an administrator for assistance.",
        ) from e


def _get_index_by_id_or_raise(index_id: str) -> IndexInfo:
    """
    Retrieves an index by ID or raises a standardized exception if not found
    """
    index: IndexInfo = IndexInfo.find_by_id(index_id)  # type: ignore
    if not index:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=f"Cannot find datasource by given id: {index_id}",
            details=f"The datasource with id {index_id} could not be found in the system. "
            f"Please make sure that you provide correct datasource ID",
        )
    return index


def _get_elasticsearch_stats(index: IndexInfo) -> Optional[ElasticsearchStatsResponse]:
    """
    Get Elasticsearch index statistics.

    Args:
        index: The IndexInfo object

    Returns:
        ElasticsearchStatsResponse with index statistics or None if unavailable
    """
    try:
        from codemie.clients.elasticsearch import ElasticSearchClient

        es_client = ElasticSearchClient.get_client()
        index_name = index.get_index_identifier()

        if not es_client.indices.exists(index=index_name):
            return None

        stats = es_client.indices.stats(index=index_name)

        if not stats or 'indices' not in stats or index_name not in stats['indices']:
            return None

        size_in_bytes = stats['indices'][index_name]['total']['store']['size_in_bytes']

        return ElasticsearchStatsResponse(
            index_name=index_name,
            size_in_bytes=size_in_bytes,
        )

    except Exception as e:
        logger.warning(
            f"Failed to get Elasticsearch stats for index {index.id}, repo_name={index.repo_name}, error={str(e)}"
        )
        return None


def _update_datasource_scheduler(user_id: str, index_info: IndexInfo, cron_expression: str) -> None:
    """
    Update or create scheduler settings for a datasource.
    If cron_expression is empty string, deletes existing schedule.

    Args:
        user_id: ID of the user
        index_info: IndexInfo object for the datasource
        cron_expression: Cron expression for scheduling, or empty string to remove schedule
    """
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    SchedulerSettingsService.handle_schedule(
        user_id=user_id,
        project_name=index_info.project_name,
        resource_id=index_info.id,
        resource_name=index_info.repo_name,
        cron_expression=cron_expression,
    )


def _index_unique_check(project_name, repo_name):
    if IndexInfo.filter_by_project_and_repo(project_name=project_name, repo_name=repo_name):
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message=INDEX_EXISTS_MESSAGE,
            details=f"An index with the name '{repo_name}' already exists in the project '{project_name}'.",
            help=INDEX_EXISTS_HELP,
        )


def _kb_demo_user_check(user):
    """Check if user is demo user and has already indexed a repository"""
    if user.is_demo_user:
        existing_repos = KnowledgeBaseIndexInfo.get_by_user_id(user_id=user.id)

        if len(existing_repos) >= 1:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Repository limit reached",
                details="Demo users are allowed to index only one repository. You have already reached this limit.",
                help="To index additional repositories, please upgrade your account "
                "or remove your existing indexed repository.",
            )


def validate_json_file(filename: str, content: bytes):
    """Function to validate if JSON file is correct."""
    try:
        for d in json.loads(content):
            if "content" not in d:
                raise KeyError("missing 'content' key")
            if "metadata" not in d:
                raise KeyError("missing 'metadata' key")

    except (json.JSONDecodeError, KeyError) as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="File has incorrect format",
            details=f"An error occurred while validating file {filename} datasource: {str(e)}",
            help="Please check provided data on form or contact an administrator for assistance.",
        ) from e


def _validate_remote_entities_and_raise(entity: IndexInfo):
    deleted_entity_name = BedrockKnowledgeBaseService.validate_remote_entity_exists_and_cleanup(entity)

    if deleted_entity_name is not None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Requested entity was not found on vendor, deleting from AI/Run.",
            details=f"We haven't found the entity '{deleted_entity_name}' on the vendor.",
            help="Make sure that the entity exists on the vendor side and reimport.",
        )


def _validate_git_credentials(user_id: str, project_name: str, repo_link: str, setting_id: str) -> None:
    """
    Validates git credentials by attempting to retrieve them.

    Args:
        user_id: The user ID
        project_name: The project name
        repo_link: The repository link
        setting_id: The git integration setting ID

    Raises:
        ExtendedHTTPException: If credentials are invalid or missing required fields
    """
    if not setting_id:
        # No git integration configured, skip validation
        return

    try:
        SettingsService.get_git_creds(
            user_id=user_id,
            project_name=project_name,
            repo_link=repo_link,
            setting_id=setting_id,
        )
    except Exception as e:
        error_message = str(e)
        if "Field required" in error_message and "token" in error_message:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Invalid Git Integration Configuration",
                details="The selected git integration is missing a required token. "
                "Please ensure your git integration has a valid access token configured, not just a token alias.",
                help="Go to Settings > Integrations and add a valid token to the git integration.",
            ) from e
        else:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Git Integration Error",
                details=f"Failed to validate git credentials: {error_message}",
                help="Please check your git integration settings.",
            ) from e


def _validate_project_change(new_project_name: str, current_project_name: str, repo_name: str, user: User):
    """Validates project change request and raises appropriate exceptions if invalid.

    Args:
        new_project_name: Target project name
        current_project_name: Current project name
        repo_name: Repository/datasource name
        user: Current user making the request

    Raises:
        ExtendedHTTPException: If project change validation fails
    """
    if not new_project_name or new_project_name == current_project_name:
        return

    # Verify target project exists
    try:
        Application.get_by_id(new_project_name)
    except (NotFoundError, KeyError) as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=APPLICATION_NOT_FOUND_MESSAGE,
            details=f"The application with name '{new_project_name}' could not be found in the system.",
            help=APPLICATION_NOT_FOUND_HELP,
        ) from e

    # Check if user has access to the target project
    if not user.has_access_to_application(new_project_name):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ACCESS_DENIED_MESSAGE,
            details=f"You don't have permission for the project '{new_project_name}'.",
            help=CHECK_PERMISSIONS_MESSAGE,
        )

    # Check if there's an existing index with same name in target project
    existing_index = IndexInfo.filter_by_project_and_repo(project_name=new_project_name, repo_name=repo_name)
    if existing_index:
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message=INDEX_EXISTS_MESSAGE,
            details=f"An index with the name '{repo_name}' already exists in the project '{new_project_name}'.",
            help=INDEX_EXISTS_HELP,
        )
