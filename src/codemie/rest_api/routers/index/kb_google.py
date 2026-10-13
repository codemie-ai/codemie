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
from codemie.datasource.google_doc.google_doc_datasource_processor import GoogleDocDatasourceProcessor
from codemie.rest_api.models.index import (
    KnowledgeBaseIndexInfo,
    IndexKnowledgeBaseGoogleRequest,
    ReIndexKnowledgeBaseRequest,
    UpdateKnowledgeBaseGoogleRequest,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.settings.settings import Settings
from codemie_tools.base.models import CredentialTypes
from codemie.service.google_oauth.token_manager import GoogleOAuthTokenManager

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


@router.post("/index/knowledge_base/google", status_code=status.HTTP_200_OK)
def index_knowledge_base_google_doc(
    request: IndexKnowledgeBaseGoogleRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
) -> BaseResponse:
    _index_unique_check(request.project_name, request.name)
    _kb_demo_user_check(raw_request.state.user)

    user = raw_request.state.user

    if request.setting_id is None:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="setting_id is required",
            details="A Google OAuth integration setting_id must be provided to create a Google Docs datasource.",
            help="Please provide a valid setting_id referencing a Google OAuth integration.",
        )

    settings = Settings.find_by_id(request.setting_id)
    if settings is None or settings.user_id != user.id:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Integration not found or access denied",
            details=f"No Google OAuth integration with id '{request.setting_id}' was found for the current user.",
            help="Please verify the setting_id and ensure the integration belongs to your account.",
        )

    if settings.credential_type != CredentialTypes.GOOGLE_OAUTH:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid credential type for Google Docs integration",
            details=(
                f"The setting '{request.setting_id}' has credential type '{settings.credential_type}', "
                "but a Google OAuth credential is required."
            ),
            help="Please provide a setting_id that references a Google OAuth integration.",
        )

    token_manager = GoogleOAuthTokenManager()
    try:
        access_token = token_manager.get_valid_access_token(request.setting_id)
    except ExtendedHTTPException:
        raise
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Failed to fetch Google OAuth credentials",
            details=str(e),
            help="Please ensure your Google OAuth integration is properly configured and authorized.",
        ) from e

    try:
        GoogleDocDatasourceProcessor.check_google_doc(
            product_id=GoogleDocDatasourceProcessor._parse_google_doc_id(request.googleDoc),
            access_token=access_token,
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
        user=user,
        project_name=request.project_name,
        google_doc=request.googleDoc,
        description=request.description,
        project_space_visible=request.project_space_visible,
        request_uuid=raw_request.state.uuid,
        embedding_model=request.embedding_model,
        guardrail_assignments=request.guardrail_assignments,
        cron_expression=request.cron_expression,
        setting_id=request.setting_id,
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
        setting_id=index_info.setting_id,
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
            setting_id=index_info.setting_id,
        )

        datasource_processor.schedule(background_tasks, datasource_processor.reprocess)
        return BaseResponse(message=f"Indexing of datasource {index_info.repo_name} has been started in the background")

    else:
        if cron_expression_provided:
            _update_datasource_scheduler(user.id, kb_index[0], request.cron_expression, timezone=request.timezone)
        return BaseResponse(message=EDIT_SUCCESSFUL)
