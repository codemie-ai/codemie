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

"""MCP server instructions reach the assistant system prompt."""

from unittest.mock import MagicMock

import pytest

from codemie.service.assistant_service import AssistantService
from codemie.service.mcp.server_instructions import MCPServerInstructions, instructions_for_tools
from codemie.service.tools.agent_tools_result import AgentToolsResult

INSTRUCTIONS = "Always call the tool with sysparm_help=true before first use."
BASE_PROMPT = "You are a helpful assistant."


def _record(server_name: str, text: str) -> MCPServerInstructions:
    return MCPServerInstructions(server_name=server_name, instructions=text)


@pytest.fixture
def prompt_inputs(monkeypatch):
    monkeypatch.setattr(AssistantService, "get_system_prompt", classmethod(lambda cls, *a, **kw: BASE_PROMPT))
    assistant = MagicMock()
    assistant.skill_ids = None
    assistant.interactive_enabled = False
    user = MagicMock()
    user.id = "user-1"
    user.username = "user@example.com"
    request = MagicMock()
    request.output_schema = None
    request.conversation_id = "conversation-1"
    return assistant, user, request


def test_instructions_are_appended_to_the_prompt(prompt_inputs):
    assistant, user, request = prompt_inputs
    entries = [_record("Support Portal", INSTRUCTIONS)]

    prompt = AssistantService._prepare_system_prompt(assistant, user, request, mcp_instructions=entries)

    assert prompt.startswith(BASE_PROMPT)
    assert INSTRUCTIONS in prompt
    assert "Support Portal" in prompt


def test_prompt_is_unchanged_when_no_server_declared_instructions(prompt_inputs):
    assistant, user, request = prompt_inputs

    assert AssistantService._prepare_system_prompt(assistant, user, request) == BASE_PROMPT
    assert AssistantService._prepare_system_prompt(assistant, user, request, mcp_instructions=[]) == BASE_PROMPT


def test_instructions_from_several_servers_all_reach_the_prompt(prompt_inputs):
    assistant, user, request = prompt_inputs
    entries = [_record("first", "alpha guidance"), _record("second", "beta guidance")]

    prompt = AssistantService._prepare_system_prompt(assistant, user, request, mcp_instructions=entries)

    assert "alpha guidance" in prompt
    assert "beta guidance" in prompt


def test_separate_builds_never_share_instructions(prompt_inputs):
    assistant, user, request = prompt_inputs
    first_build = [_record("Support Portal", INSTRUCTIONS)]

    with_instructions = AssistantService._prepare_system_prompt(assistant, user, request, mcp_instructions=first_build)
    without_instructions = AssistantService._prepare_system_prompt(assistant, user, request, mcp_instructions=[])

    assert INSTRUCTIONS in with_instructions
    assert without_instructions == BASE_PROMPT
    assert first_build == [_record("Support Portal", INSTRUCTIONS)]


def test_disabled_feature_leaves_the_prompt_unchanged(prompt_inputs, monkeypatch):
    assistant, user, request = prompt_inputs
    disabled = MagicMock()
    disabled.is_feature_enabled.return_value = False
    monkeypatch.setattr("codemie.service.assistant_service.customer_config", disabled)

    prompt = AssistantService._prepare_system_prompt(
        assistant, user, request, mcp_instructions=[_record("Support Portal", INSTRUCTIONS)]
    )

    assert prompt == BASE_PROMPT


# --- Both execution paths: the server's instructions must reach the prompt the agent is built with ---


def _load_and_build_prompt(assistant, prompt_inputs, monkeypatch):
    """Drive the real chain: merge servers -> load tools -> assemble the prompt."""
    from unittest.mock import AsyncMock

    from codemie.service.mcp.client import MCPConnectClient
    from codemie.service.mcp.models import MCPListToolsResponse, MCPToolDefinition
    from codemie.service.mcp.toolkit_service import MCPToolkitService
    from codemie.service.tools.toolkit_service import ToolkitService

    mock_client = MagicMock(spec=MCPConnectClient)
    mock_client.base_url = "http://mock-mcp-connect"
    mock_client.list_tools_with_instructions = AsyncMock(
        return_value=MCPListToolsResponse(
            tools=[
                MCPToolDefinition(name="echo", description="Echo", inputSchema={"type": "object", "properties": {}})
            ],
            instructions=INSTRUCTIONS,
        )
    )
    monkeypatch.setattr(MCPToolkitService, "_instances_cache", {}, raising=False)
    monkeypatch.setattr(MCPToolkitService, "_instance", None, raising=False)
    MCPToolkitService.init_singleton(mock_client)
    MCPToolkitService.get_instance().toolkit_factory._toolkit_cache.clear()

    merged = ToolkitService._merge_skill_mcp_servers(assistant)
    loaded = MCPToolkitService.get_mcp_server_tools_with_instructions(merged)
    mcp_instructions = instructions_for_tools(loaded.mcp_instructions, loaded.tools)

    _, user, request = prompt_inputs
    return AssistantService._prepare_system_prompt(assistant, user, request, mcp_instructions=mcp_instructions)


def _mcp_server(name):
    from codemie.rest_api.models.assistant import MCPServerDetails
    from codemie.service.mcp.models import MCPServerConfig

    return MCPServerDetails(
        name=name,
        enabled=True,
        config=MCPServerConfig(command="uvx", args=["example-server"], env={}),
    )


def test_assistant_attached_server_reaches_the_prompt(prompt_inputs, monkeypatch):
    assistant, _, _ = prompt_inputs
    assistant.skill_ids = None
    assistant.mcp_servers = [_mcp_server("Assistant Server")]

    prompt = _load_and_build_prompt(assistant, prompt_inputs, monkeypatch)

    assert INSTRUCTIONS in prompt
    assert "Assistant Server" in prompt


def test_skill_attached_server_reaches_the_prompt(prompt_inputs, monkeypatch):
    """Skills have no prompt builder of their own; they ride the assistant's."""
    from codemie.service.skills.skill_contributions import SkillContributionsResolver

    assistant, _, _ = prompt_inputs
    assistant.skill_ids = ["skill-1"]
    assistant.mcp_servers = []
    assistant.name = "assistant-under-test"

    skill = MagicMock()
    skill.name = "support-skill"
    skill.mcp_servers = [_mcp_server("Skill Server")]
    monkeypatch.setattr(
        SkillContributionsResolver,
        "resolve_skills_for_assistant",
        classmethod(lambda cls, _assistant: [skill]),
    )

    prompt = _load_and_build_prompt(assistant, prompt_inputs, monkeypatch)

    assert INSTRUCTIONS in prompt
    assert "Skill Server" in prompt


def test_workflow_prompt_receives_the_build_instructions(monkeypatch):
    monkeypatch.setattr(AssistantService, "get_system_prompt", classmethod(lambda cls, *a, **kw: BASE_PROMPT))
    workflow_assistant = MagicMock()
    workflow_assistant.system_prompt = None
    assistant = MagicMock()
    assistant.skill_ids = None
    user = MagicMock()
    user.id = "user-1"
    user.full_name = "User One"

    prompt, _ = AssistantService._prepare_workflow_system_prompt(
        workflow_assistant,
        assistant,
        user,
        None,
        None,
        workspace_notices=None,
        mcp_instructions=[_record("Workflow Server", INSTRUCTIONS)],
    )

    assert prompt.startswith(BASE_PROMPT)
    assert INSTRUCTIONS in prompt


def test_build_agent_for_workflow_hands_loaded_instructions_to_the_prompt(monkeypatch):
    """Workflow assistants get the same first-call guarantee as chat."""
    from codemie.service import assistant_service as assistant_module
    from codemie.service.tools import toolkit_service as tools_module

    captured = {}

    def fake_collect(*args, **kwargs):
        return AgentToolsResult(tools=[], mcp_instructions=[_record("Workflow Server", INSTRUCTIONS)])

    def fake_prepare(cls, workflow_assistant, assistant, user, workflow_state, preprocessor, **kwargs):
        captured["mcp_instructions"] = kwargs["mcp_instructions"]
        return BASE_PROMPT, None

    monkeypatch.setattr(tools_module.ToolkitService, "collect_agent_tools", staticmethod(fake_collect))
    monkeypatch.setattr(
        AssistantService,
        "_load_and_configure_workflow_assistant",
        classmethod(
            lambda cls, *a, **kw: MagicMock(
                id="a1",
                name="assistant",
                is_global=False,
                llm_model_type="m",
                is_react=False,
                toolkits=[],
                context=None,
            )
        ),
    )
    monkeypatch.setattr(AssistantService, "_prepare_workflow_system_prompt", classmethod(fake_prepare))
    monkeypatch.setattr(AssistantService, "prepare_tools_config_from_toolkits", classmethod(lambda cls, *a: None))

    def stop_after_prompt(cls, *args):
        raise _StopBuild

    monkeypatch.setattr(AssistantService, "_select_agent_class_for_workflow", classmethod(stop_after_prompt))
    monkeypatch.setattr(assistant_module, "set_llm_context", lambda *a, **kw: None)
    monkeypatch.setattr(assistant_module, "set_disable_prompt_cache", lambda *a, **kw: None)
    monkeypatch.setattr(assistant_module, "build_unique_file_objects_list", lambda **kw: [])
    monkeypatch.setattr(assistant_module, "prepare_skill_workspace", lambda *a, **kw: None)
    monkeypatch.setattr("codemie.service.tools.oauth_connect_gate.enforce_oauth_connected", lambda *a, **kw: None)

    workflow_assistant = MagicMock()
    workflow_assistant.assistant_id = None
    workflow_assistant.limit_tool_output_tokens = None
    workflow_assistant.exclude_extra_context_tools = False
    user = MagicMock()
    user.id = "user-1"

    with pytest.raises(_StopBuild):
        AssistantService.build_agent_for_workflow(
            user_input="hi",
            user=user,
            request_uuid="req-1",
            workflow_assistant=workflow_assistant,
            execution_id="exec-1",
        )

    assert [entry.instructions for entry in captured["mcp_instructions"]] == [INSTRUCTIONS]


class _StopBuild(Exception):
    """Ends build_agent_for_workflow right after the prompt is assembled."""


def test_build_agent_hands_loaded_instructions_to_the_prompt(monkeypatch):
    """The instructions collected while loading tools are the ones the prompt is assembled with."""
    from codemie.service import assistant_service as assistant_module
    from codemie.service.tools import toolkit_service as tools_module

    captured = {}

    def fake_collect(*args, **kwargs):
        # Stands in for the MCP load: returns the instructions for the tools it keeps.
        return AgentToolsResult(tools=[], mcp_instructions=[_record("Support Portal", INSTRUCTIONS)])

    monkeypatch.setattr(tools_module.ToolkitService, "collect_agent_tools", staticmethod(fake_collect))
    monkeypatch.setattr(assistant_module, "set_llm_context", lambda *a, **kw: None)
    monkeypatch.setattr(assistant_module, "build_unique_file_objects", lambda **kw: {})
    monkeypatch.setattr(AssistantService, "_apply_conversation_runtime_overrides", classmethod(lambda cls, *a: None))
    monkeypatch.setattr(AssistantService, "drop_missing_context", classmethod(lambda cls, *a: None))
    monkeypatch.setattr(
        assistant_module.BedrockOrchestratorService, "is_bedrock_assistant", staticmethod(lambda *a: False)
    )
    monkeypatch.setattr(AssistantService, "get_system_prompt", classmethod(lambda cls, *a, **kw: BASE_PROMPT))
    monkeypatch.setattr(AssistantService, "_prepare_system_prompt", classmethod(_capturing_prepare(captured)))
    monkeypatch.setattr(assistant_module, "resolve_build_policy", lambda *a: (MagicMock(), MagicMock()))
    monkeypatch.setattr(assistant_module, "resolve_assistant_model", lambda *a, **kw: "test-model")
    monkeypatch.setattr(assistant_module.llm_service, "get_react_llms", lambda: [])
    monkeypatch.setattr(AssistantService, "_apply_marketplace_tool_mappings", classmethod(lambda cls, *a: None))
    monkeypatch.setattr(assistant_module.SkillContributionsResolver, "resolve_for_assistant", lambda *a: None)
    monkeypatch.setattr(assistant_module, "prepare_skill_workspace", lambda *a: [])
    monkeypatch.setattr("codemie.service.tools.oauth_connect_gate.enforce_oauth_connected", lambda *a: None)

    assistant = MagicMock()
    assistant.skill_ids = None
    assistant.mcp_servers = []
    user = MagicMock()
    user.id = "user-1"
    user.username = "user@example.com"
    request = MagicMock()
    request.output_schema = None

    # Only the hand-off matters here; let build_agent fail after prompt assembly.
    try:
        AssistantService.build_agent(assistant, request, user, "req-1")
    except Exception as exc:  # noqa: BLE001 - only the hand-off matters here
        captured["error"] = exc

    assert "mcp_instructions" in captured, f"build_agent stopped before prompt assembly: {captured.get('error')!r}"
    assert [entry.instructions for entry in captured["mcp_instructions"]] == [INSTRUCTIONS]


def _capturing_prepare(captured):
    """Record the instructions build_agent passes to prompt assembly."""

    def _prepare(cls, assistant, user, request, thread_generator=None, workspace_notices=None, mcp_instructions=None):
        captured["mcp_instructions"] = list(mcp_instructions or [])
        return BASE_PROMPT

    return _prepare
