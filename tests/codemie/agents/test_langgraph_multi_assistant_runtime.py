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

"""Runtime history tests for LangGraph multi-assistant behavior."""

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool as langchain_tool
from langgraph.prebuilt import create_react_agent
from langgraph_supervisor import create_supervisor
from pydantic import Field

from codemie.agents.langgraph_agent import (
    LangGraphAgent,
    _strip_handoff_back_messages_pre_model_hook,
    _subagent_task_pre_model_hook,
)
from codemie.agents.supervisor.constants import (
    METADATA_KEY_HANDOFF_BACK,
    METADATA_KEY_HANDOFF_DESTINATION,
    METADATA_KEY_SUBAGENT_TASK,
)


class BindableFakeMessagesListChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class RecordingBindableFakeMessagesListChatModel(FakeMessagesListChatModel):
    recorded_messages: list[list] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.recorded_messages.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def message_history_signature(messages: list) -> list[tuple[str, str]]:
    signature = []
    for message in messages:
        if isinstance(message, SystemMessage):
            signature.append(("system", str(message.content)))
            continue
        if isinstance(message, HumanMessage):
            signature.append(("user", str(message.content)))
            continue
        if isinstance(message, ToolMessage):
            if message.response_metadata.get(METADATA_KEY_HANDOFF_DESTINATION):
                signature.append(("subassistant_call", message.name))
            else:
                signature.append(("tool_response", message.name))
            continue
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            for tool_call in message.tool_calls:
                tool_name = tool_call["name"]
                event_type = "subassistant_call" if tool_name.startswith("transfer_to_") else "tool_call"
                signature.append((event_type, tool_name))
            continue
        if isinstance(message, AIMessage):
            signature.append(("assistant_message", str(message.content)))
    return signature


class TestLangGraphMultiAssistantRuntime:
    def test_runtime_supervisor_history_is_preserved_during_custom_handoff(self):
        child_model = BindableFakeMessagesListChatModel(responses=[AIMessage(content="child final")])
        child = create_react_agent(model=child_model, tools=[], name="analyst")
        handoff_tool = LangGraphAgent._create_custom_handoff_tool(
            agent_name="analyst",
            name="transfer_to_analyst",
            description="Sub-assistant: Analyst",
        )
        supervisor_model = RecordingBindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "transfer_to_analyst",
                            "args": {"task": "Analyze this repository"},
                            "id": "call-123",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="supervisor final"),
            ]
        )

        app = create_supervisor(
            agents=[child],
            model=supervisor_model,
            tools=[handoff_tool],
            add_handoff_back_messages=False,
            output_mode="last_message",
            pre_model_hook=_strip_handoff_back_messages_pre_model_hook,
        ).compile()

        result = app.invoke({"messages": [HumanMessage(content="user asks")]})

        assert isinstance(result["messages"][1], AIMessage)
        assert result["messages"][1].tool_calls == [
            {
                "name": "transfer_to_analyst",
                "args": {"task": "Analyze this repository"},
                "id": "call-123",
                "type": "tool_call",
            }
        ]
        assert isinstance(result["messages"][2], ToolMessage)
        assert result["messages"][2].response_metadata == {METADATA_KEY_HANDOFF_DESTINATION: "analyst"}
        assert result["messages"][2].content == "Analyze this repository"
        assert result["messages"][2].additional_kwargs == {METADATA_KEY_SUBAGENT_TASK: True}
        assert result["messages"][3].content == "child final"

        recorded_messages = supervisor_model.recorded_messages[-1]
        assert len(recorded_messages) == 3
        assert isinstance(recorded_messages[0], HumanMessage)
        assert recorded_messages[0].content == "user asks"
        assert isinstance(recorded_messages[1], AIMessage)
        assert recorded_messages[1].tool_calls == [
            {
                "name": "transfer_to_analyst",
                "args": {"task": "Analyze this repository"},
                "id": "call-123",
                "type": "tool_call",
            }
        ]
        assert recorded_messages[2] == ToolMessage(
            content="child final",
            name="transfer_to_analyst",
            tool_call_id="call-123",
        )
        assert not any(
            getattr(message, "response_metadata", {}).get(METADATA_KEY_HANDOFF_BACK) for message in result["messages"]
        )

    def test_runtime_subagent_turns_exclude_parent_history_but_keep_child_state(self):
        @langchain_tool("lookup_repo")
        def lookup_repo(query: str) -> str:
            """Look up repository information for the child agent test."""
            return f"lookup result for {query}"

        child_model = RecordingBindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "lookup_repo",
                            "args": {"query": "repository layout"},
                            "id": "child-call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="child final"),
            ]
        )
        child = create_react_agent(
            model=child_model,
            tools=[lookup_repo],
            name="analyst",
            pre_model_hook=_subagent_task_pre_model_hook,
        )
        handoff_tool = LangGraphAgent._create_custom_handoff_tool(
            agent_name="analyst",
            name="transfer_to_analyst",
            description="Sub-assistant: Analyst",
        )
        supervisor_model = BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "transfer_to_analyst",
                            "args": {"task": "Analyze this repository"},
                            "id": "call-123",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="supervisor final"),
            ]
        )

        app = create_supervisor(
            agents=[child],
            model=supervisor_model,
            tools=[handoff_tool],
            add_handoff_back_messages=False,
            output_mode="last_message",
        ).compile()

        app.invoke({"messages": [HumanMessage(content="user asks")]})

        assert len(child_model.recorded_messages) == 2

        first_turn_messages = child_model.recorded_messages[0]
        assert first_turn_messages == [HumanMessage(content="Analyze this repository")]

        second_turn_messages = child_model.recorded_messages[1]
        assert second_turn_messages[0] == HumanMessage(content="Analyze this repository")
        assert isinstance(second_turn_messages[1], AIMessage)
        assert second_turn_messages[1].tool_calls == [
            {
                "name": "lookup_repo",
                "args": {"query": "repository layout"},
                "id": "child-call-1",
                "type": "tool_call",
            }
        ]
        assert isinstance(second_turn_messages[2], ToolMessage)
        assert second_turn_messages[2].content == "lookup result for repository layout"
        assert not any(getattr(message, "content", None) == "user asks" for message in second_turn_messages)
        assert not any(
            isinstance(message, AIMessage)
            and any(tool_call.get("name") == "transfer_to_analyst" for tool_call in getattr(message, "tool_calls", []))
            for message in second_turn_messages
        )

    def test_runtime_main_assistant_history_with_nested_and_parallel_subassistants(self):
        @langchain_tool("main_tool_1")
        def main_tool_1(topic: str) -> str:
            """Return a deterministic result for the first main tool."""
            return f"main tool 1 result for {topic}"

        @langchain_tool("main_tool_2")
        def main_tool_2(topic: str) -> str:
            """Return a deterministic result for the second main tool."""
            return f"main tool 2 result for {topic}"

        @langchain_tool("main_tool_3")
        def main_tool_3(topic: str) -> str:
            """Return a deterministic result for the third main tool."""
            return f"main tool 3 result for {topic}"

        @langchain_tool("main_tool_4")
        def main_tool_4(topic: str) -> str:
            """Return a deterministic result for the fourth main tool."""
            return f"main tool 4 result for {topic}"

        @langchain_tool("first_sub_tool_1")
        def first_sub_tool_1(topic: str) -> str:
            """Return a deterministic result for the first nested subassistant tool."""
            return f"first sub tool 1 result for {topic}"

        @langchain_tool("first_sub_tool_2")
        def first_sub_tool_2(topic: str) -> str:
            """Return a deterministic result for the second nested subassistant tool."""
            return f"first sub tool 2 result for {topic}"

        @langchain_tool("second_sub_1_tool_1")
        def second_sub_1_tool_1(topic: str) -> str:
            """Return a deterministic result for the first parallel subassistant tool."""
            return f"second sub 1 tool 1 result for {topic}"

        @langchain_tool("second_sub_2_tool_1")
        def second_sub_2_tool_1(topic: str) -> str:
            """Return a deterministic result for the second parallel subassistant tool."""
            return f"second sub 2 tool 1 result for {topic}"

        @langchain_tool("second_sub_2_tool_2")
        def second_sub_2_tool_2(topic: str) -> str:
            """Return a deterministic result for the second parallel subassistant follow-up tool."""
            return f"second sub 2 tool 2 result for {topic}"

        @langchain_tool("second_sub_3_tool_1")
        def second_sub_3_tool_1(topic: str) -> str:
            """Return a deterministic result for the third parallel subassistant tool."""
            return f"second sub 3 tool 1 result for {topic}"

        @langchain_tool("second_sub_3_tool_2")
        def second_sub_3_tool_2(topic: str) -> str:
            """Return a deterministic result for the third parallel subassistant follow-up tool."""
            return f"second sub 3 tool 2 result for {topic}"

        first_subassistant_model = BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "first_sub_tool_1",
                            "args": {"topic": "first delegated task"},
                            "id": "first-sub-tool-call-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "first_sub_tool_2",
                            "args": {"topic": "first delegated task"},
                            "id": "first-sub-tool-call-2",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="first subassistant final response"),
            ]
        )
        first_subassistant = create_react_agent(
            model=first_subassistant_model,
            tools=[first_sub_tool_1, first_sub_tool_2],
            name="first_subassistant",
            pre_model_hook=_subagent_task_pre_model_hook,
        )

        second_subassistant_1_model = BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "second_sub_1_tool_1",
                            "args": {"topic": "parallel task one"},
                            "id": "second-sub-1-tool-call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="second subassistant 1 final response"),
            ]
        )
        second_subassistant_1 = create_react_agent(
            model=second_subassistant_1_model,
            tools=[second_sub_1_tool_1],
            name="second_subassistant_1",
            pre_model_hook=_subagent_task_pre_model_hook,
        )

        second_subassistant_2_model = BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "second_sub_2_tool_1",
                            "args": {"topic": "parallel task two"},
                            "id": "second-sub-2-tool-call-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "second_sub_2_tool_2",
                            "args": {"topic": "parallel task two"},
                            "id": "second-sub-2-tool-call-2",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="second subassistant 2 final response"),
            ]
        )
        second_subassistant_2 = create_react_agent(
            model=second_subassistant_2_model,
            tools=[second_sub_2_tool_1, second_sub_2_tool_2],
            name="second_subassistant_2",
            pre_model_hook=_subagent_task_pre_model_hook,
        )

        second_subassistant_3_model = BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "second_sub_3_tool_1",
                            "args": {"topic": "parallel task three"},
                            "id": "second-sub-3-tool-call-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "second_sub_3_tool_2",
                            "args": {"topic": "parallel task three"},
                            "id": "second-sub-3-tool-call-2",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="second subassistant 3 final response"),
            ]
        )
        second_subassistant_3 = create_react_agent(
            model=second_subassistant_3_model,
            tools=[second_sub_3_tool_1, second_sub_3_tool_2],
            name="second_subassistant_3",
            pre_model_hook=_subagent_task_pre_model_hook,
        )

        first_handoff = LangGraphAgent._create_custom_handoff_tool(
            agent_name="first_subassistant",
            name="transfer_to_first_subassistant",
            description="Sub-assistant: First nested subassistant",
        )
        second_handoff_1 = LangGraphAgent._create_custom_handoff_tool(
            agent_name="second_subassistant_1",
            name="transfer_to_second_subassistant_1",
            description="Sub-assistant: Second nested subassistant one",
        )
        second_handoff_2 = LangGraphAgent._create_custom_handoff_tool(
            agent_name="second_subassistant_2",
            name="transfer_to_second_subassistant_2",
            description="Sub-assistant: Second nested subassistant two",
        )
        second_handoff_3 = LangGraphAgent._create_custom_handoff_tool(
            agent_name="second_subassistant_3",
            name="transfer_to_second_subassistant_3",
            description="Sub-assistant: Second nested subassistant three",
        )

        supervisor_model = RecordingBindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "main_tool_1",
                            "args": {"topic": "main request"},
                            "id": "main-tool-call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "main_tool_2",
                            "args": {"topic": "main request"},
                            "id": "main-tool-call-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "transfer_to_first_subassistant",
                            "args": {"task": "handle the first delegated task"},
                            "id": "main-handoff-call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "main_tool_3",
                            "args": {"topic": "main request"},
                            "id": "main-tool-call-3",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "transfer_to_second_subassistant_1",
                            "args": {"task": "parallel task for branch 1"},
                            "id": "main-handoff-call-2-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "transfer_to_second_subassistant_2",
                            "args": {"task": "parallel task for branch 2"},
                            "id": "main-handoff-call-2-2",
                            "type": "tool_call",
                        },
                        {
                            "name": "transfer_to_second_subassistant_3",
                            "args": {"task": "parallel task for branch 3"},
                            "id": "main-handoff-call-2-3",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "main_tool_4",
                            "args": {"topic": "main request"},
                            "id": "main-tool-call-4",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="main assistant final response"),
            ]
        )

        app = create_supervisor(
            agents=[
                first_subassistant,
                second_subassistant_1,
                second_subassistant_2,
                second_subassistant_3,
            ],
            model=supervisor_model,
            tools=[
                main_tool_1,
                main_tool_2,
                main_tool_3,
                main_tool_4,
                first_handoff,
                second_handoff_1,
                second_handoff_2,
                second_handoff_3,
            ],
            prompt="You are the main supervisor system prompt.",
            add_handoff_back_messages=False,
            output_mode="last_message",
            pre_model_hook=_strip_handoff_back_messages_pre_model_hook,
        ).compile()

        result = app.invoke(
            {"messages": [HumanMessage(content="user asks the main assistant for complex orchestration")]}
        )

        main_assistant_final_history = [*supervisor_model.recorded_messages[-1], result["messages"][-1]]
        history_signature = message_history_signature(main_assistant_final_history)

        assert history_signature[:10] == [
            ("system", "You are the main supervisor system prompt."),
            ("user", "user asks the main assistant for complex orchestration"),
            ("tool_call", "main_tool_1"),
            ("tool_response", "main_tool_1"),
            ("tool_call", "main_tool_2"),
            ("tool_response", "main_tool_2"),
            ("subassistant_call", "transfer_to_first_subassistant"),
            ("tool_response", "transfer_to_first_subassistant"),
            ("tool_call", "main_tool_3"),
            ("tool_response", "main_tool_3"),
        ]

        parallel_history = supervisor_model.recorded_messages[-1][10:16]
        assert [
            message.tool_calls[0]["args"]["task"]
            for message in parallel_history
            if isinstance(message, AIMessage) and message.tool_calls
        ] == [
            "parallel task for branch 1",
            "parallel task for branch 2",
            "parallel task for branch 3",
        ]
        assert [message.content for message in parallel_history if isinstance(message, ToolMessage)] == [
            "second subassistant 1 final response",
            "second subassistant 2 final response",
            "second subassistant 3 final response",
        ]
        assert all(
            isinstance(parallel_history[index], AIMessage)
            and parallel_history[index].tool_calls
            and isinstance(parallel_history[index + 1], ToolMessage)
            for index in range(0, len(parallel_history), 2)
        )

        parallel_signature = history_signature[10:16]
        assert parallel_signature == [
            ("subassistant_call", "transfer_to_second_subassistant_1"),
            ("tool_response", "transfer_to_second_subassistant_1"),
            ("subassistant_call", "transfer_to_second_subassistant_2"),
            ("tool_response", "transfer_to_second_subassistant_2"),
            ("subassistant_call", "transfer_to_second_subassistant_3"),
            ("tool_response", "transfer_to_second_subassistant_3"),
        ]

        assert history_signature[-3:] == [
            ("tool_call", "main_tool_4"),
            ("tool_response", "main_tool_4"),
            ("assistant_message", "main assistant final response"),
        ]

    def test_runtime_parallel_same_subassistant_history_preserves_all_instances(self):
        @langchain_tool("shared_sub_tool")
        def shared_sub_tool(topic: str) -> str:
            """Return a deterministic result for the shared parallel subassistant tool."""
            return f"shared sub tool result for {topic}"

        shared_subassistant_model = RecordingBindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "shared_sub_tool",
                            "args": {"topic": "parallel shared task 1"},
                            "id": "shared-sub-tool-call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="shared subassistant final response 1"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "shared_sub_tool",
                            "args": {"topic": "parallel shared task 2"},
                            "id": "shared-sub-tool-call-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="shared subassistant final response 2"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "shared_sub_tool",
                            "args": {"topic": "parallel shared task 3"},
                            "id": "shared-sub-tool-call-3",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="shared subassistant final response 3"),
            ]
        )
        shared_subassistant = create_react_agent(
            model=shared_subassistant_model,
            tools=[shared_sub_tool],
            name="shared_subassistant",
            pre_model_hook=_subagent_task_pre_model_hook,
        )

        shared_handoff = LangGraphAgent._create_custom_handoff_tool(
            agent_name="shared_subassistant",
            name="transfer_to_shared_subassistant",
            description="Sub-assistant: Shared parallel subassistant",
        )

        supervisor_model = RecordingBindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "transfer_to_shared_subassistant",
                            "args": {"task": "parallel shared task 1"},
                            "id": "shared-handoff-call-1",
                            "type": "tool_call",
                        },
                        {
                            "name": "transfer_to_shared_subassistant",
                            "args": {"task": "parallel shared task 2"},
                            "id": "shared-handoff-call-2",
                            "type": "tool_call",
                        },
                        {
                            "name": "transfer_to_shared_subassistant",
                            "args": {"task": "parallel shared task 3"},
                            "id": "shared-handoff-call-3",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="main assistant final response"),
            ]
        )

        app = create_supervisor(
            agents=[shared_subassistant],
            model=supervisor_model,
            tools=[shared_handoff],
            prompt="You are the main supervisor system prompt.",
            add_handoff_back_messages=False,
            output_mode="last_message",
            pre_model_hook=_strip_handoff_back_messages_pre_model_hook,
        ).compile()

        result = app.invoke({"messages": [HumanMessage(content="user asks for shared parallel orchestration")]})

        history_signature = message_history_signature([*supervisor_model.recorded_messages[-1], result["messages"][-1]])

        initial_shared_prompts = [
            recorded_messages[0].content
            for recorded_messages in shared_subassistant_model.recorded_messages
            if len(recorded_messages) == 1 and isinstance(recorded_messages[0], HumanMessage)
        ]

        assert len(shared_subassistant_model.recorded_messages) == 6
        assert set(initial_shared_prompts) == {
            "parallel shared task 1",
            "parallel shared task 2",
            "parallel shared task 3",
        }
        parallel_history = supervisor_model.recorded_messages[-1][2:]
        assert [
            message.tool_calls[0]["args"]["task"]
            for message in parallel_history
            if isinstance(message, AIMessage) and message.tool_calls
        ] == [
            "parallel shared task 1",
            "parallel shared task 2",
            "parallel shared task 3",
        ]
        assert {message.content for message in parallel_history if isinstance(message, ToolMessage)} == {
            "shared subassistant final response 1",
            "shared subassistant final response 2",
            "shared subassistant final response 3",
        }
        assert len(parallel_history) == 6
        assert all(
            isinstance(parallel_history[index], AIMessage)
            and parallel_history[index].tool_calls
            and isinstance(parallel_history[index + 1], ToolMessage)
            for index in range(0, len(parallel_history), 2)
        )
        assert history_signature[:8] == [
            ("system", "You are the main supervisor system prompt."),
            ("user", "user asks for shared parallel orchestration"),
            ("subassistant_call", "transfer_to_shared_subassistant"),
            ("tool_response", "transfer_to_shared_subassistant"),
            ("subassistant_call", "transfer_to_shared_subassistant"),
            ("tool_response", "transfer_to_shared_subassistant"),
            ("subassistant_call", "transfer_to_shared_subassistant"),
            ("tool_response", "transfer_to_shared_subassistant"),
        ]
        assert history_signature[-1] == ("assistant_message", "main assistant final response")
