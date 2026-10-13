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

"""Unit tests for SkillTool: name-collision handling and the workspace path hint."""

import inspect
from types import SimpleNamespace
from unittest.mock import patch


import codemie.agents.tools.skill.skill_tool as skill_tool_module
from codemie.agents.tools.skill.skill_tool import SkillTool, create_skill_tool_if_needed
from codemie.rest_api.security.user import User


def _make_tool() -> SkillTool:
    user = User(id="user-1", username="user", name="User", project_names=["project-a"], admin_project_names=[])
    with patch.object(SkillTool, "_load_available_skills"):
        tool = SkillTool(user=user, project="project-a", assistant_id="assistant-1", available_skills=[])
    return tool


class TestFindSkillCollision:
    def test_returns_first_match_in_attachment_order_on_name_collision(self):
        tool = _make_tool()
        tool.available_skills = [
            {"id": "id-1", "name": "dup", "description": "first"},
            {"id": "id-2", "name": "dup", "description": "second"},
        ]

        result = tool._find_skill("dup")

        assert result["id"] == "id-1"

    def test_logs_a_warning_on_name_collision(self):
        tool = _make_tool()
        tool.available_skills = [
            {"id": "id-1", "name": "dup", "description": "first"},
            {"id": "id-2", "name": "dup", "description": "second"},
        ]

        with patch("codemie.agents.tools.skill.skill_tool.logger") as mock_logger:
            tool._find_skill("dup")

        mock_logger.warning.assert_called_once()

    def test_no_warning_when_no_collision(self):
        tool = _make_tool()
        tool.available_skills = [{"id": "id-1", "name": "unique", "description": "only"}]

        with patch("codemie.agents.tools.skill.skill_tool.logger") as mock_logger:
            result = tool._find_skill("unique")

        assert result["id"] == "id-1"
        mock_logger.warning.assert_not_called()

    def test_returns_none_when_not_found(self):
        tool = _make_tool()
        tool.available_skills = []

        assert tool._find_skill("missing") is None


class TestWorkspaceHint:
    """The hint comes from skill_location_hint, looked up at execute time; the tool never touches the repository."""

    def _build_tool(self, conversation_id="c-1"):
        user = User(id="user-1", username="user", name="User", project_names=["project-a"], admin_project_names=[])
        with patch.object(SkillTool, "_load_available_skills"):
            tool = create_skill_tool_if_needed(
                SimpleNamespace(skill_ids=["s-1"], project="project-a", id="assistant-1"),
                user,
                conversation_id=conversation_id,
            )
        tool.available_skills = [{"id": "s-1", "name": "new-name", "description": "d"}]
        return tool

    def _execute(self, tool, hint="Files of this skill are in the workspace at skills/old-name/."):
        skill = SimpleNamespace(id="s-1", name="new-name", content="body")
        with (
            patch("codemie.repository.skill_repository.SkillRepository.get_by_id", return_value=skill),
            patch.object(SkillTool, "_send_metric"),
            patch("codemie.agents.tools.skill.skill_tool.skill_location_hint") as hint_fn,
        ):
            hint_fn.return_value = hint
            output = tool.execute("new-name")
        return output, hint_fn

    def test_the_service_hint_is_appended_to_the_skill_output(self):
        tool = self._build_tool()

        output, hint_fn = self._execute(tool)

        assert "Files of this skill are in the workspace at skills/old-name/." in output
        hint_fn.assert_called_once_with("c-1", tool.user, "s-1")

    def test_no_hint_when_the_service_has_none(self):
        output, _ = self._execute(self._build_tool(), hint=None)

        assert "workspace" not in output

    def test_no_lookup_without_a_conversation(self):
        output, hint_fn = self._execute(self._build_tool(conversation_id=None))

        assert "workspace" not in output
        hint_fn.assert_not_called()

    def test_a_failing_lookup_does_not_break_loading_the_skill(self):
        tool = self._build_tool()
        skill = SimpleNamespace(id="s-1", name="new-name", content="body")
        with (
            patch("codemie.repository.skill_repository.SkillRepository.get_by_id", return_value=skill),
            patch.object(SkillTool, "_send_metric"),
            patch("codemie.agents.tools.skill.skill_tool.skill_location_hint") as hint_fn,
        ):
            hint_fn.side_effect = RuntimeError("db down")
            output = tool.execute("new-name")

        assert "body" in output


def test_the_skill_tool_module_does_not_reach_into_the_repository_or_the_seeder():
    source = inspect.getsource(skill_tool_module)

    assert "agent_workspace_repository" not in source
    assert "skill_workspace_seeder" not in source
