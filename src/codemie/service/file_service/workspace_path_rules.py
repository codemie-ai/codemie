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

"""Shape rules for workspace-relative file paths shared by skill upload, seeding and workspace writes."""

from __future__ import annotations

import posixpath
import re
import unicodedata

from codemie_tools.data_management.code_executor.reserved_paths import is_reserved_path

MAX_WORKSPACE_PATH_LENGTH = 1024
SKILL_FOLDER_PREFIX = "skills/"
MAX_SKILL_NAME_LENGTH = 64
SKILL_FOLDER_ID_LENGTH = 8
# A bundle path lands at skills/<name>-<id>/<path>; reserve room for the longest skill name, "-<id>" and the separator.
MAX_SKILL_BUNDLE_PATH_LENGTH = (
    MAX_WORKSPACE_PATH_LENGTH - len(SKILL_FOLDER_PREFIX) - MAX_SKILL_NAME_LENGTH - (1 + SKILL_FOLDER_ID_LENGTH) - 1
)

_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_LINE_SEPARATORS = {"\u2028", "\u2029"}


def skill_workspace_folder(skill_name: str, skill_id: str) -> str:
    """The workspace folder that holds a skill's files: the only place this name is built.

    The id fragment keeps same-named skills apart; the folder-owner collision check stays as a last resort.
    """
    return f"{SKILL_FOLDER_PREFIX}{skill_name}-{skill_id[:SKILL_FOLDER_ID_LENGTH]}"


def _is_forbidden_char(ch: str) -> bool:
    """Control characters and line separators; format characters (ZWJ in emoji, RTL marks) are legitimate in names."""
    return ch in _LINE_SEPARATORS or unicodedata.category(ch) == "Cc"


def normalize_relative_path(path: str) -> str:
    """Convert backslashes to slashes and collapse redundant segments; no validation."""
    return posixpath.normpath(path.strip().replace("\\", "/"))


def validate_relative_path(path: str, max_length: int) -> str:
    """Return the normalized relative path or raise ValueError describing why it is unsafe."""
    path = path or ""
    if any(_is_forbidden_char(ch) for ch in path):
        raise ValueError("path must not contain control characters")
    if path != path.strip():
        raise ValueError("path must not start or end with whitespace")
    raw = path.replace("\\", "/")
    if not raw:
        raise ValueError("path must not be empty")
    if raw.startswith("/") or _DRIVE_PREFIX.match(raw):
        raise ValueError("path must be relative")
    if ".." in raw.split("/"):
        raise ValueError("path must not contain '..' segments")
    normalized = normalize_relative_path(raw)
    if normalized in {"", "."}:
        raise ValueError("path must point to a file")
    if len(normalized) > max_length:
        raise ValueError(f"path is too long (max {max_length} characters)")
    return normalized


def is_reserved_workspace_path(normalized_path: str) -> bool:
    """True for paths the script runtime owns; the rule itself lives in ``reserved_paths``."""
    return is_reserved_path(normalized_path)
