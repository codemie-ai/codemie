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

"""Tell the model where a skill's files are in a conversation's workspace."""

from __future__ import annotations

from codemie.repository.agent_workspace_repository import AgentWorkspaceRepository
from codemie.rest_api.models.agent_workspace import SOURCE_KIND_SKILL
from codemie.rest_api.security.user import User


def skill_location_hint(conversation_id: str, user: User, skill_id: str) -> str | None:
    """Say where a skill's files are in this conversation's workspace, from its folder-owner record.

    The folder comes from the record the seeder wrote, not from the skill's current name, so the hint stays
    correct if the skill is renamed later.
    """
    repository = AgentWorkspaceRepository()
    workspace = repository.get_by_conversation_for_user(conversation_id, user.id)
    if workspace is None:
        return None
    owner = repository.get_folder_owner(workspace.id, SOURCE_KIND_SKILL, skill_id)
    if owner is None:
        return None
    return (
        f"Files of this skill are in the workspace at {owner.folder}/. "
        "Files over the size or count limits, and files stored inline in older skills, are not there; "
        "read them with the skill tool."
    )
