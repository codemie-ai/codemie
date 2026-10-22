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

from abc import ABC, abstractmethod
from typing import AsyncIterable

from codemie.rest_api.a2a.types import (
    CancelTaskRPCRequest,
    CancelTaskRPCResponse,
    GetExtendedAgentCardRPCRequest,
    GetExtendedAgentCardRPCResponse,
    GetTaskRPCRequest,
    GetTaskRPCResponse,
    SendMessageRPCRequest,
    SendMessageRPCResponse,
    SendStreamingMessageRPCRequest,
    StreamRPCResponse,
)


class TaskManager(ABC):
    @abstractmethod
    async def on_get_task(self, request: GetTaskRPCRequest) -> GetTaskRPCResponse:
        pass

    @abstractmethod
    async def on_send_task(self, request: SendMessageRPCRequest) -> SendMessageRPCResponse:
        pass

    @abstractmethod
    async def on_cancel_task(self, request: CancelTaskRPCRequest) -> CancelTaskRPCResponse:
        pass

    @abstractmethod
    async def on_get_extended_agent_card(
        self, request: GetExtendedAgentCardRPCRequest, http_request
    ) -> GetExtendedAgentCardRPCResponse:
        pass

    @abstractmethod
    async def on_send_streaming_message(
        self, request: SendStreamingMessageRPCRequest
    ) -> AsyncIterable[StreamRPCResponse]:
        pass
