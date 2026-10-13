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

from __future__ import annotations

import hashlib

import pytest
from codemie_tools.base.file_object import FileObject

from codemie.rest_api.models.agent_workspace import SOURCE_KIND_SKILL
from codemie.service.file_service.blob_ref_rules import (
    CONTENT_KEY_HEX_LENGTH,
    content_addressed_key,
    is_safe_blob_ref,
    is_source_owned_blob,
    is_source_owned_blob_ref,
    skill_blob_owner,
    source_blob_owner,
)


def _file(owner: str, name: str) -> FileObject:
    return FileObject(name=name, mime_type="application/pdf", owner=owner)


@pytest.mark.parametrize(
    ("owner", "name"),
    [
        ("owner", ""),
        ("", "name.pdf"),
        ("owner", "/abs"),
        ("/abs", "name.pdf"),
        ("owner", "../x"),
        ("../x", "name.pdf"),
        ("owner", "a\\b"),
        ("a\\b", "name.pdf"),
        ("owner", "."),
        (".", "name.pdf"),
        ("owner", "a\x00b"),
        ("a\x00b", "name.pdf"),
        ("owner", "a\nb"),
    ],
)
def test_rejects_unsafe_refs(owner: str, name: str) -> None:
    assert is_safe_blob_ref(_file(owner, name)) is False


@pytest.mark.parametrize(
    ("owner", "name"),
    [("uuid", "name.pdf"), ("workspace-abc", "sub/dir/file.txt"), ("uuid", "a..b.txt")],
)
def test_accepts_safe_refs(owner: str, name: str) -> None:
    assert is_safe_blob_ref(_file(owner, name)) is True


class TestContentAddressedKey:
    def test_key_prefix_has_content_key_hex_length(self):
        key, digest = content_addressed_key("scripts/run.py", b"print(1)")

        assert CONTENT_KEY_HEX_LENGTH == 32
        assert digest == hashlib.sha256(b"print(1)").hexdigest()
        assert key == f"{digest[:CONTENT_KEY_HEX_LENGTH]}/scripts/run.py"

    def test_same_content_same_key_changed_content_new_key(self):
        key, _ = content_addressed_key("scripts/run.py", b"print(1)")

        assert content_addressed_key("scripts/run.py", b"print(1)")[0] == key
        assert content_addressed_key("scripts/run.py", b"print(2)")[0] != key

    def test_worst_case_skill_storage_key_fits_the_s3_key_limit(self):
        from codemie.service.file_service.workspace_path_rules import MAX_SKILL_BUNDLE_PATH_LENGTH

        owner = skill_blob_owner("00000000-0000-0000-0000-000000000000")
        key, _ = content_addressed_key("a" * MAX_SKILL_BUNDLE_PATH_LENGTH, b"x")

        assert len(owner) == 42
        assert len(f"{owner}/{key}".encode()) <= 1024


class TestSourceOwnerRegistry:
    def test_skill_owner_keeps_its_prefix(self):
        assert source_blob_owner(SOURCE_KIND_SKILL, "sk1") == "skill-sk1"
        assert skill_blob_owner("sk1") == "skill-sk1"

    def test_unknown_source_kind_has_no_owner(self):
        with pytest.raises(ValueError):
            source_blob_owner("assistant", "a1")

    @pytest.mark.parametrize("owner", ["skill-sk1", "skill-00000000-0000-0000-0000-000000000000"])
    def test_registered_prefixes_are_source_owned(self, owner):
        assert is_source_owned_blob(owner) is True
        assert is_source_owned_blob_ref(_file(owner, "ab12/a.py")) is True

    @pytest.mark.parametrize("owner", ["user-1", "workspace-ws1", "assistant-a1", "skills-x", ""])
    def test_other_owners_are_not_source_owned(self, owner):
        assert is_source_owned_blob(owner) is False
