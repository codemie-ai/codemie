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

"""skill_location_hint reads where a skill's files are from the folder-owner record."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.agent_workspace import SOURCE_KIND_SKILL
from codemie.service.skill_workspace_hint import skill_location_hint


@pytest.fixture
def repository():
    with patch("codemie.service.skill_workspace_hint.AgentWorkspaceRepository") as repository_cls:
        yield repository_cls.return_value


def test_hint_names_the_folder_stored_on_the_owner_record_not_the_current_skill_name(repository):
    repository.get_by_conversation_for_user.return_value = SimpleNamespace(id="w1")
    repository.get_folder_owner.return_value = SimpleNamespace(folder="skills/old-name")

    hint = skill_location_hint("c1", MagicMock(id="u1"), "sk1")

    assert "skills/old-name/" in hint
    assert "read them with the skill tool" in hint
    repository.get_by_conversation_for_user.assert_called_once_with("c1", "u1")
    repository.get_folder_owner.assert_called_once_with("w1", SOURCE_KIND_SKILL, "sk1")


def test_no_hint_without_a_workspace(repository):
    repository.get_by_conversation_for_user.return_value = None

    assert skill_location_hint("c1", MagicMock(id="u1"), "sk1") is None
    repository.get_folder_owner.assert_not_called()


def test_no_hint_when_the_skill_does_not_own_a_folder(repository):
    repository.get_by_conversation_for_user.return_value = SimpleNamespace(id="w1")
    repository.get_folder_owner.return_value = None

    assert skill_location_hint("c1", MagicMock(id="u1"), "sk1") is None
