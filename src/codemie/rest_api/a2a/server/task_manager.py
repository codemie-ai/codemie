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

from abc import ABC, abstractmethod
from typing import AsyncIterable

from codemie.rest_api.a2a.types import (
    MessageSendRequest,
    MessageSendResponse,
    MessageStreamRequest,
    SendTaskStreamingResponse,
    GetTaskRequest,
    GetTaskResponse,
    CancelTaskRequest,
    CancelTaskResponse,
    TaskResubscribeRequest,
    JSONRPCResponse,
    # Backward-compat aliases
    SendTaskRequest,
    SendTaskResponse,
    SendTaskStreamingRequest,
)


class TaskManager(ABC):
    @abstractmethod
    async def on_message_send(self, request: MessageSendRequest) -> MessageSendResponse:
        pass

    @abstractmethod
    async def on_message_stream(
        self, request: MessageStreamRequest
    ) -> AsyncIterable[SendTaskStreamingResponse]:
        pass

    @abstractmethod
    async def on_get_task(self, request: GetTaskRequest) -> GetTaskResponse:
        pass

    @abstractmethod
    async def on_cancel_task(self, request: CancelTaskRequest) -> CancelTaskResponse:
        pass

    @abstractmethod
    async def on_task_resubscribe(
        self, request: TaskResubscribeRequest
    ) -> AsyncIterable[SendTaskStreamingResponse]:
        pass

    # Backward-compatible aliases
    async def on_send_task(self, request: SendTaskRequest) -> SendTaskResponse:
        return await self.on_message_send(request)

    async def on_send_task_subscribe(
        self, request: SendTaskStreamingRequest
    ) -> AsyncIterable[SendTaskStreamingResponse] | JSONRPCResponse:
        return self.on_message_stream(request)
