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

"""Skill.get_companion_file_refs exposes where a companion file's bytes live, server-side only."""

from codemie.rest_api.models.skill import Skill


def _skill(companion_files):
    return Skill(name="s", description="d", content="c" * 200, project="p", companion_files=companion_files)


def test_refs_expose_storage_key_and_checksum_and_never_content():
    skill = _skill(
        [
            {
                "path": "a.py",
                "mime_type": "text/x-python",
                "encoding": "text",
                "size_bytes": 3,
                "storage_key": "ab12/a.py",
                "checksum": "ff",
            },
            {"path": "old.md", "mime_type": "text/markdown", "encoding": "text", "size_bytes": 2, "content": "hi"},
        ]
    )

    refs = skill.get_companion_file_refs()

    assert (refs[0].path, refs[0].storage_key, refs[0].checksum) == ("a.py", "ab12/a.py", "ff")
    assert (refs[1].path, refs[1].storage_key, refs[1].checksum) == ("old.md", None, "")
    assert "content" not in refs[1].model_dump()


def test_refs_of_a_skill_without_files_is_empty():
    assert _skill([]).get_companion_file_refs() == []


def test_api_metadata_does_not_leak_the_storage_key():
    skill = _skill([{"path": "a.py", "size_bytes": 3, "storage_key": "ab12/a.py", "checksum": "ff"}])

    assert "storage_key" not in skill.get_companion_file_metadata()[0].model_dump()
