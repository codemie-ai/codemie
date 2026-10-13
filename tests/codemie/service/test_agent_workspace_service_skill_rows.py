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

"""A workspace file that points at a skill's stored file is copy-on-write: editing it never touches the skill."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codemie.rest_api.models.agent_workspace import AgentWorkspaceFile
from codemie.service.agent_workspace_service import AgentWorkspaceService

WORKSPACE_ID = "ws-1"
PATH = "skills/foo/a.py"


def _skill_row() -> AgentWorkspaceFile:
    return AgentWorkspaceFile(
        id="f1",
        workspace_id=WORKSPACE_ID,
        path=PATH,
        blob_owner="skill-sk1",
        blob_name="ab12cd34ef56ab12/a.py",
        mime_type="text/x-python",
        checksum="ff",
        size=3,
        version=1,
    )


@pytest.fixture
def service() -> AgentWorkspaceService:
    svc = AgentWorkspaceService.__new__(AgentWorkspaceService)
    svc.repository = MagicMock()
    svc.file_repository = MagicMock()
    svc.repository.get_file.return_value = _skill_row()
    svc.repository.save_file.side_effect = lambda workspace_file: workspace_file
    return svc


def test_editing_a_skill_owned_file_writes_a_workspace_blob_and_repoints_the_row(service):
    saved = service._upsert_workspace_file_content(WORKSPACE_ID, PATH, "print('edited')")

    service.file_repository.write_file.assert_called_once()
    written = service.file_repository.write_file.call_args.kwargs
    assert written["owner"] == f"workspace-{WORKSPACE_ID}"
    assert written["name"] == PATH
    assert (saved.blob_owner, saved.blob_name) == (f"workspace-{WORKSPACE_ID}", PATH)
    assert saved.version == 2


def test_no_write_ever_targets_the_skill_owner(service):
    service._upsert_workspace_file_content(WORKSPACE_ID, PATH, "x")
    service._upsert_workspace_file_content(WORKSPACE_ID, PATH, b"\x00\x01")

    owners = {call.kwargs["owner"] for call in service.file_repository.write_file.call_args_list}
    assert owners == {f"workspace-{WORKSPACE_ID}"}
    service.file_repository.delete_file.assert_not_called()


def test_deleting_a_skill_owned_file_only_soft_deletes_the_row(service):
    service.get_workspace = MagicMock(return_value=MagicMock(id=WORKSPACE_ID))  # type: ignore[method-assign]
    service.repository.soft_delete_file.return_value = True

    result = service.delete_file(WORKSPACE_ID, PATH, MagicMock(id="u1"))

    assert result.deleted is True
    service.repository.soft_delete_file.assert_called_once_with(WORKSPACE_ID, PATH)
    service.file_repository.write_file.assert_not_called()
    service.file_repository.delete_file.assert_not_called()
