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

import asyncio
import time

from typing import List
from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    CreateConversationRequest,
    IdeChatRequest,
    BaseModelResponse,
    VirtualIdeChatRequest,
    CreatedByUser,
)
from codemie.rest_api.models.assistant import VirtualIdeAssistant
from codemie.rest_api.models.ide import IdeConfigurationResponse, IdeConfigurationRequest
from codemie.rest_api.routers.assistant import ask_assistant_by_id, _get_assistant_by_id_or_raise, get_request_handler
from codemie.rest_api.routers.conversation import create_conversation
from codemie.service.settings.settings import SettingsService
from codemie.service.monitoring.agent_monitoring_service import AgentMonitoringService
from fastapi import APIRouter, status, Request, Depends, BackgroundTasks
from codemie.service.assistant.assistant_user_interaction_service import assistant_user_interaction_service
from codemie.service.request_summary_manager import request_summary_manager
from codemie.rest_api.models.conversation import Conversation, ConversationListItem

from codemie.configs import config
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

router = APIRouter(
    tags=["IDE Integration"],
    prefix="/v1/ide",
    dependencies=[],
)


@router.post("/config", status_code=status.HTTP_200_OK, response_model=IdeConfigurationResponse)
def get_config(request: IdeConfigurationRequest, user: User = Depends(authenticate)):
    SettingsService.upsert_ide_settings(
        user_id=user.id,
        ide_installation_id=request.installation_id,
        plugin_key=request.new_plugin_key,
    )

    return IdeConfigurationResponse(
        nats_uri=config.NATS_CLIENT_CONNECT_URI if config.NATS_CLIENT_CONNECT_URI else config.NATS_SERVERS_URI
    )


@router.get(
    "/virtual/conversations/{virtual_assistant_id}",
    response_model=List[ConversationListItem],
)
def get_virtual_assistant_conversations(
    virtual_assistant_id: str, user: User = Depends(authenticate)
) -> List[ConversationListItem]:
    conversation_list_response = Conversation.get_user_conversations(
        user_id=user.id, filters={"initial_assistant_id": _get_formatted_virtual_assistant_id(virtual_assistant_id)}
    )

    return conversation_list_response


@router.post(
    "/virtual/conversations",
    response_model=Conversation,
)
def create_virtual_conversation(
    request: CreateConversationRequest,
    user: User = Depends(authenticate),
) -> Conversation:
    request.initial_assistant_id = _get_formatted_virtual_assistant_id(request.initial_assistant_id)
    return create_conversation(request, user)


@router.post(
    "/inference/virtual",
    status_code=status.HTTP_200_OK,
    response_model=BaseModelResponse,
    response_model_by_alias=True,
)
async def inference_virtual(
    raw_request: Request,
    background_tasks: BackgroundTasks,
    request: VirtualIdeChatRequest,
    user: User = Depends(authenticate),
):
    asyncio.create_task(raw_request.state.wait_for_disconnect())
    try:
        assistant = VirtualIdeAssistant(
            id=_get_formatted_virtual_assistant_id(request.virtual_assistant_id),
            name="Virtual IDE Assistant",
            description="N/A",
            system_prompt=request.system_prompt,
            created_by=CreatedByUser(id=user.id, name=user.name, username=user.username),
            project=request.project or user.current_project,
            llm_model_type=request.llm_model,
            shared=False,
            created_date=time.time(),
            temperature=request.temperature,
            top_p=request.top_p,
        )
        request_uuid = raw_request.state.uuid
        assistant_user_interaction_service.record_usage(assistant=assistant, user=user)

        request_summary_manager.create_request_summary(
            request_id=request_uuid,
            project_name=assistant.project,
            user=user.as_user_model(),
        )

        handler = get_request_handler(assistant, user, request_uuid)
        return await asyncio.to_thread(handler.process_request, request, background_tasks, raw_request)
    except Exception as e:
        details = f"An unexpected error occurred during answer generation: {str(e)}"
        error = ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Virtual assistant error",
            details=details,
            help="",
        )
        logger.error(details, exc_info=True)
        raise error from e


@router.post(
    "/inference/{assistant_id}",
    status_code=status.HTTP_200_OK,
    response_model=BaseModelResponse,
    response_model_by_alias=True,
)
async def inference(
    raw_request: Request,
    assistant_id: str,
    background_tasks: BackgroundTasks,
    request: IdeChatRequest,
    user: User = Depends(authenticate),
):
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    try:
        result = await ask_assistant_by_id(
            raw_request=raw_request,
            assistant_id=assistant_id,
            background_tasks=background_tasks,
            request=request,
            user=user,
        )
        AgentMonitoringService.send_assistant_mngmnt_metric(
            metric_name="ide_request", user=user, assistant=assistant, success=True
        )
        return result
    except:
        AgentMonitoringService.send_assistant_mngmnt_metric(
            metric_name="ide_request", user=user, assistant=assistant, success=False
        )
        raise


def _get_formatted_virtual_assistant_id(virtual_assistant_id: str):
    return f"{SettingsService.IDE_PREFIX}{virtual_assistant_id}"
