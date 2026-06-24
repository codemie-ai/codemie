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

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Iterator, List, Optional

from codemie.core.models import ChatMessage
from codemie.rest_api.models.assistant import Assistant

if TYPE_CHECKING:
    from codemie.core.thread import ThreadedGenerator
from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService


class AgentCoreExecutor:
    """Drop-in AgentExecutor adapter for AWS Bedrock AgentCore Runtime.

    Exposes .invoke() and .stream() with the same dict contract as LangChain
    AgentExecutor so AIToolsAgent needs no special-casing after init_agent().

    Doubles as its own callback carrier: the existing get_thoughts_from_callback()
    implementation searches agent_executor.callbacks for an object with a .thoughts
    attribute, which this class satisfies by including itself in .callbacks.

    Streaming note: .stream() does not yield incremental chunks via the iterator.
    Instead, invoke_agentcore_runtime pushes StreamedGenerationResult chunks directly
    to the thread_generator as it parses the SSE stream, so streaming to the client
    happens as a side-effect of the call. The single yielded dict contains the
    fully-assembled output once the stream is complete.
    """

    def __init__(
        self,
        assistant: Assistant,
        conversation_id: str,
        history_fn: Callable[[], Optional[List[ChatMessage]]],
        thread_generator: Optional[ThreadedGenerator] = None,
    ):
        self._assistant = assistant
        self._conversation_id = conversation_id
        self._history_fn = history_fn
        self._thread_generator = thread_generator
        self.thoughts: list[dict] = []

    @property
    def callbacks(self) -> list:
        return [self]

    def invoke(self, inputs: dict, **_kwargs) -> dict:
        self.thoughts = []
        response = BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
            assistant=self._assistant,
            input_text=inputs.get("input", ""),
            conversation_id=self._conversation_id,
            history=self._history_fn(),
        )
        self.thoughts = response.get("thoughts", [])
        return {"output": response["output"], "intermediate_steps": []}

    def stream(self, inputs: dict, **_kwargs) -> Iterator[dict]:
        self.thoughts = []
        response = BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
            assistant=self._assistant,
            input_text=inputs.get("input", ""),
            conversation_id=self._conversation_id,
            history=self._history_fn(),
            thread_generator=self._thread_generator,
        )
        self.thoughts = response.get("thoughts", [])
        yield {"output": response["output"]}
