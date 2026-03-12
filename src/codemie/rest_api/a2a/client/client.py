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
from httpx_sse import connect_sse

from codemie.configs import config, logger
from codemie.rest_api.a2a.client.adapter import BedrockAdapter, BaseAdapter
from codemie.rest_api.a2a.types import (
    AgentCard,
    GetTaskRequest,
    SendTaskRequest,
    SendTaskResponse,
    JSONRPCRequest,
    GetTaskResponse,
    CancelTaskResponse,
    CancelTaskRequest,
    SetTaskPushNotificationRequest,
    SetTaskPushNotificationResponse,
    GetTaskPushNotificationRequest,
    GetTaskPushNotificationResponse,
    A2AClientHTTPError,
    A2AClientJSONError,
    SendTaskStreamingResponse,
    TaskSendParams,
    TaskQueryParams,
    TaskIdParams,
    TaskPushNotificationConfig,
)
from codemie.rest_api.a2a.utils import get_auth_header
from codemie.service.settings.settings import SettingsService


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
        self.url = url or from_card("url")
        self.user_id = user_id or from_card("user_id")
        self.project_name = project_name or from_card("project_name")
        self.integration_id = integration_id or from_card("integration_id")
        self.adapter = BedrockAdapter() if from_card("bedrock_agentcore") else BaseAdapter()

    async def send_task(self, payload: dict[str, Any]) -> SendTaskResponse:
        request = SendTaskRequest(params=TaskSendParams.model_validate(payload))
        return SendTaskResponse(**await self._send_request(request))

    async def send_task_streaming(self, payload: dict[str, Any]) -> AsyncIterable[SendTaskStreamingResponse]:
        request = self.adapter.make_streaming_request(payload)
        json_payload, data_payload = self.adapter.prepare_stream_body(request.model_dump())

        with (
            httpx.Client(timeout=None) as client,
            connect_sse(
                client=client,
                method="POST",
                url=self.url,
                json=json_payload,
                headers=self._get_header(method="POST", url=self.url, body=data_payload),
                data=data_payload,
            ) as event_source,
        ):
            try:
                for sse in event_source.iter_sse():
                    raw = json.loads(sse.data)
                    normalized = self.adapter.normalize_event(raw)
                    yield SendTaskStreamingResponse(**normalized)
            except json.JSONDecodeError as e:
                raise A2AClientJSONError(str(e)) from e
            except httpx.RequestError as e:
                raise A2AClientHTTPError(400, str(e)) from e

    async def _send_request(self, request: JSONRPCRequest) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            try:
                # Image generation could take time, adding timeout
                request_json = request.model_dump()
                response = await client.post(
                    url=self.url,
                    json=request_json,
                    headers=self._get_header(method="POST", url=self.url, body=request_json),
                    timeout=config.A2A_AGENT_REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                raise A2AClientHTTPError(e.response.status_code, str(e)) from e
            except json.JSONDecodeError as e:
                raise A2AClientJSONError(str(e)) from e

    async def get_task(self, payload: dict[str, Any]) -> GetTaskResponse:
        request = GetTaskRequest(params=TaskQueryParams.model_validate(payload))
        return GetTaskResponse(**await self._send_request(request))

    async def cancel_task(self, payload: dict[str, Any]) -> CancelTaskResponse:
        request = CancelTaskRequest(params=TaskIdParams.model_validate(payload))
        return CancelTaskResponse(**await self._send_request(request))

    async def set_task_callback(self, payload: dict[str, Any]) -> SetTaskPushNotificationResponse:
        request = SetTaskPushNotificationRequest(params=TaskPushNotificationConfig.model_validate(payload))
        return SetTaskPushNotificationResponse(**await self._send_request(request))

    async def get_task_callback(self, payload: dict[str, Any]) -> GetTaskPushNotificationResponse:
        request = GetTaskPushNotificationRequest(params=TaskIdParams.model_validate(payload))
        return GetTaskPushNotificationResponse(**await self._send_request(request))

    def _get_header(
        self, method: str = "POST", url: Optional[str] = None, body: Optional[dict] = None
    ) -> dict[str, str]:
        """
        Retrieves A2A credentials from settings service and generates appropriate authentication headers.
        Always returns an {"Authorization": xxx} dictionary or appropriate header for API keys.

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
            return get_auth_header(creds, method, url, body)

        except Exception as e:
            # Log the error but continue without authentication
            logger.error(f"Failed to get A2A credentials: {e}")
            return {}
