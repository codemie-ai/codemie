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

"""Normalisation of plugin hook events (the `/event-hooks` NDJSON), shared by both adapters."""

from __future__ import annotations

import json
from datetime import datetime, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def datetime_to_ns(dt: datetime) -> int:
    """Nanoseconds since the Unix epoch; a naive datetime is taken as UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt - _EPOCH
    return delta.days * 86_400_000_000_000 + delta.seconds * 1_000_000_000 + delta.microseconds * 1_000


def timestamp_to_ns(event: dict, now_ns: int) -> int:
    """The event's ISO-8601 `timestamp` in nanoseconds, or `now_ns` when missing or invalid."""
    ts = event.get("timestamp")
    if not ts:
        return now_ns
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)  # OverflowError past year 9999 or before year 1
        return datetime_to_ns(parsed)
    except (ValueError, AttributeError, OverflowError):
        return now_ns


def safe_str(val: object) -> str:
    """Hook values as strings: `None` is empty, containers are JSON (non-ASCII kept)."""
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return str(val)
