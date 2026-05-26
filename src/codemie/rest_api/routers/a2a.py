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

import json
from typing import Optional

from fastapi import APIRouter, status, Depends, Request, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.a2a.client.card_resolver import A2ACardResolver
from codemie.rest_api.a2a.server.codemie_task_manager import CodemieTaskManager
from codemie.rest_api.a2a.types import (
    A2ARequest,
    AgentCard,
    MessageSendRequest,
    MessageStreamRequest,
    A2ARequestBody,
    JSONRPCResponse,
    InvalidRequestError,
    JSONParseError,
    InternalError,
    GetTaskRequest,
    CancelTaskRequest,
    TaskResubscribeRequest,
    SetTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    SendTaskStreamingResponse,
)
from codemie.rest_api.a2a.utils import assistant_to_agent_card
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

router = APIRouter(
    tags=["A2A"],
    prefix="/v1/a2a",
    dependencies=[],
)


@router.get(
    "/assistants/{assistant_id}/.well-known/agent.json",
    status_code=status.HTTP_200_OK,
    response_model=AgentCard,
    response_model_by_alias=True,
)
def get_assistant_agent_card(assistant_id: str, request: Request):
    """
    Returns A2A agent card for a specific assistant.
    Follows the A2A v0.2 specification for agent discovery.
    """
    # Get assistant by ID first
    assistant = Assistant.find_by_id(assistant_id)

    if not assistant:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND, message=f"Assistant with id {assistant_id} wasn't found"
        )

    return assistant_to_agent_card(assistant, request)


@router.post(
    "/assistants/{assistant_id}",
    status_code=status.HTTP_200_OK,
    summary="Execute A2A Request",
    description="Endpoint to process A2A v0.2 JSON-RPC requests (also accepts v0.1 method names)",
)
async def execute_a2a_request(assistant_id: str, request_body: A2ARequestBody, user: User = Depends(authenticate)):
    """
    Process A2A request with explicit request body model.
    Supports both v0.1 and v0.2 method names.
    """
    body = request_body.model_dump()

    assistant = Assistant.find_by_id(assistant_id)

    if not assistant:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND, message=f"Assistant with id {assistant_id} wasn't found"
        )

    if not Ability(user).can(Action.READ, assistant):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Access Denied",
            details="You don't have permission to access this assistant.",
            help="Please ensure you have the correct permissions to use this assistant.",
        )

    try:
        json_rpc_request = A2ARequest.validate_python(body)
        task_manager = CodemieTaskManager(assistant=assistant, user=user)

        if isinstance(json_rpc_request, GetTaskRequest):
            response = await task_manager.on_get_task(json_rpc_request)
            return JSONResponse(response.model_dump(exclude_none=True))

        elif isinstance(json_rpc_request, MessageSendRequest):
            response = await task_manager.on_message_send(json_rpc_request)
            return JSONResponse(response.model_dump(exclude_none=True))

        elif isinstance(json_rpc_request, MessageStreamRequest):
            return StreamingResponse(
                _sse_generator(task_manager.on_message_stream(json_rpc_request)),
                media_type="text/event-stream",
            )

        elif isinstance(json_rpc_request, CancelTaskRequest):
            response = await task_manager.on_cancel_task(json_rpc_request)
            return JSONResponse(response.model_dump(exclude_none=True))

        elif isinstance(json_rpc_request, TaskResubscribeRequest):
            return StreamingResponse(
                _sse_generator(task_manager.on_task_resubscribe(json_rpc_request)),
                media_type="text/event-stream",
            )

        elif isinstance(json_rpc_request, (SetTaskPushNotificationConfigRequest, GetTaskPushNotificationConfigRequest)):
            logger.warning(f"Push notification methods not yet implemented: {json_rpc_request.method}")
            from codemie.rest_api.a2a.types import PushNotificationNotSupportedError
            response = JSONRPCResponse(id=json_rpc_request.id, error=PushNotificationNotSupportedError())
            return JSONResponse(response.model_dump(exclude_none=True))

        else:
            logger.warning(f"Unexpected request type: {type(json_rpc_request)}")
            raise ValueError(f"Unexpected request type: {type(json_rpc_request)}")

    except Exception as e:
        return _handle_exception(e)


@router.get(
    "/assistants/fetch",
    status_code=status.HTTP_200_OK,
    response_model=AgentCard,
    response_model_by_alias=True,
)
async def fetch_remote_assistant(
    url: str = Query(..., description="URL of the remote assistant"),
    project_name: str = Query(..., description="Project name to put assistant"),
    integration_id: Optional[str] = Query(None, description="ID of the integration to use for authentication"),
    user: User = Depends(authenticate),
):
    """
    Fetches an agent card from a remote URL

    Args:
        url: The URL of the remote assistant
        project_name: The project name to associate with the assistant
        integration_id: Optional ID of the integration to use for authentication
        user: The authenticated user making the request

    Returns:
        AgentCard: The fetched agent card
    """
    card_resolver = A2ACardResolver()
    success, agent_card, error_message = await card_resolver.fetch_agent_card(
        url=url, project_name=project_name, user_id=user.id, integration_id=integration_id
    )

    if not success:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="The issue occur while fetching assistant",
            details=f"'{error_message}'",
        )

    return agent_card


async def _sse_generator(event_stream) -> str:
    """Convert an async iterable of SendTaskStreamingResponse to SSE format."""
    async for event in event_stream:
        data = event.model_dump(exclude_none=True)
        yield f"data: {json.dumps(data)}\n\n"


def _handle_exception(e: Exception) -> JSONResponse:
    """Handle exceptions during request processing"""
    if isinstance(e, json.decoder.JSONDecodeError):
        json_rpc_error = JSONParseError()
    elif isinstance(e, ValidationError):
        json_rpc_error = InvalidRequestError(data=json.loads(e.json()))
    else:
        logger.error(f"Unhandled exception: {e}")
        json_rpc_error = InternalError()

    response = JSONRPCResponse(id=None, error=json_rpc_error)
    return JSONResponse(response.model_dump(exclude_none=True), status_code=400)
