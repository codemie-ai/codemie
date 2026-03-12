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

from datetime import datetime

from fastapi import APIRouter, status, Depends, BackgroundTasks

from codemie.configs import config
from codemie.core.models import (
    Application,
    ApplicationRequest,
    ApplicationsResponse,
    BaseResponse,
    BaseResponseWithData,
    LLMBulkRetirementRequest,
    LLMRetirementRequest,
)
from codemie.rest_api.models.conversation import Conversation, Operator, FinalOperatorFeedback
from codemie.rest_api.models.standard import FinalFeedbackRequest
from codemie.rest_api.security.authentication import authenticate, admin_access_only
from codemie.rest_api.security.user import User
from codemie.service.monitoring.project_monitoring_service import ProjectMonitoringService
from codemie.service.platform.platform_indexing_service import PlatformIndexingService
from codemie.configs.logger import logger

router = APIRouter(tags=["Admin"], prefix="/v1", dependencies=[Depends(authenticate), Depends(admin_access_only)])


@router.get(
    "/admin/applications",
    status_code=status.HTTP_200_OK,
    response_model=ApplicationsResponse,
)
def get_applications(search: str = None, limit: int = None):
    applications = Application.search_by_name(name_query=search, limit=limit)
    result = [app.name for app in applications]

    return ApplicationsResponse(applications=result)


@router.post(
    "/admin/application",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
)
def add_application(application: ApplicationRequest, admin: User = Depends(authenticate)):
    from codemie.service.user.application_service import application_service

    app = application_service.create_application(application.name)
    ProjectMonitoringService.send_project_creation_metric(
        user=admin,
        project_name=app.name,
    )

    return BaseResponse(message=f"Application {application} has been created")


@router.get(
    "/admin/users/{user_id}/conversations/{conversation_id}",
    response_model=Conversation,
)
async def get_conversation_by_ids(user_id: str, conversation_id: str) -> Conversation:
    """
    Get a conversation document by provided user id and conversation id
    """
    return Conversation.get_by_fields({"conversation_id": conversation_id, "user_id": user_id})


@router.put(
    "/admin/users/{user_id}/conversations/{conversation_id}/feedback",
    response_model=Conversation,
)
async def update_conversation_final_feedback(
    user_id: str,
    conversation_id: str,
    request: FinalFeedbackRequest,
    admin: User = Depends(authenticate),
) -> Conversation:
    """
    Update chat with final operator chat feedback
    """
    chat = Conversation.get_by_fields({"user_id": user_id, "conversation_id": conversation_id})

    if chat.final_operator_mark:
        mark = chat.final_operator_mark.copy(update=request.dict())
    else:
        mark = FinalOperatorFeedback(**request.dict())
        mark.date = datetime.now()

    mark.operator = Operator(user_id=admin.id, name=admin.name)
    chat.final_operator_mark = mark
    chat.update()
    return chat


@router.get("/speech/config")
def get_speech_token():
    service_config = {
        'token': config.AZURE_SPEECH_SERVICE_KEY,
        'region': config.AZURE_SPEECH_REGION,
        'stt_wss_url': f"wss://{config.AZURE_SPEECH_REGION}.stt.speech.microsoft.com/speech/universal/v2",
        'tts_url': f"https://{config.AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/avatar/relay/token/v1",
    }
    return service_config


@router.post(
    "/admin/marketplace/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BaseResponse,
)
async def reindex_marketplace_assistants(
    background_tasks: BackgroundTasks,
    admin: User = Depends(authenticate),
):
    """
    Trigger reindexing of marketplace assistants datasource.

    This endpoint is admin-only and triggers a background task to:
    1. Sync all platform datasources (marketplace assistants)
    2. Reindex published assistants into the marketplace index

    The operation runs in the background and returns immediately.

    Returns:
        BaseResponse with status message
    """
    logger.info(
        "Marketplace reindexing triggered by admin",
        extra={"admin_id": admin.id, "admin_name": admin.name},
    )

    def reindex_task():
        try:
            logger.info(
                "Starting marketplace assistants reindexing",
                extra={"admin_id": admin.id, "admin_name": admin.name},
            )
            results = PlatformIndexingService.sync_all_platform_datasources(user=admin)
            logger.info(
                "Marketplace reindexing completed",
                extra={"results": results, "total_indexed": sum(results.values()), "admin_id": admin.id},
            )
        except Exception as e:
            logger.error(
                f"Marketplace reindexing failed: {e}",
                exc_info=True,
                extra={"admin_id": admin.id},
            )

    background_tasks.add_task(reindex_task)

    return BaseResponse(message="Marketplace assistants reindexing started in background. Check logs for progress.")


@router.get(
    "/admin/llm/reload",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
)
async def reload_llm_models(admin: User = Depends(authenticate)):
    """
    Reload LLM models from LiteLLM proxy without restarting the container.

    This endpoint is admin-only and allows reloading of LLM models from the LiteLLM proxy.
    It clears the models cache and reinitializes models from the proxy, making new models
    or configuration changes available without requiring a container restart.

    This is useful when:
    - New models are added to the LiteLLM proxy
    - Model configurations are updated
    - Model availability changes

    Returns:
        BaseResponse with success message and model counts
    """
    logger.info(
        "LLM models reload triggered by admin",
        extra={"admin_id": admin.id, "admin_name": admin.name},
    )

    try:
        from codemie.enterprise.litellm import get_litellm_service_or_none, get_available_models
        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.service.llm_service.llm_service import llm_service

        litellm_service = get_litellm_service_or_none()
        if litellm_service is None:
            raise ExtendedHTTPException(
                code=400,
                message="LiteLLM service not available",
                details="LiteLLM enterprise package may not be installed or LiteLLM is disabled in configuration.",
            )

        # Clear the models cache to force fresh fetch
        litellm_service.models_cache.clear()
        logger.info("Cleared LiteLLM models cache")

        # Fetch fresh models from LiteLLM proxy
        models = get_available_models()

        # Reinitialize models in llm_service
        llm_service.initialize_default_litellm_models(models)

        message = (
            f"Successfully reloaded LLM models: {len(models.chat_models)} chat models, "
            f"{len(models.embedding_models)} embedding models"
        )

        logger.info(
            "LLM models reloaded successfully",
            extra={
                "admin_id": admin.id,
                "admin_name": admin.name,
                "chat_models_count": len(models.chat_models),
                "embedding_models_count": len(models.embedding_models),
            },
        )

        return BaseResponse(message=message)

    except Exception as e:
        error_message = f"Failed to reload LLM models: {str(e)}"
        logger.error(
            error_message,
            exc_info=True,
            extra={"admin_id": admin.id, "admin_name": admin.name},
        )
        return BaseResponse(message=error_message)


@router.post(
    "/admin/llm/retire",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponseWithData,
)
def retire_llm_model(
    request: LLMRetirementRequest,
    admin: User = Depends(authenticate),
) -> BaseResponseWithData:
    """
    Retire a deprecated LLM model across all DB entities.

    Replaces all references to `deprecated_model` with `replacement_model` in:
    - assistants (llm_model_type column — all rows)
    - assistant_configurations (llm_model_type — latest version per assistant only)
    - workflows (assistants JSONB + yaml_config TEXT, Python-side YAML parsing)

    The existing update_date on all affected records is preserved.
    """
    from codemie.service.llm_retirement_service import llm_retirement_service

    logger.info(
        f"LLM model retirement triggered by admin '{admin.name or admin.id}': "
        f"deprecated='{request.deprecated_model}', replacement='{request.replacement_model}'"
    )

    result = llm_retirement_service.retire_model(
        deprecated_model=request.deprecated_model,
        replacement_model=request.replacement_model,
        check_models_existence=request.check_models_existence,
    )

    return BaseResponseWithData(
        message=f"Successfully retired model '{request.deprecated_model}' -> '{request.replacement_model}'",
        data={
            "assistants_updated": result.assistants_updated,
            "assistant_configurations_updated": result.assistant_configurations_updated,
            "workflows_updated": result.workflows_updated,
        },
    )


@router.post(
    "/admin/llm/retire/bulk",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponseWithData,
)
def retire_llm_models_bulk(
    request: LLMBulkRetirementRequest,
    admin: User = Depends(authenticate),
) -> BaseResponseWithData:
    """
    Retire multiple deprecated LLM models in a single call.

    Each pair is processed independently in its own transaction — a failure
    on one pair does not affect the others. Per-item results (including
    errors) are returned in the response data.
    """
    from codemie.service.llm_retirement_service import llm_retirement_service

    logger.info(
        f"Bulk LLM retirement triggered by admin '{admin.name or admin.id}': {len(request.retirements)} pair(s)"
    )

    results = llm_retirement_service.retire_models_bulk(
        retirements=request.retirements,
        check_models_existence=request.check_models_existence,
    )

    succeeded = sum(1 for r in results if r.success)
    failed = len(results) - succeeded

    return BaseResponseWithData(
        message=f"Bulk retirement complete: {succeeded} succeeded, {failed} failed",
        data={
            "total": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "results": [
                {
                    "deprecated_model": r.deprecated_model,
                    "replacement_model": r.replacement_model,
                    "success": r.success,
                    "assistants_updated": r.assistants_updated,
                    "assistant_configurations_updated": r.assistant_configurations_updated,
                    "workflows_updated": r.workflows_updated,
                    "error": r.error,
                }
                for r in results
            ],
        },
    )
