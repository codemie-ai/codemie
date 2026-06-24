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

from typing import Callable, Iterator, List, Optional

from codemie.rest_api.models.assistant import Assistant
from codemie.service.aws_bedrock.bedrock_agent_service import BedrockAgentService


class BedrockAgentExecutor:
    """Drop-in AgentExecutor adapter for AWS Bedrock Agents.

    Exposes .invoke() and .stream() with the same dict contract as LangChain
    AgentExecutor so AIToolsAgent needs no special-casing after init_agent().
    """

    def __init__(
        self,
        assistant: Assistant,
        conversation_id: str,
        history_fn: Callable[[], Optional[List]],
    ):
        self._assistant = assistant
        self._conversation_id = conversation_id
        self._history_fn = history_fn

    @property
    def callbacks(self) -> list:
        return []

    def invoke(self, inputs: dict, **_kwargs) -> dict:
        response = BedrockAgentService.invoke_agent(
            assistant=self._assistant,
            input_text=inputs.get("input", ""),
            conversation_id=self._conversation_id,
            chat_history=self._history_fn(),
        )
        return {"output": response["output"], "intermediate_steps": []}

    def stream(self, inputs: dict, **_kwargs) -> Iterator[dict]:
        yield self.invoke(inputs)
