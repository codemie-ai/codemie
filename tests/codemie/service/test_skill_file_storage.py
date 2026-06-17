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

from unittest.mock import MagicMock

from codemie_tools.base.file_object import FileObject

from codemie.service.skill_file_storage import SkillFileStorage


SKILL_ID = "abc-123"
EXPECTED_OWNER = "skill-abc-123"


def _make_repo():
    repo = MagicMock()
    repo.write_file.return_value = FileObject(
        name="file.md", mime_type="text/markdown", owner=EXPECTED_OWNER, content=b"hello"
    )
    repo.read_file.return_value = FileObject(
        name="file.md", mime_type="text/markdown", owner=EXPECTED_OWNER, content=b"hello"
    )
    return repo


class TestSkillFileStorageWriteFile:
    def test_delegates_to_repo_with_correct_owner(self):
        repo = _make_repo()
        storage = SkillFileStorage(SKILL_ID, repo)

        storage.write_file(name="file.md", mime_type="text/markdown", content=b"hello")

        repo.write_file.assert_called_once_with(
            name="file.md",
            mime_type="text/markdown",
            owner=EXPECTED_OWNER,
            content=b"hello",
        )

    def test_owner_prefix_is_skill_id(self):
        repo = _make_repo()
        storage = SkillFileStorage("my-skill-id", repo)
        storage.write_file(name="x.md", mime_type="text/plain", content=b"x")
        _, kwargs = repo.write_file.call_args
        assert kwargs["owner"] == "skill-my-skill-id"


class TestSkillFileStorageReadFile:
    def test_delegates_to_repo_with_correct_owner(self):
        repo = _make_repo()
        storage = SkillFileStorage(SKILL_ID, repo)

        storage.read_file(file_name="file.md", mime_type="text/markdown")

        repo.read_file.assert_called_once_with(
            file_name="file.md",
            owner=EXPECTED_OWNER,
            mime_type="text/markdown",
        )

    def test_mime_type_optional(self):
        repo = _make_repo()
        storage = SkillFileStorage(SKILL_ID, repo)
        storage.read_file(file_name="file.md")
        _, kwargs = repo.read_file.call_args
        assert kwargs.get("mime_type") is None
        assert kwargs["owner"] == EXPECTED_OWNER
