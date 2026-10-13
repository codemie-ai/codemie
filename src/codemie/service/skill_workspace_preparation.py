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

"""Make an assistant's skill files available in a conversation's workspace before its tools are built."""

from __future__ import annotations

from codemie_tools.data_management.workspace.tools_vars import AGENT_WORKSPACE_TOOLKIT, EXECUTE_WORKSPACE_SCRIPT_TOOL

from codemie.configs import logger
from codemie.repository.agent_workspace_repository import AgentWorkspaceRepository
from codemie.rest_api.models.agent_workspace import CreateAgentWorkspaceRequest
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant, ToolKitDetails
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.skill_service import SkillService
from codemie.service.skill_workspace_seeder import SkillWorkspaceSeeder
from codemie.service.tools.toolkit_service import ToolkitService
from codemie.service.workspace_script_bridge import resolve_tool_calling_settings

SETUP_FAILED_NOTICE = (
    "Skill files could not be set up in the workspace due to an unexpected error. Companion files may be unavailable."
)


def _is_tool_selected(toolkits: list[ToolKitDetails] | None, toolkit_name: str, tool_name: str) -> bool:
    """True when ``toolkit_name`` is among ``toolkits`` with ``tool_name`` selected in it."""
    for toolkit in toolkits or []:
        if str(toolkit.toolkit) == toolkit_name and any(tool.name == tool_name for tool in toolkit.tools or []):
            return True
    return False


def _gate_is_open(assistant: Assistant) -> bool:
    """The script tool is selected (directly or through a skill) and the script bridge is enabled."""
    toolkits = ToolkitService._merge_skill_toolkits(assistant)
    has_script_tool = _is_tool_selected(toolkits, AGENT_WORKSPACE_TOOLKIT, EXECUTE_WORKSPACE_SCRIPT_TOOL.name)
    return has_script_tool and resolve_tool_calling_settings() is not None


def prepare_skill_workspace(assistant: Assistant, request: AssistantChatRequest | None, user: User) -> list[str]:
    """Make the assistant's skill files available in the conversation's workspace; return notices for the model.

    Called by the agent builders (chat and workflow assistants alike) right before the tools are built, so
    the side effect does not hide in a toolkit getter. It needs: attached skills, a conversation, the
    script-execution tool among the assistant's selected tools, and the script bridge enabled.

    The other callers of ``ToolkitService.get_toolkit_methods()`` (script step, tool invocation) have
    no assistant skill context and intentionally do not seed. Never raises.
    """
    skill_ids = getattr(assistant, "skill_ids", None) or []
    conversation_id = getattr(request, "conversation_id", None) if request is not None else None
    if not skill_ids or not conversation_id:
        return []
    try:
        if not _gate_is_open(assistant):
            return []
        workspace = AgentWorkspaceService().create_workspace(
            CreateAgentWorkspaceRequest(conversation_id=conversation_id), user
        )
        skills = SkillService.get_skills_by_ids(skill_ids, user)
        return SkillWorkspaceSeeder(AgentWorkspaceRepository()).ensure_seeded(workspace.id, skills)
    except Exception as exc:  # noqa: BLE001 - preparing skill files must never fail building the agent
        logger.error(f"Failed to prepare skill files for conversation '{conversation_id}': {exc}")
        return [SETUP_FAILED_NOTICE]
