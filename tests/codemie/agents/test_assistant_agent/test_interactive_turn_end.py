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

"""Verifies the turn-based pause contract: request_user_input ends the agent turn.

This is the single most uncertain integration point of the interactive-input
feature — the pause semantics rest entirely on the tool's return_direct=True
being honored by langgraph's create_react_agent. This test pins that contract:
after the tool runs, the agent must NOT call the LLM a second time.
"""

from unittest.mock import MagicMock

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt import create_react_agent
from pydantic import Field

from codemie.agents.tools.interactive.request_user_input import RequestUserInputTool
from codemie.core.interactive import InteractiveFeaturesConfig


class RecordingBindableFakeChatModel(FakeMessagesListChatModel):
    """Fake chat model that counts how many times the LLM is invoked."""

    call_count: int = Field(default=0)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.call_count += 1
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_request_user_input_ends_turn_without_second_llm_call():
    generator = MagicMock()
    tool = RequestUserInputTool(config=InteractiveFeaturesConfig(action_buttons=True), thread_generator=generator)

    # First (and only) LLM response calls request_user_input. If return_direct
    # is honored, the graph routes to END and never calls the model again.
    tool_call_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "request_user_input",
                "args": {"surface": [{"type": "button", "id": "ok", "label": "OK"}]},
                "id": "call-1",
            }
        ],
    )
    # A second response is provided as a trap: if the agent loops, it would be consumed.
    trap_message = AIMessage(content="the agent should never reach this")
    model = RecordingBindableFakeChatModel(responses=[tool_call_message, trap_message])

    agent = create_react_agent(model=model, tools=[tool])
    agent.invoke({"messages": [HumanMessage(content="decide please")]})

    # Exactly one LLM call: the turn ended after the tool ran.
    assert model.call_count == 1
    # And the interactive request was streamed out.
    assert generator.send.call_count == 1
