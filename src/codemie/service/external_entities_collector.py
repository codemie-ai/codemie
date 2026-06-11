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

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from codemie.core.exceptions import NotFoundException
from codemie.core.workflow_models.workflow_models import WorkflowAssistant
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.skill import Skill
from codemie.rest_api.security.user import User
from codemie.service.skill_service import SkillService

if TYPE_CHECKING:
    from codemie.core.workflow_models.workflow_config import WorkflowConfig

_T = TypeVar("_T")


class WorkflowExternalEntitiesCollector:
    """Collects external assistants and skills referenced by a workflow.

    Public interface
    ----------------
    collect_for_workflow — all external assistants and skills reachable from a workflow
    """

    def _fetch_deduped(
        self,
        ids: list[str],
        visited: set[str],
        fetch: Callable[[list[str]], list[_T]],
    ) -> list[_T]:
        new_ids = list(dict.fromkeys(id_ for id_ in ids if id_ not in visited))
        visited.update(new_ids)
        if not new_ids:
            return []
        items = fetch(new_ids)
        if len(items) < len(new_ids):
            returned_ids = {str(item.id) for item in items if getattr(item, "id", None) is not None}
            missing = [id_ for id_ in new_ids if id_ not in returned_ids]
            raise NotFoundException(f"Entities not found: {missing}")
        return items

    def _collect_skills(self, skill_ids: list[str], visited: set[str], user: User) -> list[Skill]:
        return self._fetch_deduped(
            skill_ids or [],
            visited,
            lambda ids: SkillService.get_skills_by_ids(ids, user),
        )

    def _collect_assistants(self, assistant_ids: list[str], visited: set[str], user: User) -> list[Assistant]:
        return self._fetch_deduped(
            assistant_ids,
            visited,
            lambda ids: Assistant.get_by_ids(user, ids, parent_assistant=None),
        )

    def collect_for_workflow(
        self,
        workflow: WorkflowConfig,
        user: User,
    ) -> tuple[list[Assistant], list[Skill]]:
        """Collect all external assistants and skills referenced by the workflow, recursively.

        Traverses:
        - Skill IDs on virtual assistant steps (assistant_id is None)
        - External assistant steps (assistant_id is not None) and their sub-assistants
        - Skill references from every collected assistant

        Raises:
            NotFoundException: if any referenced assistant or skill ID cannot be resolved.
        """
        visited_assistant_ids: set[str] = set()
        visited_skill_ids: set[str] = set()
        collected_assistants: list[Assistant] = []
        collected_skills: list[Skill] = []

        workflow_skill_ids = [
            sid
            for step in (workflow.assistants or [])
            if isinstance(step, WorkflowAssistant) and step.assistant_id is None
            for sid in (step.skill_ids or [])
        ]
        collected_skills.extend(self._collect_skills(workflow_skill_ids, visited_skill_ids, user))

        workflow_assistant_ids = list(
            {
                aid
                for step in (workflow.assistants or [])
                if isinstance(step, WorkflowAssistant)
                for aid in (step.assistant_id,)
                if aid is not None
            }
        )
        self._collect_recursive(
            workflow_assistant_ids,
            user,
            visited_assistant_ids,
            visited_skill_ids,
            collected_assistants,
            collected_skills,
        )

        return collected_assistants, collected_skills

    _MAX_RECURSION_DEPTH = 20

    def _collect_recursive(
        self,
        assistant_ids: list[str],
        user: User,
        visited_assistant_ids: set[str],
        visited_skill_ids: set[str],
        collected_assistants: list[Assistant],
        collected_skills: list[Skill],
        _depth: int = 0,
    ) -> None:
        """Recursively load assistants and their sub-assistants and skills, deduplicating by ID.

        Raises:
            NotFoundException: if any referenced assistant or skill ID cannot be resolved.
            ValueError: if nesting depth exceeds _MAX_RECURSION_DEPTH.
        """
        if _depth >= self._MAX_RECURSION_DEPTH:
            raise ValueError(
                f"Assistant nesting depth exceeds the maximum allowed depth of {self._MAX_RECURSION_DEPTH}"
            )

        assistants = self._collect_assistants(assistant_ids, visited_assistant_ids, user)
        collected_assistants.extend(assistants)

        for assistant in assistants:
            collected_skills.extend(self._collect_skills(assistant.skill_ids or [], visited_skill_ids, user))
            self._collect_recursive(
                assistant.assistant_ids or [],
                user,
                visited_assistant_ids,
                visited_skill_ids,
                collected_assistants,
                collected_skills,
                _depth=_depth + 1,
            )
