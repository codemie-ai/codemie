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
from typing import Optional

from fastapi import APIRouter, status, Depends, Request, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.a2a.client.card_resolver import A2ACardResolver
from codemie.rest_api.a2a.server.codemie_task_manager import CodemieTaskManager
from codemie.rest_api.a2a.types import (
    A2ARequest,
    AgentCard,
    SendTaskRequest,
    A2ARequestBody,
    JSONRPCResponse,
    InvalidRequestError,
    JSONParseError,
    InternalError,
    GetTaskRequest,
    SendTaskStreamingRequest,
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
    Follows the A2A specification for agent discovery.
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
    description="Endpoint to process requests to assistant",
)
async def execute_a2a_request(assistant_id: str, request_body: A2ARequestBody, user: User = Depends(authenticate)):
    """
    Process A2A request with explicit request body model.
    This is an alternative to the main endpoint that provides better Swagger UI integration.
    """
    # Convert request body to dict
    body = request_body.model_dump()

    # Get assistant by ID first
    assistant = Assistant.find_by_id(assistant_id)

    if not assistant:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND, message=f"Assistant with id {assistant_id} wasn't found"
        )

    # Check access permissions
    if not Ability(user).can(Action.READ, assistant):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Access Denied",
            details="You don't have permission to access this assistant.",
            help="Please ensure you have the correct permissions to use this assistant.",
        )

    try:
        # Parse the request body as an A2A request
        json_rpc_request = A2ARequest.validate_python(body)

        # Initialize task manager
        task_manager = CodemieTaskManager(assistant=assistant, user=user)

        # Currently we only support SendTaskRequest, other request types will be added later
        if isinstance(json_rpc_request, GetTaskRequest):
            response = await task_manager.on_get_task(json_rpc_request)
        elif isinstance(json_rpc_request, SendTaskRequest):
            response = await task_manager.on_send_task(json_rpc_request)
        elif isinstance(json_rpc_request, SendTaskStreamingRequest):
            response = await task_manager.on_send_task_subscribe(json_rpc_request)
        else:
            logger.warning(f"Unexpected request type: {type(json_rpc_request)}")
            raise ValueError(f"Unexpected request type: {type(request_body.model_dump())}")
        return JSONResponse(response.model_dump(exclude_none=True))

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
