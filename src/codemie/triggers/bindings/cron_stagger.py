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

STAGGER_SEED = b"codemie-reindex-stagger-v1"
STAGGER_WINDOW_MINUTES = 60
MINUTES_PER_DAY = 1440


def _offset_minutes(datasource_id: str) -> int:
    """Derive a stable 0-59 minute offset for ``datasource_id``."""
    digest = hashlib.blake2b(str(datasource_id).encode("utf-8") + STAGGER_SEED, digest_size=8).digest()
    return int.from_bytes(digest, "big") % STAGGER_WINDOW_MINUTES


def stagger_cron(datasource_id: str, cron_expression: str) -> str:
    """Return ``cron_expression`` with a deterministic per-datasource stagger applied.

    Staggering only ever shifts minute/hour, so it applies regardless of what
    day-of-month, month, or day-of-week say -- a schedule that fires on a single
    specific weekday every week (``"0 0 * * 0"``) or a single day every month
    (``"0 0 1 * *"``) collides with every other datasource on the same schedule
    just as surely as a plain daily cron does.

    Only "generic" minute/hour combinations stagger: an hourly cron (``hour ==
    "*"``), or an on-the-hour daily cron (``minute == "0"`` with a concrete hour).
    Anything with a deliberately chosen non-zero minute on a concrete hour (e.g.
    ``"30 16 * * *"``) is left untouched -- that is someone's specific choice of
    time, not the kind of "whatever the default is" value that collides across
    many datasources.
    """
    minute, hour, day_of_month, month, day_of_week = cron_expression.split()

    can_stagger = (
        datasource_id and minute.isdigit() and (hour == "*" or hour.isdigit()) and (hour == "*" or minute == "0")
    )

    if can_stagger:
        offset = _offset_minutes(datasource_id)
        if hour == "*":  # every hour - so stagger just minutes
            minute = str((int(minute) + offset) % 60)
        else:
            total_minutes = (int(hour) * 60 + int(minute) + offset) % MINUTES_PER_DAY
            minute, hour = str(total_minutes % 60), str(total_minutes // 60)

    return " ".join([minute, hour, day_of_month, month, day_of_week])
