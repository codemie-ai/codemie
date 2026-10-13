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

"""prepare_skill_workspace seeds an assistant's skill files before its tools are built."""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.service import skill_workspace_preparation as module
from codemie.service.skill_workspace_preparation import SETUP_FAILED_NOTICE, prepare_skill_workspace
from codemie.service.tools.toolkit_service import ToolkitService
from codemie_tools.data_management.workspace.tools_vars import AGENT_WORKSPACE_TOOLKIT, EXECUTE_WORKSPACE_SCRIPT_TOOL

USER = MagicMock(id="u1")


def _toolkits(script_selected: bool = True):
    tools = (
        [SimpleNamespace(name=EXECUTE_WORKSPACE_SCRIPT_TOOL.name)]
        if script_selected
        else [SimpleNamespace(name="read")]
    )
    return [SimpleNamespace(toolkit=AGENT_WORKSPACE_TOOLKIT, tools=tools)]


def _assistant(skill_ids=("sk1", "sk2")):
    return SimpleNamespace(skill_ids=list(skill_ids), name="a")


class _Env:
    """Patches the collaborators of prepare_skill_workspace and exposes them."""

    def __init__(self, stack: ExitStack, *, script_selected=True, switch_on=True):
        self.seeder_cls = stack.enter_context(patch.object(module, "SkillWorkspaceSeeder"))
        self.get_skills = stack.enter_context(patch.object(module.SkillService, "get_skills_by_ids"))
        self.workspace_service = stack.enter_context(patch.object(module, "AgentWorkspaceService"))
        self.workspace_service.return_value.create_workspace.return_value = SimpleNamespace(id="w1")
        stack.enter_context(patch.object(module, "AgentWorkspaceRepository"))
        stack.enter_context(
            patch.object(ToolkitService, "_merge_skill_toolkits", return_value=_toolkits(script_selected))
        )
        stack.enter_context(
            patch.object(module, "resolve_tool_calling_settings", return_value=object() if switch_on else None)
        )


@pytest.fixture
def env():
    with ExitStack() as stack:
        yield _Env(stack)


def test_gate_open_seeds_in_attachment_order_and_returns_the_notices(env):
    skills = [MagicMock(), MagicMock()]
    env.get_skills.return_value = skills
    env.seeder_cls.return_value.ensure_seeded.return_value = ["note"]
    assistant = _assistant()

    notices = prepare_skill_workspace(assistant, SimpleNamespace(conversation_id="c1"), USER)

    assert notices == ["note"]
    env.get_skills.assert_called_once_with(["sk1", "sk2"], USER)
    env.seeder_cls.return_value.ensure_seeded.assert_called_once_with("w1", skills)


@pytest.mark.parametrize("closed", ["no_skills", "no_request", "no_conversation", "tool_not_selected", "switch_off"])
def test_a_closed_gate_returns_nothing_and_touches_nothing(closed):
    with ExitStack() as stack:
        env = _Env(stack, script_selected=closed != "tool_not_selected", switch_on=closed != "switch_off")
        assistant = _assistant(() if closed == "no_skills" else ("sk1",))
        request = (
            None
            if closed == "no_request"
            else SimpleNamespace(conversation_id=None if closed == "no_conversation" else "c1")
        )

        assert prepare_skill_workspace(assistant, request, USER) == []

        env.seeder_cls.assert_not_called()
        env.workspace_service.return_value.create_workspace.assert_not_called()


def test_an_unexpected_error_becomes_one_notice_and_does_not_raise(env):
    env.get_skills.side_effect = RuntimeError("db down")

    notices = prepare_skill_workspace(_assistant(), SimpleNamespace(conversation_id="c1"), USER)

    assert notices == [SETUP_FAILED_NOTICE]


def test_a_gate_that_cannot_be_evaluated_becomes_a_notice_and_seeds_nothing(env):
    with patch.object(ToolkitService, "_merge_skill_toolkits", side_effect=RuntimeError("boom")):
        notices = prepare_skill_workspace(_assistant(), SimpleNamespace(conversation_id="c1"), USER)

    assert notices == [SETUP_FAILED_NOTICE]
    env.seeder_cls.assert_not_called()


def test_workspace_is_resolved_with_the_conversation_id(env):
    env.get_skills.return_value = []
    env.seeder_cls.return_value.ensure_seeded.return_value = []

    prepare_skill_workspace(_assistant(), SimpleNamespace(conversation_id="c1"), USER)

    request_arg = env.workspace_service.return_value.create_workspace.call_args.args[0]
    assert request_arg.conversation_id == "c1"


def test_script_tool_selected_and_seed_warnings_are_gone_from_the_source():
    import pathlib

    hits = [
        str(path)
        for path in pathlib.Path("src").rglob("*.py")
        if "script_tool_selected" in path.read_text() or "_seed_warnings" in path.read_text()
    ]

    assert hits == []


def test_script_step_and_tool_invocation_sites_do_not_prepare_skill_workspace():
    # They build toolkits through get_toolkit_methods() without an assistant's skills, so they intentionally never
    # seed. A workflow gets its skill files through its assistant nodes (build_agent_for_workflow).
    import pathlib

    sites = [
        "src/codemie/workflows/nodes/tool_node.py",
        "src/codemie/service/tools/tool_execution_service.py",
    ]

    assert [site for site in sites if "prepare_skill_workspace" in pathlib.Path(site).read_text()] == []
