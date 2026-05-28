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

"""Handoff and pre-model-hook tests for LangGraph multi-assistant behavior."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Send
from langgraph_supervisor.handoff import create_handoff_back_messages

from codemie.agents.supervisor.constants import (
    METADATA_KEY_HANDOFF_DESTINATION,
    METADATA_KEY_PARALLEL_SUBAGENT_PARENT_HANDOFF,
    METADATA_KEY_SUBAGENT_TASK,
)
from codemie.agents.langgraph_agent import (
    LangGraphAgent,
    _strip_handoff_back_messages_pre_model_hook,
    _strip_subagent_task_messages_pre_model_hook,
    _subagent_task_pre_model_hook,
)


class TestLangGraphMultiAssistantHandoffs:
    def test_custom_handoff_tool_uses_clean_single_subagent_context(self):
        handoff_tool = LangGraphAgent._create_custom_handoff_tool(
            agent_name="analyst",
            name="transfer_to_analyst",
            description="Sub-assistant: Analyst",
        )
        ai_message = AIMessage(content="Route to analyst")
        ai_message.tool_calls = [
            {
                "name": "transfer_to_analyst",
                "args": {"task": "Analyze this repository"},
                "id": "call-123",
                "type": "tool_call",
            }
        ]
        state = {
            "messages": [
                HumanMessage(content="User request"),
                ai_message,
            ],
            "thread_id": "thread-1",
        }

        result = handoff_tool.func(task="Analyze this repository", state=state, tool_call_id="call-123")

        assert result.goto == "analyst"
        assert result.update["thread_id"] == "thread-1"
        assert result.update["messages"][:2] == state["messages"]
        assert isinstance(result.update["messages"][-1], ToolMessage)
        assert result.update["messages"][-1].response_metadata == {METADATA_KEY_HANDOFF_DESTINATION: "analyst"}
        assert result.update["messages"][-1].content == "Analyze this repository"
        assert result.update["messages"][-1].additional_kwargs == {METADATA_KEY_SUBAGENT_TASK: True}

    def test_custom_handoff_tool_uses_clean_parallel_subagent_context(self):
        handoff_tool = LangGraphAgent._create_custom_handoff_tool(
            agent_name="analyst",
            name="transfer_to_analyst",
            description="Sub-assistant: Analyst",
        )
        ai_message = AIMessage(content="Route work")
        ai_message.tool_calls = [
            {
                "name": "transfer_to_analyst",
                "args": {"task": "Analyze this repository"},
                "id": "call-123",
                "type": "tool_call",
            },
            {
                "name": "transfer_to_researcher",
                "args": {"task": "Research dependencies"},
                "id": "call-456",
                "type": "tool_call",
            },
        ]
        state = {
            "messages": [
                HumanMessage(content="User request"),
                ai_message,
            ],
            "thread_id": "thread-1",
        }

        result = handoff_tool.func(task="Analyze this repository", state=state, tool_call_id="call-123")

        assert len(result.goto) == 2
        assert all(isinstance(send_target, Send) for send_target in result.goto)

        send_targets = {send_target.node: send_target for send_target in result.goto}

        analyst_send = send_targets["analyst"]
        researcher_send = send_targets["researcher"]

        assert analyst_send.node == "analyst"
        assert len(analyst_send.arg["messages"]) == 3
        assert analyst_send.arg["messages"][0] == state["messages"][0]
        assert isinstance(analyst_send.arg["messages"][1], AIMessage)
        assert analyst_send.arg["messages"][1].content == "Route work"
        assert analyst_send.arg["messages"][1].tool_calls == [ai_message.tool_calls[0]]
        assert isinstance(analyst_send.arg["messages"][2], ToolMessage)
        assert analyst_send.arg["messages"][2].content == "Analyze this repository"
        assert analyst_send.arg["messages"][2].name == "transfer_to_analyst"
        assert analyst_send.arg["messages"][2].tool_call_id == "call-123"
        assert analyst_send.arg["messages"][2].response_metadata == {METADATA_KEY_HANDOFF_DESTINATION: "analyst"}
        assert analyst_send.arg["messages"][2].additional_kwargs == {METADATA_KEY_SUBAGENT_TASK: True}

        assert researcher_send.node == "researcher"
        assert len(researcher_send.arg["messages"]) == 3
        assert researcher_send.arg["messages"][0] == state["messages"][0]
        assert isinstance(researcher_send.arg["messages"][1], AIMessage)
        assert researcher_send.arg["messages"][1].content == "Route work"
        assert researcher_send.arg["messages"][1].tool_calls == [ai_message.tool_calls[1]]
        assert isinstance(researcher_send.arg["messages"][2], ToolMessage)
        assert researcher_send.arg["messages"][2].content == "Research dependencies"
        assert researcher_send.arg["messages"][2].name == "transfer_to_researcher"
        assert researcher_send.arg["messages"][2].tool_call_id == "call-456"
        assert researcher_send.arg["messages"][2].response_metadata == {METADATA_KEY_HANDOFF_DESTINATION: "researcher"}
        assert researcher_send.arg["messages"][2].additional_kwargs == {METADATA_KEY_SUBAGENT_TASK: True}

        assert len(result.update["messages"]) == 3
        assert result.update["messages"][0] == state["messages"][0]
        assert isinstance(result.update["messages"][1], ToolMessage)
        assert result.update["messages"][1].content == "Analyze this repository"
        assert result.update["messages"][1].name == "transfer_to_analyst"
        assert result.update["messages"][1].tool_call_id == "call-123"
        assert result.update["messages"][1].id == "parallel-handoff-parent-call-123"
        assert result.update["messages"][1].additional_kwargs == {METADATA_KEY_PARALLEL_SUBAGENT_PARENT_HANDOFF: True}
        assert result.update["messages"][1].response_metadata == {METADATA_KEY_HANDOFF_DESTINATION: "analyst"}
        assert isinstance(result.update["messages"][2], ToolMessage)
        assert result.update["messages"][2].content == "Research dependencies"
        assert result.update["messages"][2].name == "transfer_to_researcher"
        assert result.update["messages"][2].tool_call_id == "call-456"
        assert result.update["messages"][2].id == "parallel-handoff-parent-call-456"
        assert result.update["messages"][2].additional_kwargs == {METADATA_KEY_PARALLEL_SUBAGENT_PARENT_HANDOFF: True}
        assert result.update["messages"][2].response_metadata == {METADATA_KEY_HANDOFF_DESTINATION: "researcher"}

    def test_subagent_task_pre_model_hook_uses_only_synthesized_task(self):
        task_message = ToolMessage(
            content="Analyze this repository",
            name="transfer_to_analyst",
            tool_call_id="call-123",
            response_metadata={METADATA_KEY_HANDOFF_DESTINATION: "analyst"},
            additional_kwargs={METADATA_KEY_SUBAGENT_TASK: True},
        )

        result = _subagent_task_pre_model_hook(
            {
                "messages": [
                    HumanMessage(content="Original user request"),
                    AIMessage(content="Supervisor reasoning"),
                    task_message,
                ]
            }
        )

        assert result == {"llm_input_messages": [HumanMessage(content="Analyze this repository")]}

    def test_subagent_task_pre_model_hook_preserves_follow_up_subagent_state(self):
        task_message = ToolMessage(
            content="Analyze this repository",
            name="transfer_to_analyst",
            tool_call_id="call-123",
            response_metadata={METADATA_KEY_HANDOFF_DESTINATION: "analyst"},
            additional_kwargs={METADATA_KEY_SUBAGENT_TASK: True},
        )
        child_tool_call = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "lookup_repo",
                    "args": {"query": "repository layout"},
                    "id": "child-call-1",
                    "type": "tool_call",
                }
            ],
        )
        child_tool_result = ToolMessage(
            content="Found repository layout",
            tool_call_id="child-call-1",
            name="lookup_repo",
        )

        result = _subagent_task_pre_model_hook(
            {
                "messages": [
                    HumanMessage(content="Original user request"),
                    AIMessage(content="Supervisor reasoning"),
                    task_message,
                    child_tool_call,
                    child_tool_result,
                ]
            }
        )

        assert result == {
            "llm_input_messages": [
                HumanMessage(content="Analyze this repository"),
                child_tool_call,
                child_tool_result,
            ]
        }

    def test_strip_subagent_task_messages_pre_model_hook(self):
        task_message = HumanMessage(
            content="Analyze this repository",
            additional_kwargs={METADATA_KEY_SUBAGENT_TASK: True},
        )
        visible_message = AIMessage(content="Supervisor reasoning")

        result = _strip_subagent_task_messages_pre_model_hook(
            {"messages": [HumanMessage(content="Original user request"), task_message, visible_message]}
        )

        assert result == {"llm_input_messages": [HumanMessage(content="Original user request"), visible_message]}

    def test_strip_handoff_back_messages_pre_model_hook(self):
        final_answer = AIMessage(content="Final analyst answer")
        handoff_back_ai, handoff_back_tool = create_handoff_back_messages("analyst", "supervisor")

        result = _strip_handoff_back_messages_pre_model_hook(
            {"messages": [HumanMessage(content="User request"), final_answer, handoff_back_ai, handoff_back_tool]}
        )

        assert result["llm_input_messages"] == [HumanMessage(content="User request"), final_answer]

    def test_strip_handoff_back_messages_pre_model_hook_hides_parallel_parent_handoffs(self):
        parallel_parent_handoff = ToolMessage(
            content="Analyze this repository",
            name="transfer_to_analyst",
            tool_call_id="call-123",
            additional_kwargs={METADATA_KEY_PARALLEL_SUBAGENT_PARENT_HANDOFF: True},
            response_metadata={METADATA_KEY_HANDOFF_DESTINATION: "analyst"},
        )
        analyst_response = AIMessage(content="Final analyst answer", name="analyst")

        result = _strip_handoff_back_messages_pre_model_hook(
            {
                "messages": [
                    HumanMessage(content="User request"),
                    parallel_parent_handoff,
                    analyst_response,
                ]
            }
        )

        assert result["llm_input_messages"] == [
            HumanMessage(content="User request"),
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
            ToolMessage(content="Final analyst answer", name="transfer_to_analyst", tool_call_id="call-123"),
        ]

    def test_strip_handoff_back_messages_pre_model_hook_hides_single_parent_handoff(self):
        single_parent_handoff = ToolMessage(
            content="Analyze this repository",
            name="transfer_to_analyst",
            tool_call_id="call-123",
            additional_kwargs={METADATA_KEY_SUBAGENT_TASK: True},
            response_metadata={METADATA_KEY_HANDOFF_DESTINATION: "analyst"},
        )
        analyst_response = AIMessage(content="Final analyst answer", name="analyst")

        result = _strip_handoff_back_messages_pre_model_hook(
            {
                "messages": [
                    HumanMessage(content="User request"),
                    single_parent_handoff,
                    analyst_response,
                ]
            }
        )

        assert result["llm_input_messages"] == [
            HumanMessage(content="User request"),
            ToolMessage(content="Final analyst answer", name="transfer_to_analyst", tool_call_id="call-123"),
        ]

    def test_parent_context_keeps_original_messages_and_only_final_subagent_return(self):
        handoff_tool = LangGraphAgent._create_custom_handoff_tool(
            agent_name="analyst",
            name="transfer_to_analyst",
            description="Sub-assistant: Analyst",
        )
        supervisor_ai_message = AIMessage(content="Delegating to analyst")
        supervisor_ai_message.tool_calls = [
            {
                "name": "transfer_to_analyst",
                "args": {"task": "Analyze this repository"},
                "id": "call-123",
                "type": "tool_call",
            }
        ]
        original_messages = [
            HumanMessage(content="Original user request"),
            AIMessage(content="Supervisor reasoning before handoff"),
            supervisor_ai_message,
        ]
        state = {"messages": original_messages, "thread_id": "thread-1"}

        handoff_result = handoff_tool.func(task="Analyze this repository", state=state, tool_call_id="call-123")
        parent_visible_messages = [
            *handoff_result.update["messages"],
            AIMessage(content="Final analyst answer"),
        ]

        assert parent_visible_messages[: len(original_messages)] == original_messages
        assert isinstance(parent_visible_messages[len(original_messages)], ToolMessage)
        assert parent_visible_messages[len(original_messages)].response_metadata == {
            METADATA_KEY_HANDOFF_DESTINATION: "analyst"
        }
        assert parent_visible_messages[len(original_messages)].content == "Analyze this repository"
        assert parent_visible_messages[-1].content == "Final analyst answer"
        assert all(message.content != "Intermediate analyst thought" for message in parent_visible_messages)
