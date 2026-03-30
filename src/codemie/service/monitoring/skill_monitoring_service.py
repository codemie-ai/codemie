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

"""Skill monitoring service for tracking skill usage and operations."""

from typing import TYPE_CHECKING

from codemie.core.dependecies import get_current_project
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import (
    SKILL_MANAGEMENT_METRIC,
    SKILL_ATTACHED_METRIC,
    SKILL_TOOL_INVOKED_METRIC,
    SKILL_EXPORTED_METRIC,
    MetricsAttributes,
)

if TYPE_CHECKING:
    from codemie.rest_api.models.skill import Skill
    from codemie.rest_api.security.user import User


class SkillMonitoringService(BaseMonitoringService):
    """Service for monitoring skill operations and usage."""

    @classmethod
    def send_skill_management_metric(
        cls,
        metric_name: str,
        skill: 'Skill',
        success: bool,
        user: 'User',
        additional_attributes: dict | None = None,
    ):
        """
        Send metrics about skill management operations (create, update, delete).

        Args:
            metric_name: Specific operation (e.g., "create_skill", "update_skill", "delete_skill")
            skill: The skill object
            success: Whether the operation was successful
            user: User performing the operation
            additional_attributes: Any additional attributes to include
        """
        attributes = {
            MetricsAttributes.SKILL_ID: skill.id,
            MetricsAttributes.SKILL_NAME: skill.name,
            MetricsAttributes.SKILL_VISIBILITY: skill.visibility.value if skill.visibility else "unknown",
            MetricsAttributes.SKILL_CATEGORIES: ",".join(skill.categories) if skill.categories else "",
            MetricsAttributes.PROJECT: get_current_project(fallback=skill.project),
            MetricsAttributes.USER_ID: user.id,
            MetricsAttributes.USER_NAME: user.name,
            MetricsAttributes.USER_EMAIL: user.username,
        }

        if additional_attributes:
            attributes.update(additional_attributes)

        if success:
            cls.send_count_metric(
                name=f"{SKILL_MANAGEMENT_METRIC}_{metric_name}",
                attributes=attributes,
            )
        else:
            cls.send_count_metric(
                name=f"{SKILL_MANAGEMENT_METRIC}_{metric_name}_error",
                attributes=attributes,
            )

    @classmethod
    def send_skill_attached_metric(
        cls,
        skill: 'Skill',
        assistant_id: str,
        assistant_name: str,
        user: 'User',
        success: bool,
        operation: str = "attach",
        additional_attributes: dict | None = None,
    ):
        """
        Send metrics when a skill is attached/detached to/from an assistant.

        Args:
            skill: The skill being attached/detached
            assistant_id: ID of the assistant
            assistant_name: Name of the assistant
            user: User performing the operation
            success: Whether the operation was successful
            operation: "attach" or "detach"
            additional_attributes: Any additional attributes to include
        """
        attributes = {
            MetricsAttributes.SKILL_ID: skill.id,
            MetricsAttributes.SKILL_NAME: skill.name,
            MetricsAttributes.ASSISTANT_ID: assistant_id,
            MetricsAttributes.ASSISTANT_NAME: assistant_name,
            MetricsAttributes.PROJECT: get_current_project(fallback=skill.project),
            MetricsAttributes.USER_ID: user.id,
            MetricsAttributes.USER_NAME: user.name,
            MetricsAttributes.OPERATION: operation,
        }

        if additional_attributes:
            attributes.update(additional_attributes)

        if success:
            cls.send_count_metric(
                name=SKILL_ATTACHED_METRIC,
                attributes=attributes,
            )
        else:
            cls.send_count_metric(
                name=f"{SKILL_ATTACHED_METRIC}_error",
                attributes=attributes,
            )

    @classmethod
    def send_skill_tool_invoked_metric(
        cls,
        skill_id: str,
        skill_name: str,
        assistant_id: str,
        user_id: str,
        user_name: str,
        project: str,
        success: bool,
        additional_attributes: dict | None = None,
    ):
        """
        Send metrics when SkillTool loads a skill during agent execution.

        Args:
            skill_id: ID of the skill being loaded
            skill_name: Name of the skill
            assistant_id: ID of the assistant using the skill
            user_id: ID of the user
            user_name: Name of the user
            project: Project context
            success: Whether the skill was loaded successfully
            additional_attributes: Any additional attributes to include
        """
        attributes = {
            MetricsAttributes.SKILL_ID: skill_id,
            MetricsAttributes.SKILL_NAME: skill_name,
            MetricsAttributes.ASSISTANT_ID: assistant_id,
            MetricsAttributes.PROJECT: get_current_project(fallback=project),
            MetricsAttributes.USER_ID: user_id,
            MetricsAttributes.USER_NAME: user_name,
        }

        if additional_attributes:
            attributes.update(additional_attributes)

        if success:
            cls.send_count_metric(
                name=SKILL_TOOL_INVOKED_METRIC,
                attributes=attributes,
            )
        else:
            cls.send_count_metric(
                name=f"{SKILL_TOOL_INVOKED_METRIC}_error",
                attributes=attributes,
            )

    @classmethod
    def send_skill_exported_metric(
        cls,
        skill: 'Skill',
        user: 'User',
        success: bool,
        additional_attributes: dict | None = None,
    ):
        """
        Send metrics when a skill is exported.

        Args:
            skill: The skill being exported
            user: User performing the export
            success: Whether the export was successful
            additional_attributes: Any additional attributes to include
        """
        attributes = {
            MetricsAttributes.SKILL_ID: skill.id,
            MetricsAttributes.SKILL_NAME: skill.name,
            MetricsAttributes.PROJECT: get_current_project(fallback=skill.project),
            MetricsAttributes.USER_ID: user.id,
            MetricsAttributes.USER_NAME: user.name,
        }

        if additional_attributes:
            attributes.update(additional_attributes)

        if success:
            cls.send_count_metric(
                name=SKILL_EXPORTED_METRIC,
                attributes=attributes,
            )
        else:
            cls.send_count_metric(
                name=f"{SKILL_EXPORTED_METRIC}_error",
                attributes=attributes,
            )

    @classmethod
    def send_skill_instruction_generation_metric(
        cls,
        success: bool,
        user: 'User',
        mode: str,
        model: str,
        error: str | None = None,
        additional_attributes: dict | None = None,
    ):
        """
        Send metrics when AI-powered skill instruction generation is used.

        Args:
            success: Whether the generation was successful
            user: User requesting the generation
            mode: Generation mode ("generate" or "refine")
            model: LLM model used for generation
            error: Error message if generation failed
            additional_attributes: Any additional attributes to include
        """
        attributes = {
            MetricsAttributes.USER_ID: user.id,
            MetricsAttributes.USER_NAME: user.name,
            MetricsAttributes.USER_EMAIL: user.username,
            MetricsAttributes.PROJECT: get_current_project(
                fallback=user.current_project if hasattr(user, 'current_project') else None
            ),
            "mode": mode,
            "model": model,
        }

        if error:
            attributes["error"] = error

        if additional_attributes:
            attributes.update(additional_attributes)

        metric_name = "skill.instruction.generation" if success else "skill.instruction.generation.error"

        cls.send_count_metric(
            name=metric_name,
            attributes=attributes,
        )
