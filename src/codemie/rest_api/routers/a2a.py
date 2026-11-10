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
from typing import Any, Optional

from fastapi import APIRouter, status, Depends, Request, Query

from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from codemie.rest_api.dependencies.core import require_core
from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.a2a.client.card_resolver import A2ACardResolver
from codemie.rest_api.a2a.server.codemie_task_manager import CodemieTaskManager
from codemie.rest_api.a2a.types import (
    A2ARequest,
    A2ARequestBody,
    AgentCard,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    JSONRPCError,
    JSONRPCResponse,
    MethodNotFoundError,
    SendStreamingMessageRPCRequest,
    VersionNotSupportedError,
    bad_request,
    error_info,
)
from codemie.rest_api.a2a.utils import assistant_to_agent_card, public_agent_card_dict
from codemie.rest_api.a2a.version import A2A_PROTOCOL_VERSION, is_supported_protocol_version
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

router = APIRouter(
    tags=["A2A"],
    prefix="/v1/a2a",
    dependencies=[Depends(require_core)],
)

A2A_VERSION_HEADER = "A2A-Version"
SUPPORTED_METHODS = frozenset({"SendMessage", "SendStreamingMessage", "GetTask", "CancelTask", "GetExtendedAgentCard"})

# Swagger documentation for the raw-body endpoint (the request body is parsed by hand, see execute_a2a_request).
_EXECUTE_OPENAPI_EXTRA = {
    "parameters": [
        {
            "name": A2A_VERSION_HEADER,
            "in": "header",
            "required": True,
            "schema": {"type": "string", "default": A2A_PROTOCOL_VERSION},
            "description": "A2A protocol version (Major.Minor). Requests without a supported version are rejected.",
        }
    ],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": A2ARequestBody.model_json_schema()}},
    },
}


@router.get(
    "/assistants/{assistant_id}/.well-known/agent-card.json",
    status_code=status.HTTP_200_OK,
    response_model=None,
)
async def get_assistant_agent_card(assistant_id: str, request: Request):
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

    # Enforce security for private/project assistants; intentionally public (global) assistants are exempted
    if not assistant.is_global:
        user = await authenticate(request, internal_user_id=None, bind_key=None)
        if not Ability(user).can(Action.READ, assistant):
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Access Denied",
                details="You don't have permission to access this assistant.",
                help="Please ensure you have the correct permissions to use this assistant.",
            )

    return JSONResponse(public_agent_card_dict(assistant_to_agent_card(assistant, request)))


@router.post(
    "/assistants/{assistant_id}",
    status_code=status.HTTP_200_OK,
    summary="Execute A2A Request",
    description="Endpoint to process requests to assistant",
    openapi_extra=_EXECUTE_OPENAPI_EXTRA,
)
async def execute_a2a_request(assistant_id: str, request: Request, user: User = Depends(authenticate)):
    """
    Process an A2A JSON-RPC request.

    The raw body is parsed here (not by a FastAPI body model) so that every malformed input is answered with a
    JSON-RPC error instead of a FastAPI 422. Order: authentication, assistant lookup and access check, version
    gate, JSON parsing, envelope check, method lookup, params validation.
    """
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

    if not _is_supported_version(_requested_version(request)):
        return _error_response(VersionNotSupportedError())

    try:
        body = json.loads(await request.body())
    except ValueError:  # JSONDecodeError and UnicodeDecodeError are both ValueErrors
        return _error_response(JSONParseError())

    envelope_error = _check_envelope(body)
    if envelope_error:
        return _error_response(envelope_error, _request_id(body))
    if body["method"] not in SUPPORTED_METHODS:
        return _error_response(MethodNotFoundError(), _request_id(body))

    try:
        json_rpc_request = A2ARequest.validate_python(body)
    except ValidationError as e:
        return _error_response(_invalid_params(e), _request_id(body))

    try:
        task_manager = CodemieTaskManager(assistant=assistant, user=user)

        # "SendStreamingMessage" is deliberately absent from the dispatch table below — it
        # returns an SSE stream rather than a single JSON body.
        if isinstance(json_rpc_request, SendStreamingMessageRPCRequest):
            return StreamingResponse(
                _sse_frames(task_manager, json_rpc_request),
                media_type="text/event-stream",
            )

        dispatch = {
            "SendMessage": lambda tm, req: tm.on_send_task(req),
            "GetTask": lambda tm, req: tm.on_get_task(req),
            "CancelTask": lambda tm, req: tm.on_cancel_task(req),
            "GetExtendedAgentCard": lambda tm, req: tm.on_get_extended_agent_card(req, request),
        }
        response = await dispatch[json_rpc_request.method](task_manager, json_rpc_request)
        return JSONResponse(response.model_dump(exclude_none=True, by_alias=True))

    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        return _error_response(InternalError(), json_rpc_request.id)


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
        AgentCard: The fetched agent card (including the CodeMie-internal fields, which the UI stores)
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


def _requested_version(request: Request) -> str:
    """The A2A-Version header, else the query parameter; missing/empty means 0.3 (spec section 3.6)."""
    value = request.headers.get(A2A_VERSION_HEADER) or request.query_params.get(A2A_VERSION_HEADER)
    return (value or "0.3").strip()


def _is_supported_version(version: str) -> bool:
    return is_supported_protocol_version(version)


def _check_envelope(body: Any) -> JSONRPCError | None:
    """Return -32600 unless the body is a JSON-RPC 2.0 request object with a method name."""
    problems = []
    if not isinstance(body, dict):
        problems.append(("body", "Request must be a JSON object"))
    else:
        if body.get("jsonrpc") != "2.0":
            problems.append(("jsonrpc", "Must be exactly 2.0"))
        if not isinstance(body.get("method"), str):
            problems.append(("method", "A method name is required"))
        if not _valid_request_id(body.get("id")):
            problems.append(("id", "Must be a string, a number or null"))
    if not problems:
        return None
    return InvalidRequestError(
        message=f"Invalid JSON-RPC 2.0 request. Supported A2A protocol version is {A2A_PROTOCOL_VERSION}.",
        data=[error_info("INVALID_REQUEST"), bad_request(problems)],
    )


def _valid_request_id(value: Any) -> bool:
    return value is None or (isinstance(value, (str, int)) and not isinstance(value, bool))


def _request_id(body: Any) -> str | int | None:
    request_id = body.get("id") if isinstance(body, dict) else None
    return request_id if _valid_request_id(request_id) else None


def _invalid_params(e: ValidationError) -> InvalidParamsError:
    """-32602 with a google.rpc.BadRequest listing each offending field."""
    violations = []
    for err in e.errors():
        location = [str(part) for part in err["loc"][1:] if part != "params"]  # loc[0] is the method tag
        violations.append((".".join(location) or "params", err["msg"]))
    return InvalidParamsError(data=[bad_request(violations)])


def _error_response(error: JSONRPCError, request_id: str | int | None = None) -> JSONResponse:
    """A JSON-RPC error body with HTTP 200 (JSON-RPC clients read the code from the body); `id` is null if unknown."""
    body = {"jsonrpc": "2.0", "id": request_id, "error": error.model_dump(exclude_none=True)}
    return JSONResponse(body)


async def _sse_frames(task_manager: CodemieTaskManager, request: SendStreamingMessageRPCRequest):
    """Yield each streamed JSON-RPC response as an SSE `data:` frame."""
    try:
        async for event in task_manager.on_send_streaming_message(request):
            yield f"data: {json.dumps(event.model_dump(exclude_none=True, by_alias=True), default=str)}\n\n"
    except Exception as e:
        logger.error(f"Unhandled exception while streaming A2A response: {e}")
        error = JSONRPCResponse(id=request.id, error=InternalError())
        yield f"data: {json.dumps(error.model_dump(exclude_none=True, by_alias=True))}\n\n"
