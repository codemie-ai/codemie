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

"""EPMCDME-16835 AC4: large-description Story and sub-task creation through a real react graph.

The graph is LangGraph's real `create_react_agent` with the real `GenericJiraIssueTool` (only the
Jira HTTP client is mocked) and the real truncation pre-model hook. A fake chat model plays the
Jira assistant: it creates a ~12 KB Story, then ~11 KB sub-tasks. That a router forwards a limit
big enough for these payloads is covered by
tests/enterprise/litellm/test_llm_factory.py::TestMaxOutputTokensForwarding (max_tokens=64000).
"""

import json
from unittest.mock import MagicMock, Mock, patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt import create_react_agent
from pydantic import Field

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.agents.supervisor.pre_model_hooks import _build_truncation_abort_pre_model_hook
from codemie.configs import config as global_config
from codemie.core.exceptions import TokenLimitExceededException
from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
from tests.codemie.agents.test_langgraph_truncation import create_mock_agent

STORY_KEY = "EPMCDME-16793"
SECTIONS = ("Scope", "Verification", "Out of Scope", "Decisions", "Open Questions")


class CountingFakeChatModel(FakeMessagesListChatModel):
    call_count: int = Field(default=0)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.call_count += 1
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _description(title: str, size: int) -> str:
    """Jira wiki text with every Story section, padded to about `size` bytes (non-ASCII included)."""
    body = "\n".join(f"h2. {section}\n* {title}: item — перевірка {section.lower()}" for section in SECTIONS)
    filler = " Detail line for the acceptance flow — деталі."
    while len(body.encode()) < size:
        body += filler
    return body


STORY_DESCRIPTION = _description("Story", 12 * 1024)
SUBTASK_DESCRIPTIONS = [_description(f"Sub-task {n}", 11 * 1024) for n in (1, 2, 3)]


def _create_call(call_id: str, fields: dict) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": call_id,
                "name": "generic_jira_tool",
                "args": {
                    "method": "POST",
                    "relative_url": "/rest/api/2/issue",
                    "params": json.dumps({"fields": fields}),
                },
            }
        ],
    )


def _story_call() -> AIMessage:
    return _create_call(
        "call_story",
        {
            "project": {"key": "EPMCDME"},
            "issuetype": {"name": "Story"},
            "summary": "Story",
            "description": STORY_DESCRIPTION,
        },
    )


def _subtask_fields(n: int) -> dict:
    return {
        "project": {"key": "EPMCDME"},
        "parent": {"key": STORY_KEY},
        "issuetype": {"name": "Sub-task"},
        "summary": f"Sub-task {n}",
        "description": SUBTASK_DESCRIPTIONS[n - 1],
    }


def _truncated_subtask_call() -> AIMessage:
    full = json.dumps({"fields": _subtask_fields(1)})
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call_subtask_1",
                "name": "generic_jira_tool",
                "args": {"method": "POST", "relative_url": "/rest/api/2/issue", "params": full[: len(full) // 2]},
            }
        ],
        response_metadata={"finish_reason": "length"},
    )


@pytest.fixture
def jira_client():
    """Mocked Jira HTTP client; every successful create returns the next issue key."""
    with (
        patch("codemie_tools.core.project_management.jira.tools.Jira") as jira_class,
        patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"),
        patch("codemie_tools.core.project_management.jira.utils.validate_jira_creds"),
    ):
        client = MagicMock()
        client.url = "https://jira.example.com"
        jira_class.return_value = client
        counter = iter(range(16793, 16900))

        def request(method, path, data=None, **kwargs):
            response = MagicMock(status_code=201, reason="Created")
            response.text = json.dumps({"id": "1", "key": f"EPMCDME-{next(counter)}"})
            return response

        client.request.side_effect = request
        yield client


def _graph(model, jira_client):
    tool = GenericJiraIssueTool(config=JiraConfig(url="https://jira.example.com", token="t"))
    tool.jira = jira_client
    hook_agent = object.__new__(LangGraphAgent)
    hook_agent.agent_name = "jira_assistant"
    hook_agent.llm_model = "claude-router-standard"
    hook_agent._callback_bridge = Mock()
    hook_agent.thread_generator = None
    return create_react_agent(
        model=model, tools=[tool], pre_model_hook=_build_truncation_abort_pre_model_hook(hook_agent)
    )


def _sent_descriptions(jira_client) -> list[str]:
    return [call.kwargs["data"]["fields"]["description"] for call in jira_client.request.call_args_list]


def _request() -> dict:
    return {"messages": [HumanMessage(content="Create the Story with all sections and its 3 sub-tasks")]}


def _interrupted_run(jira_client) -> TokenLimitExceededException:
    model = CountingFakeChatModel(
        responses=[_story_call(), _truncated_subtask_call(), AIMessage(content="never reached")]
    )
    with pytest.raises(TokenLimitExceededException) as info:
        _graph(model, jira_client).invoke(_request())
    assert model.call_count == 2
    return info.value


def _user_message(exc: TokenLimitExceededException, report_llm, jira_client) -> str:
    """The agent holds the same real Jira tool, so the report asks that tool which keys its calls wrote."""
    agent = create_mock_agent(llm_model="claude-router-standard")
    agent.extended_error = Mock(return_value=global_config.AGENT_MSG_TOKEN_LIMIT)
    report_llm.bind.return_value = report_llm
    agent._initialize_llm = Mock(return_value=report_llm)
    tool = GenericJiraIssueTool(config=JiraConfig(url="https://jira.example.com", token="t"))
    tool.jira = jira_client
    agent.tools = [tool]
    return agent._get_user_error_message(exc)


class TestLargeTicketCreation:
    def test_story_and_subtasks_are_created_completely_when_nothing_is_cut(self, jira_client):
        model = CountingFakeChatModel(
            responses=[
                _story_call(),
                *[_create_call(f"call_subtask_{n}", _subtask_fields(n)) for n in (1, 2, 3)],
                AIMessage(content="Created EPMCDME-16793 with sub-tasks EPMCDME-16794..16796."),
            ]
        )

        result = _graph(model, jira_client).invoke(_request())

        assert _sent_descriptions(jira_client) == [STORY_DESCRIPTION, *SUBTASK_DESCRIPTIONS]
        assert len(STORY_DESCRIPTION.encode()) >= 12 * 1024
        assert all(len(d.encode()) >= 11 * 1024 for d in SUBTASK_DESCRIPTIONS)
        tool_outputs = [m.content for m in result["messages"] if m.type == "tool"]
        assert [f"EPMCDME-{n}" in out for n, out in zip(range(16793, 16797), tool_outputs)] == [True] * 4
        assert result["messages"][-1].content.startswith("Created EPMCDME-16793")


class TestInterruptedCreationIsReported:
    def test_stop_carries_the_turn_with_the_created_story(self, jira_client):
        exc = _interrupted_run(jira_client)

        assert _sent_descriptions(jira_client) == [STORY_DESCRIPTION]
        assert exc.truncated_message is not None
        assert any(STORY_KEY in str(m.content) for m in exc.turn_messages if m.type == "tool")

    def test_user_gets_limit_message_and_model_report_with_story_key(self, jira_client):
        exc = _interrupted_run(jira_client)
        report_llm = MagicMock()
        report_llm.invoke.return_value = AIMessage(
            content=f"Created {STORY_KEY} with all sections. Sub-tasks 1-3 were not created."
        )

        message = _user_message(exc, report_llm, jira_client)

        assert message.startswith(global_config.AGENT_MSG_TOKEN_LIMIT)
        assert f"Created {STORY_KEY} with all sections. Sub-tasks 1-3 were not created." in message
        prompt = report_llm.invoke.call_args.args[0][1].content
        assert STORY_KEY in prompt
        assert "3 sub-tasks" in prompt

    def test_user_gets_code_built_report_when_report_model_fails(self, jira_client):
        exc = _interrupted_run(jira_client)
        report_llm = MagicMock()
        report_llm.invoke.side_effect = RuntimeError("proxy unavailable")

        message = _user_message(exc, report_llm, jira_client)

        assert message.startswith(global_config.AGENT_MSG_TOKEN_LIMIT)
        assert f"created/updated: {STORY_KEY}" in message
        assert "Interrupted: generic_jira_tool POST /rest/api/2/issue" in message
        # The real tool cannot parse the cut-off JSON, so the interrupted call changed nothing
        assert "the tool rejected the incomplete call" in message
        assert "Later planned steps were not run" in message
