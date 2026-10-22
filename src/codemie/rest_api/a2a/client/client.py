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
from typing import Any, AsyncIterable, Optional

import httpx
from httpx_sse import SSEError, aconnect_sse
from pydantic import ValidationError

from codemie.configs import config, logger
from codemie.rest_api.a2a.types import (
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AProtocolVersionError,
    AgentCard,
    CancelTaskRequest,
    CancelTaskRPCRequest,
    CancelTaskRPCResponse,
    GetTaskRequest,
    GetTaskRPCRequest,
    GetTaskRPCResponse,
    JSONRPCRequest,
    SendMessageRequest,
    SendMessageRPCRequest,
    SendMessageRPCResponse,
    SendStreamingMessageRPCRequest,
    StreamRPCResponse,
    get_jsonrpc_url,
)
from codemie.rest_api.a2a.utils import get_auth_header
from codemie.rest_api.a2a.version import A2A_PROTOCOL_VERSION
from codemie.service.settings.settings import SettingsService

# JSON-RPC errors a non-1.0 remote answers with: unknown method, invalid request, unsupported version.
_PROTOCOL_VERSION_ERROR_CODES = (-32601, -32600, -32009)


def _error_code(body: Any) -> int | None:
    """The JSON-RPC error code of a response body, or None when it is not a JSON-RPC error."""
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def _protocol_version_error() -> A2AProtocolVersionError:
    return A2AProtocolVersionError(f"Remote agent does not support A2A protocol {A2A_PROTOCOL_VERSION}")


class A2AClient:
    def __init__(
        self,
        agent_card: AgentCard = None,
        url: str = None,
        user_id: str = None,
        project_name: str = None,
        integration_id: str = None,
    ):
        """
        Initialize the A2A client with either an agent card or a URL.

        Args:
            agent_card: Optional agent card containing authentication information
            url: Optional URL to the A2A endpoint
            user_id: Optional user ID for retrieving A2A credentials
            project_name: Optional project name for retrieving A2A credentials
            integration_id: Optional integration id for retrieving A2A credentials
        """

        if not agent_card and not url:
            raise ValueError("Must provide either agent_card or url")

        def from_card(field: str) -> Any:
            return getattr(agent_card, field, None) if agent_card else None

        self.agent_card = agent_card
        self.url = url or get_jsonrpc_url(agent_card)
        self.user_id = user_id or from_card("user_id")
        self.project_name = project_name or from_card("project_name")
        self.integration_id = integration_id or from_card("integration_id")

    async def send_task(self, payload: dict[str, Any]) -> SendMessageRPCResponse:
        request = SendMessageRPCRequest(params=SendMessageRequest.model_validate(payload))
        return self._parse(SendMessageRPCResponse, await self._send_request(request, detect_version_error=True))

    async def send_task_streaming(self, payload: dict[str, Any]) -> AsyncIterable[StreamRPCResponse]:
        request = SendStreamingMessageRPCRequest(params=SendMessageRequest.model_validate(payload))
        body = request.model_dump(by_alias=True, exclude_none=True)

        try:
            async with (
                httpx.AsyncClient(timeout=None, verify=config.HTTPS_VERIFY_SSL) as client,
                aconnect_sse(
                    client,
                    method="POST",
                    url=self.url,
                    json=body,
                    headers=self._get_header(method="POST", url=self.url, body=body),
                ) as event_source,
            ):
                if event_source.response.status_code != httpx.codes.OK:
                    await self._raise_for_stream_response(event_source.response)
                content_type = event_source.response.headers.get("content-type", "").partition(";")[0]
                if "text/event-stream" not in content_type:
                    # A 200 JSON-RPC error body (e.g. our own -32009 version rejection) instead of an SSE stream.
                    await self._raise_for_stream_response(event_source.response)
                async for sse in event_source.aiter_sse():
                    frame = StreamRPCResponse.model_validate(json.loads(sse.data))
                    if frame.error and frame.error.code in _PROTOCOL_VERSION_ERROR_CODES:
                        raise _protocol_version_error()
                    yield frame
        except (json.JSONDecodeError, ValidationError, SSEError) as e:
            raise A2AClientJSONError(str(e)) from e
        except httpx.RequestError as e:
            raise A2AClientHTTPError(400, str(e)) from e

    @staticmethod
    async def _raise_for_stream_response(response: httpx.Response) -> None:
        """A streaming request answered with a non-200 or a non-SSE body: a JSON-RPC error body decides
        which error to raise."""
        await response.aread()
        try:
            body = response.json()
        except ValueError:
            body = None
        if _error_code(body) in _PROTOCOL_VERSION_ERROR_CODES:
            raise _protocol_version_error()
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise A2AClientHTTPError(e.response.status_code, str(e)) from e
        content_type = response.headers.get("content-type", "").partition(";")[0]
        raise A2AClientJSONError(f"Expected a text/event-stream response for a streaming request, got {content_type!r}")

    async def _send_request(self, request: JSONRPCRequest, detect_version_error: bool = False) -> dict[str, Any]:
        """
        POST a JSON-RPC request and return the response body.

        A JSON-RPC error body is returned even on an HTTP 4xx (the v1.0 error mapping answers 400 with one);
        with `detect_version_error` the version-rejection codes raise A2AProtocolVersionError instead.
        """
        async with httpx.AsyncClient(verify=config.HTTPS_VERIFY_SSL) as client:
            try:
                # Image generation could take time, adding timeout
                request_json = request.model_dump(by_alias=True, exclude_none=True)
                response = await client.post(
                    url=self.url,
                    json=request_json,
                    headers=self._get_header(method="POST", url=self.url, body=request_json),
                    timeout=config.A2A_AGENT_REQUEST_TIMEOUT,
                )
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if detect_version_error and _error_code(body) in _PROTOCOL_VERSION_ERROR_CODES:
                    raise _protocol_version_error()
                if _error_code(body) is None:
                    response.raise_for_status()
                if body is None:
                    raise A2AClientJSONError("Response body is not valid JSON")
                return body
            except httpx.HTTPStatusError as e:
                raise A2AClientHTTPError(e.response.status_code, str(e)) from e

    @staticmethod
    def _parse(model, body):
        """Validate a response body; a non-conformant remote is an A2AClientJSONError, not a raw ValidationError."""
        try:
            return model.model_validate(body)
        except (TypeError, ValidationError) as e:
            raise A2AClientJSONError(str(e)) from e

    async def get_task(self, payload: dict[str, Any]) -> GetTaskRPCResponse:
        request = GetTaskRPCRequest(params=GetTaskRequest.model_validate(payload))
        return self._parse(GetTaskRPCResponse, await self._send_request(request))

    async def cancel_task(self, payload: dict[str, Any]) -> CancelTaskRPCResponse:
        request = CancelTaskRPCRequest(params=CancelTaskRequest.model_validate(payload))
        return self._parse(CancelTaskRPCResponse, await self._send_request(request))

    def _get_header(
        self, method: str = "POST", url: Optional[str] = None, body: Optional[dict] = None
    ) -> dict[str, str]:
        """
        Retrieves A2A credentials from settings service and generates appropriate authentication headers.
        Always includes the A2A-Version header alongside the auth header (if any).

        Args:
            method: HTTP method for the request
            url: URL for the request
            body: Request body

        Returns:
            dict: A dictionary containing the appropriate authorization header
        """

        try:
            # Always get credentials from settings service
            creds = SettingsService.get_a2a_creds(
                user_id=self.user_id, project_name=self.project_name, integration_id=self.integration_id
            )

            # Use the utility function to generate the auth header
            headers = dict(get_auth_header(creds, method, url, body))

        except Exception as e:
            # Log the error but continue without authentication
            logger.error(f"Failed to get A2A credentials: {e}")
            headers = {}

        headers["A2A-Version"] = A2A_PROTOCOL_VERSION
        return headers
