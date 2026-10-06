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

"""Shape rules for blob references (owner + name) that decide whether a ref is safe to resolve."""

from __future__ import annotations

from pathlib import PurePosixPath

from codemie_tools.base.file_object import FileObject


def _is_safe_part(value: str) -> bool:
    if not value or value.startswith("/") or "\\" in value:
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return False
    parts = PurePosixPath(value).parts
    return bool(parts) and ".." not in parts and "." not in parts


def is_safe_blob_ref(file_object: FileObject) -> bool:
    """True when owner and name cannot resolve outside the owner's storage directory.

    Authorization keys on the owner, but the storage backend joins owner and name, so a name such
    as ``../<victim>/secret.pdf`` would otherwise reach another owner's file. Rejects empty values,
    absolute paths, ``..`` or bare ``.`` segments, backslashes and control characters.
    """
    return _is_safe_part(file_object.owner) and _is_safe_part(file_object.name)
