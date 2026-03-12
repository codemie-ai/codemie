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
from typing import Any

from codemie.rest_api.a2a.types import (
    TaskArtifactUpdateEvent,
    Artifact,
    TaskStatusUpdateEvent,
    TaskStatus,
    SendTaskStreamingRequest,
    TaskSendParams,
)


class BaseAdapter:
    def prepare_stream_body(self, request_json: dict) -> tuple[dict | None, bytes | None]:
        return request_json, None

    def normalize_event(self, raw: dict) -> dict:
        return raw

    def make_streaming_request(self, payload: dict[str, Any]):
        return SendTaskStreamingRequest(params=(TaskSendParams.model_validate(payload)))


class BedrockAdapter(BaseAdapter):
    def prepare_stream_body(self, request_json: dict):
        payload_bytes = json.dumps(request_json).encode("utf-8")
        return None, payload_bytes

    def normalize_event(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Convert a Bedrock event to the appropriate Task event."""
        result = raw.get("result", {})
        kind = result.get("kind")
        task_id = result.get("taskId") or result.get("id") or result.get("contextId") or ""
        final = result.get("final", False)

        if kind == "status-update":
            status = result.get("status", {})
            message = status.get("message")
            if message:
                message["parts"] = self._convert_parts(message.get("parts", []))

            return {
                "result": TaskStatusUpdateEvent(
                    id=task_id,
                    status=TaskStatus(**status),
                    final=final,
                )
            }

        elif kind == "artifact-update":
            artifact_info = result.get("artifact", {})
            artifact_info["parts"] = self._convert_parts(artifact_info.get("parts", []))

            return {
                "result": TaskArtifactUpdateEvent(
                    id=task_id,
                    artifact=Artifact(**artifact_info),
                )
            }

        return {"result": None}

    @staticmethod
    def _convert_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert parts by changing "kind" to "type"."""
        for part in parts:
            if "kind" in part:
                part["type"] = part.pop("kind")
        return parts

    def make_streaming_request(self, payload: dict[str, Any]):
        payload['message']['messageId'] = payload['id']
        return SendTaskStreamingRequest(method="message/stream", params=(TaskSendParams.model_validate(payload)))
