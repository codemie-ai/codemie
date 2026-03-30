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

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any


def _to_utc(dt: datetime) -> datetime:
    """Naive datetimes are treated as UTC wall time; aware values are unchanged (comparable min/max)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _get_msg_date(msg: Any) -> datetime | None:
    """Extract the ``date`` value from a message regardless of whether it is a dict or an object.

    Only returns a value when it is already a ``datetime`` instance; non-datetime values
    (e.g. ISO-8601 strings from raw JSONB-decoded dicts) are silently skipped so that
    ``_to_utc`` never receives a non-datetime argument.
    """
    val = msg.get("date") if isinstance(msg, dict) else getattr(msg, "date", None)
    return val if isinstance(val, datetime) else None


def get_timestamp_bounds(history: Sequence[Any] | None) -> tuple[datetime | None, datetime | None]:
    """
    Min/max ``date`` from a history sequence of message objects or plain dicts.

    Handles both attribute-style objects (e.g. ``GeneratedMessage``) and raw JSONB-decoded
    dicts. Normalises naive datetimes to UTC so mixed naive/aware histories do not raise on
    ``min``/``max``. Pair with SQL ``::timestamptz`` for paginated bounds so both paths stay aware.
    """
    if not history:
        return None, None
    dates = [_to_utc(d) for msg in history if (d := _get_msg_date(msg)) is not None]
    if not dates:
        return None, None
    return min(dates), max(dates)
