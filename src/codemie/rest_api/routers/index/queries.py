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

import json
import io
from typing import List, Optional

from elasticsearch.exceptions import NotFoundError
from fastapi import APIRouter, status, Depends
from starlette.requests import Request
from starlette.responses import StreamingResponse

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    BaseResponse,
)
from codemie.rest_api.models.guardrail import GuardrailEntity
from codemie.datasource.datasources_config import STORAGE_CONFIG
from codemie.datasource.exceptions import ConnectionException
from codemie.datasource.loader.git_loader import GitBatchLoader
from codemie.service.aws_bedrock.bedrock_knowledge_base_service import BedrockKnowledgeBaseService
from codemie.service.provider.datasource import (
    ProviderDatasourceDeletionService,
    PROVIDER_INDEX_TYPE,
)
from codemie.datasource.jira.jira_datasource_processor import JiraDatasourceProcessor
from codemie.rest_api.models.assistant import Assistant, AssistantListResponse
from codemie.rest_api.models.index import (
    DatasourceHealthCheckRequest,
    IndexInfo,
    JiraFieldInfo,
    KnowledgeBaseIndexInfo,
    ElasticsearchStatsResponse,
    GetIndexInfoIDResponse,
    SortOrder,
    SortKey,
)
from codemie.rest_api.models.tool import (
    ToolInvokeResponse,
    DatasourceSearchInvokeRequest,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
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
from codemie.service.settings.settings import SettingsService, Settings
from codemie.service.tools.tool_execution_service import ToolExecutionService
from codemie.service.index.index_encrypted_settings_service import (
    IndexEncryptedSettingsService,
    IndexEncryptedSettingsError,
)

from codemie.rest_api.routers.index._common import (
    INDEX_NOT_FOUND_MESSAGE,
    INDEX_NOT_FOUND_HELP,
)


router = APIRouter()


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
        raise_forbidden("read")
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
        raise_forbidden("read")

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
        raise_forbidden("read")

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
        raise_forbidden("delete")

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


@router.get("/index/jira/fields", status_code=status.HTTP_200_OK, response_model=list[JiraFieldInfo])
def get_jira_datasource_fields(
    project_name: str,
    raw_request: Request,
    setting_id: str | None = None,
) -> list[JiraFieldInfo]:
    """
    List Jira fields available for custom-field indexing (default indexed fields excluded).
    Used by the datasource form to offer a field picker instead of free-text input.
    """
    jira_creds = SettingsService.get_jira_creds(
        user_id=raw_request.state.user.id,
        project_name=project_name,
        setting_id=setting_id,
    )
    try:
        return JiraDatasourceProcessor.get_available_fields(credentials=jira_creds)
    except ConnectionException as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot fetch Jira fields",
            details="The Jira instance is unreachable. It may be down, or a VPN connection may be required.",
            help="Check your network/VPN connection and the Jira URL in the integration, then try again.",
        ) from e
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot fetch Jira fields",
            details=f"An error occurred while fetching fields from Jira: {str(e)}",
            help="Please check the Jira integration credentials and URL, then try again.",
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


def _validate_remote_entities_and_raise(entity: IndexInfo):
    deleted_entity_name = BedrockKnowledgeBaseService.validate_remote_entity_exists_and_cleanup(entity)

    if deleted_entity_name is not None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Requested entity was not found on vendor, deleting from AI/Run.",
            details=f"We haven't found the entity '{deleted_entity_name}' on the vendor.",
            help="Make sure that the entity exists on the vendor side and reimport.",
        )
