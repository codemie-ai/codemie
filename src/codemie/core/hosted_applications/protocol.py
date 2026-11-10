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

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Collection

from codemie.core.hosted_applications.errors import HostedApplicationError

_SESSION_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_RANGE_UNAVAILABLE = "Requested range is unavailable"


def canonical_json(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str
    ).encode()


def etag(kind: str, resource_id, revision: int) -> str:
    return f'"{kind}:{resource_id}:{revision}"'


def check_etag(value: str | None, kind: str, resource_id, revision: int) -> None:
    if value is None:
        raise HostedApplicationError(
            "PRECONDITION_REQUIRED", "Reload the latest application before making this change", 428
        )
    if value != etag(kind, resource_id, revision):
        raise HostedApplicationError("REVISION_CONFLICT", "This item changed in another tab; reload before saving", 409)


def encode_cursor(scope: dict, position: dict, secret: bytes) -> str:
    payload = canonical_json({"scope": hashlib.sha256(canonical_json(scope)).hexdigest(), "position": position})
    if len(payload) > 2048:
        raise HostedApplicationError("INVALID_CURSOR", "Invalid page cursor")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(secret, b"hosted-app-cursor-v1\n" + encoded, hashlib.sha256).hexdigest()
    return encoded.decode() + "." + signature


def decode_cursor(value: str, scope: dict, secret: bytes) -> dict:
    try:
        if len(value) > 4096 or not re.fullmatch(r"[A-Za-z0-9_-]+\.[0-9a-f]{64}", value):
            raise ValueError("Invalid cursor")
        encoded, signature = value.split(".")
        expected = hmac.new(secret, b"hosted-app-cursor-v1\n" + encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid signature")
        payload = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        if payload["scope"] != hashlib.sha256(canonical_json(scope)).hexdigest() or not isinstance(
            payload["position"], dict
        ):
            raise ValueError("Scope mismatch")
        return payload["position"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HostedApplicationError(
            "INVALID_CURSOR", "Page cursor expired or does not match these filters", 422
        ) from exc


def parse_range(value: str | None, size: int) -> tuple[int, int, bool]:
    if value is None:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d{0,18})-(\d{0,18})", value, flags=re.ASCII)
    if not match or size <= 0 or not any(match.groups()):
        raise HostedApplicationError("INVALID_RANGE", _RANGE_UNAVAILABLE, 416)
    first, last = match.groups()
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:
        count = int(last)
        start, end = max(0, size - count), size - 1
        if count == 0:
            raise HostedApplicationError("INVALID_RANGE", _RANGE_UNAVAILABLE, 416)
    if start >= size or end < start:
        raise HostedApplicationError("INVALID_RANGE", _RANGE_UNAVAILABLE, 416)
    return start, end, True


def parse_session_cookies(header: str, names: Collection[str]) -> dict[str, str]:
    """Strictly read platform session cookies from one RFC 6265 ``Cookie`` header.

    The header is split on ``;`` only; each pair is trimmed and split at its first ``=``.
    Pairs whose exact name is not in ``names`` are ignored and never parsed further, so a
    sibling application's cookie value (which may contain spaces or ``=``) cannot supply a
    platform credential. A duplicate platform name or a platform pair without the exact
    43-character token alphabet raises ``ValueError``. The header length is not limited here:
    any uploaded app can set many cookies on the shared content domain, and refusing a long
    header would lock its viewers out of every hosted app. The server already bounds headers.
    """
    found: dict[str, str] = {}
    for part in header.split(";"):
        name, separator, value = part.strip(" \t").partition("=")
        if name not in names:
            continue
        if not separator or name in found or not _SESSION_TOKEN.fullmatch(value):
            raise ValueError("Ambiguous platform cookie")
        found[name] = value
    return found


def etag_matches(header: str | None, current: str) -> bool:
    """RFC 9110 ``If-None-Match`` evaluation: weak comparison over an entity-tag list or ``*``."""
    if not header:
        return False
    if header.strip() == "*":
        return True
    opaque = current.removeprefix("W/")
    return any(candidate.strip().removeprefix("W/") == opaque for candidate in header.split(","))
