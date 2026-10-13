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
import time

from fastapi import APIRouter, BackgroundTasks, status
from starlette.requests import Request

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    BaseResponse,
)
from codemie.datasource.sharepoint.sharepoint_datasource_processor import (
    SharePointDatasourceProcessor,
    SharePointProcessorConfig,
)
from codemie.rest_api.models.index import (
    IndexKnowledgeBaseSharePointRequest,
    UpdateKnowledgeBaseSharePointRequest,
    KnowledgeBaseIndexInfo,
)
from codemie.rest_api.security.user import User
from codemie.rest_api.models.settings import SharePointCredentials
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
            stored_auth_type = kb_index.sharepoint.auth_type if kb_index.sharepoint else "integration"
            if stored_auth_type not in ("oauth_codemie", "oauth_custom"):
                _update_datasource_scheduler(user.id, kb_index, request.cron_expression, timezone=request.timezone)
            else:
                logger.info(
                    f"Skipping scheduler update for SharePoint datasource '{kb_index.id}' "
                    f"with auth_type={stored_auth_type}: OAuth tokens are short-lived and cleared after indexing"
                )
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
