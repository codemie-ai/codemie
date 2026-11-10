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

from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote_to_bytes

from codemie.core.hosted_applications.errors import HostedApplicationError

_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.IGNORECASE)
_BAD_ESCAPE = re.compile(rb"%(?![0-9a-fA-F]{2})|%(?:2[fF]|5[cC]|00)")


def canonical_artifact_path(value: str) -> str:
    """Accept one portable relative path, without repairing attacker input."""
    if not isinstance(value, str):
        raise HostedApplicationError("INVALID_ARCHIVE_PATH", "Artifact contains an unsafe or noncanonical path")
    parts = value.split("/")
    if (
        not value
        or len(value.encode("utf-8")) > 240
        or len(parts) > 20
        or any(c in value for c in "\\:%?#")
        or any(unicodedata.category(c).startswith("C") for c in value)
        or unicodedata.normalize("NFC", value) != value
        or any(part in ("", ".", "..") or part.endswith((".", " ")) or _WINDOWS_RESERVED.match(part) for part in parts)
        or parts[0].casefold() == "_platform"
    ):
        raise HostedApplicationError("INVALID_ARCHIVE_PATH", "Artifact contains an unsafe or noncanonical path")
    return value


def strip_root_path(raw_path: bytes, root_path: str) -> bytes:
    """Remove the ASGI mount prefix that servers prepend to ``raw_path`` (uvicorn ``--root-path``).

    Only a whole leading segment is removed; a ``raw_path`` without the prefix is returned
    unchanged because some servers do not prepend it. Non-ASCII roots cannot match a raw path.
    """
    prefix = root_path.rstrip("/")
    if not prefix:
        return raw_path
    try:
        encoded = prefix.encode("ascii")
    except UnicodeError:
        return raw_path
    if raw_path == encoded:
        return b"/"
    if raw_path.startswith(encoded + b"/"):
        return raw_path[len(encoded) :]
    return raw_path


def decode_content_path(raw_path: bytes) -> str:
    """Decode a URL path once, preserving a unique manifest lookup key."""
    if not raw_path.startswith(b"/") or _BAD_ESCAPE.search(raw_path):
        raise HostedApplicationError("FILE_NOT_FOUND", "File unavailable", 404)
    try:
        path = unquote_to_bytes(raw_path[1:]).decode("utf-8", errors="strict")
        trailing = path.endswith("/")
        if trailing:
            path = path[:-1]
        if not path:
            return ""
        return canonical_artifact_path(path) + ("/" if trailing else "")
    except (ValueError, HostedApplicationError) as exc:
        raise HostedApplicationError("FILE_NOT_FOUND", "File unavailable", 404) from exc


def normalize_name(value: str, maximum: int = 160) -> tuple[str, str]:
    value = unicodedata.normalize("NFC", value).strip()
    if not value or len(value) > maximum or any(unicodedata.category(c).startswith("C") for c in value):
        raise HostedApplicationError("INVALID_NAME", f"Name must contain 1–{maximum} printable characters")
    return value, value.casefold()
