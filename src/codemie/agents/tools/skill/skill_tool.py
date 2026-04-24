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

"""SkillTool - loads skills into agent context on-demand.

This tool is ONLY added to agents when the assistant has attached skills.
"""

from typing import Any, Type

from codemie_tools.base.codemie_tool import CodeMieTool
from pydantic import BaseModel, Field

from codemie.configs import logger
from codemie.rest_api.security.user import User
from codemie.service.monitoring.skill_monitoring_service import SkillMonitoringService

_DESCRIPTION_TEMPLATE = """Load a skill to get specialized domain knowledge and instructions.

Skills provide context-specific best practices, code examples, and step-by-step guidance.
For complex tasks, you can load multiple skills one by one as needed — each call loads a
single skill, so invoke this tool multiple times when the task requires several relevant skills fron list of available.

Available skills:
{skill_list}

Use this tool when you need expertise in a specific domain/tasks/purposes based on list of available skills.
Example: skill: 'api-testing' to get REST API testing patterns.
For a task requiring both API testing and code review, load each skill separately in sequence.
"""

_FILE_DESCRIPTION_TEMPLATE = """Load a bundled companion file for an attached skill.

Use this after the main skill tool when the loaded skill advertises companion files such as
references or assets. Provide both the skill name and the relative file path to load only the
single file you need.

Available skills:
{skill_list}
"""

_NO_SKILLS_MESSAGE = "No skills attached to this assistant."

_SKILL_OUTPUT_TEMPLATE = """<skill_content name="{name}">
# Skill: {name}

{content}
{companion_files_block}
</skill_content>"""

_SKILL_FILE_OUTPUT_TEMPLATE = """<skill_file skill="{skill}" path="{path}" kind="{kind}" mime_type="{mime_type}" encoding="{encoding}">
{content}
</skill_file>"""


class SkillInput(BaseModel):
    """Input schema for SkillTool."""

    skill: str = Field(description="The name of the skill to load. Example: 'api-testing', 'code-review'")


class SkillCompanionFileInput(BaseModel):
    """Input schema for SkillCompanionFileTool."""

    skill: str = Field(description="The name of the skill that owns the companion file")
    path: str = Field(description="Relative companion file path, e.g. 'references/writing-guidelines.md'")


class SkillTool(CodeMieTool):
    """Tool that loads skills into agent context on-demand.

    This tool is ONLY added to agents when the assistant has attached skills.
    It provides domain-specific knowledge, best practices, and instructions
    that help the agent complete specialized tasks.
    """

    name: str = "skill"
    description: str = _DESCRIPTION_TEMPLATE.format(skill_list=_NO_SKILLS_MESSAGE)
    args_schema: Type[BaseModel] = SkillInput

    user: User
    project: str = ""
    assistant_id: str = ""
    available_skills: list[dict[str, str]] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True

    def model_post_init(self, __context: Any) -> None:
        """Load available skills after model initialization."""
        super().model_post_init(__context)
        self._load_available_skills()

    def _load_available_skills(self) -> None:
        """Load skills from skill_ids stored on the assistant and update the description."""
        skill_ids = [s["id"] for s in self.available_skills] if self.available_skills else []
        if not skill_ids:
            self._update_description()
            return

        try:
            from codemie.service.skill_service import SkillService

            skills = SkillService.get_skills_by_ids(skill_ids, self.user)
            self.available_skills = [{"id": s.id, "name": s.name, "description": s.description} for s in skills]
        except Exception as e:
            logger.error(f"Error loading skills for SkillTool: {e}")
            self.available_skills = []

        self._update_description()

    def _update_description(self) -> None:
        """Update tool description with the list of available skills."""
        if self.available_skills:
            skill_list = "\n".join(f"- {s['name']}: {s['description']}" for s in self.available_skills)
        else:
            skill_list = _NO_SKILLS_MESSAGE

        self.description = _DESCRIPTION_TEMPLATE.format(skill_list=skill_list)

    def _find_skill(self, skill_name: str) -> dict[str, str] | None:
        """Find a skill by name in the available skills list."""
        return next((s for s in self.available_skills if s["name"] == skill_name), None)

    @staticmethod
    def _format_companion_files(skill_obj: Any) -> str:
        """Format advertised companion files for the agent without loading file bodies."""
        companion_files = getattr(skill_obj, "get_companion_file_metadata", lambda: [])()
        if not companion_files:
            return ""

        formatted_files = "\n".join(
            f"- {item.path} ({item.kind.value}, {item.mime_type}, {item.encoding})" for item in companion_files
        )
        return f"\n<skill_files>\n{formatted_files}\n</skill_files>"

    def _send_metric(self, skill_id: str, skill_name: str, success: bool, error: str | None = None) -> None:
        """Send skill invocation metric."""
        SkillMonitoringService.send_skill_tool_invoked_metric(
            skill_id=skill_id,
            skill_name=skill_name,
            assistant_id=self.assistant_id,
            user_id=self.user.id,
            user_name=self.user.name,
            project=self.project,
            success=success,
            additional_attributes={"error": error[:100]} if error else None,
        )

    def execute(self, skill: str) -> str:
        """Load skill content by name.

        Args:
            skill: Name of the skill to load

        Returns:
            Formatted skill content or error message
        """
        try:
            skill_info = self._find_skill(skill)
            if not skill_info:
                available = [s["name"] for s in self.available_skills]
                return f"Error: Skill '{skill}' not found. Available skills: {available}"

            from codemie.repository.skill_repository import SkillRepository

            skill_obj = SkillRepository.get_by_id(skill_info["id"])
            if not skill_obj:
                return f"Error: Could not load skill '{skill}'"

            output = _SKILL_OUTPUT_TEMPLATE.format(
                name=skill_obj.name,
                content=skill_obj.content,
                companion_files_block=self._format_companion_files(skill_obj),
            )
            logger.info(f"Loaded skill '{skill}' for user '{self.user.name}' in assistant '{self.assistant_id}'")
            self._send_metric(skill_id=skill_obj.id, skill_name=skill_obj.name, success=True)
            return output

        except Exception as e:
            logger.error(f"Error loading skill '{skill}': {e}")
            if skill:
                self._send_metric(skill_id="", skill_name=skill, success=False, error=str(e))
            return f"Error loading skill '{skill}': {e}"

    async def _arun(self, skill: str) -> str:
        """Load skill content asynchronously (delegates to sync implementation)."""
        return self._run(skill)


class SkillCompanionFileTool(SkillTool):
    """Tool that loads a single bundled companion file for an attached skill."""

    name: str = "skill_file"
    description: str = _FILE_DESCRIPTION_TEMPLATE.format(skill_list=_NO_SKILLS_MESSAGE)
    args_schema: Type[BaseModel] = SkillCompanionFileInput

    def _update_description(self) -> None:
        """Update tool description with the list of available skills."""
        if self.available_skills:
            skill_list = "\n".join(f"- {s['name']}: {s['description']}" for s in self.available_skills)
        else:
            skill_list = _NO_SKILLS_MESSAGE

        self.description = _FILE_DESCRIPTION_TEMPLATE.format(skill_list=skill_list)

    def execute(self, skill: str, path: str) -> str:
        """Load one bundled companion file by relative path."""
        try:
            skill_info = self._find_skill(skill)
            if not skill_info:
                available = [s["name"] for s in self.available_skills]
                return f"Error: Skill '{skill}' not found. Available skills: {available}"

            from codemie.service.skill_service import SkillService

            companion_file = SkillService.get_companion_file(skill_info["id"], path, self.user)
            logger.info(
                f"Loaded companion file '{companion_file.path}' for skill '{skill}' and user '{self.user.name}'"
            )
            return _SKILL_FILE_OUTPUT_TEMPLATE.format(
                skill=skill,
                path=companion_file.path,
                kind=companion_file.kind.value,
                mime_type=companion_file.mime_type,
                encoding=companion_file.encoding,
                content=companion_file.content,
            )
        except Exception as e:
            logger.error(f"Error loading companion file '{path}' for skill '{skill}': {e}")
            return f"Error loading companion file '{path}' for skill '{skill}': {e}"

    async def _arun(self, skill: str, path: str) -> str:
        """Load one bundled companion file asynchronously (delegates to sync implementation)."""
        return self._run(skill=skill, path=path)


def create_skill_tool_if_needed(
    assistant_config: Any,
    user: User,
) -> SkillTool | None:
    """Create SkillTool only if the assistant has attached skills.

    Args:
        assistant_config: Assistant configuration object with skill_ids
        user: The current user

    Returns:
        SkillTool instance if assistant has skills, None otherwise
    """
    skill_ids = getattr(assistant_config, "skill_ids", None) or []
    if not skill_ids:
        return None

    project = getattr(assistant_config, "project", "demo")
    assistant_id = getattr(assistant_config, "id", "")

    return SkillTool(
        user=user,
        project=project,
        assistant_id=assistant_id,
        available_skills=[{"id": sid, "name": "", "description": ""} for sid in skill_ids],
    )


def create_skill_companion_file_tool_if_needed(
    assistant_config: Any,
    user: User,
) -> SkillCompanionFileTool | None:
    """Create companion-file tool only if the assistant has attached skills."""
    skill_ids = getattr(assistant_config, "skill_ids", None) or []
    if not skill_ids:
        return None

    project = getattr(assistant_config, "project", "demo")
    assistant_id = getattr(assistant_config, "id", "")

    return SkillCompanionFileTool(
        user=user,
        project=project,
        assistant_id=assistant_id,
        available_skills=[{"id": sid, "name": "", "description": ""} for sid in skill_ids],
    )
