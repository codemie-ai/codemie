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

from apscheduler.triggers.cron import CronTrigger


def build_cron_trigger(cron_expression: str) -> CronTrigger | None:
    """Parse a 5-field cron expression and return a UTC CronTrigger, or None on error."""
    cron_parts = cron_expression.split()
    if len(cron_parts) != 5:
        return None
    minute, hour, day, month, day_of_week = cron_parts
    try:
        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone="UTC",
        )
    except (ValueError, TypeError):
        return None
