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
RESOURCE_TYPE_DATASOURCE = "datasource"

STAGGERABLE_PRESETS = frozenset(
    {
        "0 * * * *",  # hourly
        "0 0 * * *",  # daily at midnight
        "0 0 * * 0",  # weekly on Sunday at midnight
        "0 0 1 * *",  # monthly on the 1st at midnight
    }
)


def _offset_minutes(resource_id: str) -> int:
    """Derive a stable 0-59 minute offset for ``resource_id``."""
    digest = hashlib.blake2b(str(resource_id).encode("utf-8") + STAGGER_SEED, digest_size=8).digest()
    return int.from_bytes(digest, "big") % STAGGER_WINDOW_MINUTES


def stagger_cron(resource_id: str, cron_expression: str, resource_type: str = RESOURCE_TYPE_DATASOURCE) -> str:
    """Return ``cron_expression`` with a deterministic per-datasource stagger applied.

    Only datasource schedules stagger -- assistant and workflow schedules fire at
    exactly the time requested. And even for a datasource, only one of
    ``STAGGERABLE_PRESETS`` staggers -- the generic preset values that many
    datasources end up sharing verbatim. Any other expression, including a preset
    spelled differently (e.g. ``"0 0 * * 1-5"`` instead of ``"0 0 * * 0"``) or a
    deliberately chosen time (e.g. ``"30 16 * * *"``), is a specific choice, not a
    "whatever the default is" value, and is left untouched.
    """
    if (
        not resource_id
        or (resource_type or "").lower() != RESOURCE_TYPE_DATASOURCE
        or " ".join(cron_expression.split()) not in STAGGERABLE_PRESETS
    ):
        return cron_expression

    minute, hour, day_of_month, month, day_of_week = cron_expression.split()
    offset = _offset_minutes(resource_id)
    if hour == "*":  # hourly preset - stagger just the minute
        minute = str((int(minute) + offset) % 60)
    else:
        total_minutes = (int(hour) * 60 + int(minute) + offset) % MINUTES_PER_DAY
        minute, hour = str(total_minutes % 60), str(total_minutes // 60)

    return " ".join([minute, hour, day_of_month, month, day_of_week])
