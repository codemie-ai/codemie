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

"""Makes skill bundle companion files available in an agent workspace by pointing at the skill's stored blobs."""

from __future__ import annotations

from typing import Protocol

from codemie.configs import logger
from codemie.repository.agent_workspace_repository import SeedFileReference, SeedOutcome
from codemie.rest_api.models.agent_workspace import SOURCE_KIND_SKILL
from codemie.rest_api.models.skill import Skill
from codemie.service.file_service.workspace_path_rules import skill_workspace_folder
from codemie.service.skill_bundle_seed_policy import SeedLimits, bundle_seed_skip_messages, plan_bundle_seed


class SkillSeedStore(Protocol):
    def seed_references(
        self, workspace_id: str, source_kind: str, source_id: str, folder: str, files: list[SeedFileReference]
    ) -> SeedOutcome: ...


class SkillWorkspaceSeeder:
    """Seeds each skill once per workspace; the first skill to claim a folder keeps it.

    The set of files is fixed at the first seeding: files added to a skill later do not appear.
    """

    def __init__(self, store: SkillSeedStore):
        self._store = store

    def ensure_seeded(self, workspace_id: str, skills: list[Skill]) -> list[str]:
        """Seed ``skills`` (already in attachment order) and return notices for the model."""
        notices: list[str] = []
        for skill in skills:
            try:
                notices.extend(self._seed_one(workspace_id, skill))
            except Exception as exc:  # noqa: BLE001 - seeding must never break building the agent
                logger.error(f"Failed to seed skill '{skill.name}' into workspace '{workspace_id}': {exc}")
                notices.append(
                    f"Skill '{skill.name}' could not be set up in the workspace due to an unexpected error. "
                    "Its companion files may be unavailable."
                )
        return notices

    def _seed_one(self, workspace_id: str, skill: Skill) -> list[str]:
        refs = skill.get_companion_file_refs()
        if not refs:
            return []
        folder = skill_workspace_folder(skill.name, skill.id)
        plan = plan_bundle_seed(refs, folder, SeedLimits.current())
        skip_notices = [f"Skill '{skill.name}': {message}" for message in bundle_seed_skip_messages(plan)]
        if plan.skipped:
            reasons = ", ".join(
                f"{reason}={plan.skipped_count(reason)}" for reason in dict.fromkeys(r for _, r in plan.skipped)
            )
            logger.warning(
                f"Skill '{skill.name}' ({folder}/): {len(plan.skipped)} companion file(s) not seeded: {reasons}"
            )
        if not plan.to_seed:
            # Nothing to reference: record no folder owner, so the notice repeats and a later fix is not frozen out.
            return skip_notices

        files = [
            SeedFileReference(
                path=f"{folder}/{ref.path}",
                blob_name=ref.storage_key,
                mime_type=ref.mime_type,
                checksum=ref.checksum,
                size=ref.size_bytes,
            )
            for ref in plan.to_seed
        ]
        outcome = self._store.seed_references(workspace_id, SOURCE_KIND_SKILL, skill.id, folder, files)
        if outcome is SeedOutcome.COLLISION:
            logger.warning(f"Skill '{skill.name}' not seeded into workspace '{workspace_id}': folder {folder} is taken")
            return [
                f"Skill '{skill.name}' was not set up in the workspace because another skill already owns the folder "
                f"{folder}/. Rename one of the skills to make its companion files available."
            ]
        if outcome is SeedOutcome.ALREADY_SEEDED:
            return []
        return skip_notices
