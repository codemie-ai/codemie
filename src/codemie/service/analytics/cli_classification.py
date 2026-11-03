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

"""Subscription classification of a CLI analytics session (EPMCDME-16440, D7).

Pure: no I/O. Organization names are free-text labels, so matching is trimmed and case-insensitive.
"""

from __future__ import annotations

import base64
import hashlib
import unicodedata
from collections.abc import Iterable
from typing import Literal

Classification = Literal["codemie", "unknown", "corporate", "personal"]


def _normalize(values: Iterable[str]) -> list[str]:
    return sorted({v.strip().casefold() for v in values if v and v.strip()})


def classify(routed_via_codemie: bool, organization: str | None, org_list: Iterable[str]) -> Classification:
    if routed_via_codemie:
        return "codemie"
    org = (organization or "").strip().casefold()
    orgs = _normalize(org_list)
    if not org or not orgs:
        return "unknown"
    return "corporate" if org in orgs else "personal"


ORGANIZATION_ENCODING = "utf8-b64u.v1"
_ENCODED_PREFIX = ORGANIZATION_ENCODING + ":"
_MAX_ENCODED_CHARS = 1366  # ceil(1024 * 4 / 3), unpadded
_MAX_DECODED_BYTES = 1024
_MAX_CODE_POINTS = 256
_B64URL = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def decode_organization(value: str) -> str:
    """The original organization text carried by `X-CodeMie-Organization-Encoded` (`utf8-b64u.v1:<base64url>`).

    Transport only: the text is returned as sent (no trim, casefold or NFC); `classify` normalizes it. Strict, so
    one organization has one representation: canonical unpadded base64url, valid UTF-8, no C0/C1 controls, bounded.
    Raises ValueError for anything else.
    """
    if not value.startswith(_ENCODED_PREFIX):
        raise ValueError("unsupported_version")
    payload = value[len(_ENCODED_PREFIX) :]
    if not payload or len(payload) > _MAX_ENCODED_CHARS or not _B64URL.issuperset(payload) or len(payload) % 4 == 1:
        raise ValueError("malformed")
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != payload:  # non-zero trailing bits: not canonical
        raise ValueError("not_canonical")
    text = raw.decode("utf-8")  # UnicodeDecodeError is a ValueError
    if len(raw) > _MAX_DECODED_BYTES or len(text) > _MAX_CODE_POINTS:
        raise ValueError("too_long")
    if not text.strip() or any(unicodedata.category(ch) == "Cc" for ch in text):
        raise ValueError("empty_or_control")
    return text


def policy_version(org_list: Iterable[str]) -> str:
    """Short SHA-256 of the sorted normalized list; changes only with the list."""
    return hashlib.sha256("\n".join(_normalize(org_list)).encode()).hexdigest()[:16]
