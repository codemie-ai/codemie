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

import hashlib
from pathlib import PurePosixPath

from codemie_tools.base.file_object import FileObject

from codemie.rest_api.models.agent_workspace import SOURCE_KIND_SKILL

# Blob owner prefix per seeding source; stories 5 and 6 add assistant- and workflow-.
_SOURCE_OWNER_PREFIXES: dict[str, str] = {SOURCE_KIND_SKILL: "skill-"}


def source_blob_owner(source_kind: str, source_id: str) -> str:
    """The blob owner under which a source's stored files live: the only place this name is built."""
    prefix = _SOURCE_OWNER_PREFIXES.get(source_kind)
    if prefix is None:
        raise ValueError(f"Unknown blob source kind: {source_kind!r}")
    return f"{prefix}{source_id}"


def skill_blob_owner(skill_id: str) -> str:
    return source_blob_owner(SOURCE_KIND_SKILL, skill_id)


# 128 bits of the sha-256 in the blob key: with 64 bits a crafted collision could substitute a stored blob.
# Budget: owner 'skill-<uuid>' (42) + '/' + 32 + '/' + MAX_SKILL_BUNDLE_PATH_LENGTH (943) = 1019 <= 1024 (S3 key).
CONTENT_KEY_HEX_LENGTH = 32


def content_addressed_key(path: str, content: bytes) -> tuple[str, str]:
    """Return (storage key, full sha-256 hex) for a source blob; the key changes whenever the content does."""
    digest = hashlib.sha256(content).hexdigest()
    return f"{digest[:CONTENT_KEY_HEX_LENGTH]}/{path}", digest


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


def is_source_owned_blob(owner: str) -> bool:
    """True for a blob owned by a seeding source: only that source's seeder may create workspace rows pointing at it."""
    return any(owner.startswith(prefix) for prefix in _SOURCE_OWNER_PREFIXES.values())


def is_source_owned_blob_ref(file_object: FileObject) -> bool:
    return is_source_owned_blob(file_object.owner)
