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

"""Deterministic per-datasource cron stagger (EPMCDME-16236).

Datasources sharing one cron expression (e.g. everyone on "Daily at midnight") all
fire at the same instant and overload the reindex queue. This spreads them out by
giving each datasource a stable BLAKE2b-derived offset (not Python's ``hash()``,
which is salted per process and would disagree across pods) — no new state, no
randomness at runtime, same id always yields the same offset.
"""

import hashlib

from codemie.triggers.bindings.cron_helpers import is_every_day_of_week

STAGGER_SEED = b"codemie-reindex-stagger-v1"
STAGGER_WINDOW_MINUTES = 60
MINUTES_PER_DAY = 1440


def _offset_minutes(datasource_id: str) -> int:
    """Derive a stable 0-59 minute offset for ``datasource_id``."""
    digest = hashlib.blake2b(str(datasource_id).encode("utf-8") + STAGGER_SEED, digest_size=8).digest()
    return int.from_bytes(digest, "big") % STAGGER_WINDOW_MINUTES


def stagger_cron(datasource_id: str, cron_expression: str) -> str:
    """Return ``cron_expression`` with a deterministic per-datasource stagger applied.

    Unchanged unless day/month/day-of-week are all ``"*"``, minute/hour are plain
    integers, and ``datasource_id`` is set. An hourly cron (``hour == "*"``) staggers
    only the minute, mod 60.
    """
    minute, hour, day_of_month, month, day_of_week = cron_expression.split()

    is_every_day = day_of_month == "*" and month == "*" and is_every_day_of_week(day_of_week)
    can_stagger = datasource_id and is_every_day and minute.isdigit() and (hour == "*" or hour.isdigit())

    if can_stagger:
        offset = _offset_minutes(datasource_id)
        if hour == "*":  # every hour - so stagger just minutes
            minute = str((int(minute) + offset) % 60)
        else:
            total_minutes = (int(hour) * 60 + int(minute) + offset) % MINUTES_PER_DAY
            minute, hour = str(total_minutes % 60), str(total_minutes // 60)

    return " ".join([minute, hour, day_of_month, month, day_of_week])
