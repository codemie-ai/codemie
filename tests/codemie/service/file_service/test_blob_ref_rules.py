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

import pytest
from codemie_tools.base.file_object import FileObject

from codemie.service.file_service.blob_ref_rules import is_safe_blob_ref


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
