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

"""Tests that _build_scheduler_item's nextRunAt agrees with the staggered cron trigger
cron.py actually schedules (EPMCDME-16236)."""

from codemie.service.settings.scheduler_settings_service import (
    RESOURCE_TYPE_DATASOURCE,
    SchedulerSettingsService,
)
from codemie.triggers.bindings.cron_stagger import stagger_cron


def _make_setting(resource_id, cron_expression="0 0 * * *", timezone="UTC"):
    setting = SchedulerSettingsService._create_new_schedule(
        user_id="u1",
        project_name="p",
        resource_type=RESOURCE_TYPE_DATASOURCE,
        resource_id=resource_id,
        resource_name="name",
        cron_expression=cron_expression,
        is_enabled=True,
        timezone=timezone,
    )
    setting.id = "setting-1"
    return setting


def test_next_run_at_uses_staggered_time():
    setting = _make_setting("ds-stagger-1")

    item = SchedulerSettingsService._build_scheduler_item(setting)

    minute, hour, *_ = stagger_cron("ds-stagger-1", "0 0 * * *").split()
    assert item.schedule.nextRunAt is not None
    assert f"T{int(hour):02d}:{int(minute):02d}" in item.schedule.nextRunAt


def test_description_keeps_the_raw_unstaggered_cron():
    """The stored cron/description must still reflect what the user configured."""
    setting = _make_setting("ds-stagger-1")

    item = SchedulerSettingsService._build_scheduler_item(setting)

    assert item.schedule.cron == "0 0 * * *"
