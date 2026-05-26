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
from typing import Any, AsyncIterable, Optional

import httpx
from httpx_sse import connect_sse

from codemie.configs import config, logger
from codemie.rest_api.a2a.client.adapter import BedrockAdapter, BaseAdapter
from codemie.rest_api.a2a.types import (
    AgentCard,
    MessageSendRequest,
    MessageSendResponse,
    MessageStreamRequest,
    GetTaskRequest,
    SendTaskResponse,
    JSONRPCRequest,
    GetTaskResponse,
    CancelTaskResponse,
    CancelTaskRequest,
    SetTaskPushNotificationConfigRequest,
    SetTaskPushNotificationConfigResponse,
    GetTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigResponse,
    TaskResubscribeRequest,
    A2AClientHTTPError,
    A2AClientJSONError,
    SendTaskStreamingResponse,
    TaskSendParams,
    TaskQueryParams,
    TaskIdParams,
    TaskPushNotificationConfig,
    ProtocolVersion,
)
from codemie.rest_api.a2a.utils import get_auth_header
from codemie.service.settings.settings import SettingsService


def _detect_protocol_version(agent_card: Optional["AgentCard"]) -> ProtocolVersion:
    """Auto-detect protocol version from AgentCard.version field."""
    if agent_card and agent_card.version:
        v = agent_card.version
        if v.startswith("0.2") or v.startswith("1.") or v.startswith("2."):
            return ProtocolVersion.V02
    return ProtocolVersion.V01


def _serialize_for_v01(payload: dict) -> dict:
    """Convert v0.2 payload to v0.1 format: kind -> type in parts."""
    def convert_parts(parts):
        if not parts:
            return parts
        result = []
        for part in parts:
            p = dict(part) if isinstance(part, dict) else part
            if "kind" in p:
                p["type"] = p.pop("kind")
            result.append(p)
        return result

    if "params" in payload and "message" in payload.get("params", {}):
        msg = payload["params"]["message"]
        if "parts" in msg:
            msg["parts"] = convert_parts(msg["parts"])
        if "kind" in msg:
            del msg["kind"]

    # Rename method for v0.1
    method = payload.get("method", "")
    method_map = {
        "message/send": "tasks/send",
        "message/stream": "tasks/sendSubscribe",
        "tasks/pushNotificationConfig/set": "tasks/pushNotification/set",
        "tasks/pushNotificationConfig/get": "tasks/pushNotification/get",
    }
    if method in method_map:
        payload["method"] = method_map[method]

    return payload


class A2AClient:
    def __init__(
        self,
        agent_card: AgentCard = None,
        url: str = None,
        user_id: str = None,
        project_name: str = None,
        integration_id: str = None,
        protocol_version: Optional[str] = None,
    ):
        """
        Initialize the A2A client with either an agent card or a URL.

        Args:
            agent_card: Optional agent card containing authentication information
            url: Optional URL to the A2A endpoint
            user_id: Optional user ID for retrieving A2A credentials
            project_name: Optional project name for retrieving A2A credentials
            integration_id: Optional integration id for retrieving A2A credentials
            protocol_version: Optional explicit protocol version override ("0.1.0" or "0.2.0").
                Resolution order: explicit override > AgentCard.version auto-detect > default (v0.2)
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

        # Protocol version: explicit override > auto-detect from card > default v0.2
        if protocol_version:
            self.protocol_version = ProtocolVersion(protocol_version)
        else:
            self.protocol_version = _detect_protocol_version(agent_card)

    def _prepare_request_payload(self, request: JSONRPCRequest) -> dict:
        """Serialize request, converting to v0.1 format if needed."""
        payload = request.model_dump(exclude_none=True)
        if self.protocol_version == ProtocolVersion.V01:
            payload = _serialize_for_v01(payload)
        return payload

    async def send_message(self, payload: dict[str, Any]) -> MessageSendResponse:
        """Send a message/send request (v0.2 primary method)."""
        request = MessageSendRequest(params=TaskSendParams.model_validate(payload))
        return MessageSendResponse(**await self._send_request(request))

    async def send_task(self, payload: dict[str, Any]) -> SendTaskResponse:
        """Backward-compatible alias for send_message."""
        return await self.send_message(payload)

    async def send_task_streaming(self, payload: dict[str, Any]) -> AsyncIterable[SendTaskStreamingResponse]:
        request = self.adapter.make_streaming_request(payload)
        json_payload, data_payload = self.adapter.prepare_stream_body(
            self._prepare_request_payload(request)
        )

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
                request_json = self._prepare_request_payload(request)
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

    async def resubscribe(self, payload: dict[str, Any]) -> AsyncIterable[SendTaskStreamingResponse]:
        """Reconnect to an in-progress task's event stream (v0.2)."""
        request = TaskResubscribeRequest(params=TaskQueryParams.model_validate(payload))
        request_json = self._prepare_request_payload(request)

        with (
            httpx.Client(timeout=None) as client,
            connect_sse(
                client=client,
                method="POST",
                url=self.url,
                json=request_json,
                headers=self._get_header(method="POST", url=self.url, body=request_json),
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

    async def set_task_callback(self, payload: dict[str, Any]) -> SetTaskPushNotificationConfigResponse:
        request = SetTaskPushNotificationConfigRequest(params=TaskPushNotificationConfig.model_validate(payload))
        return SetTaskPushNotificationConfigResponse(**await self._send_request(request))

    async def get_task_callback(self, payload: dict[str, Any]) -> GetTaskPushNotificationConfigResponse:
        request = GetTaskPushNotificationConfigRequest(params=TaskIdParams.model_validate(payload))
        return GetTaskPushNotificationConfigResponse(**await self._send_request(request))

    def _get_header(
        self, method: str = "POST", url: Optional[str] = None, body: Optional[dict] = None
    ) -> dict[str, str]:
        """
        Retrieves A2A credentials from settings service and generates appropriate authentication headers.
        """
        try:
            creds = SettingsService.get_a2a_creds(
                user_id=self.user_id, project_name=self.project_name, integration_id=self.integration_id
            )
            return get_auth_header(creds, method, url, body)

        except Exception as e:
            logger.error(f"Failed to get A2A credentials: {e}")
            return {}
