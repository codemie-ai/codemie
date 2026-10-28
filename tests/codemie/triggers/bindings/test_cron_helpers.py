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

import pytest
from codemie.triggers.bindings.cron_helpers import is_every_day_of_week, normalize_day_of_week

# --- day_of_week translation (EPMCDME-13782) -------------------------------------------
# APScheduler numbers weekdays 0=Monday, standard crontab numbers them 0=Sunday.


@pytest.mark.parametrize(
    "day_of_week,expected",
    [
        ("0", "sun"),
        ("1", "mon"),
        ("4", "thu"),
        ("6", "sat"),
        ("7", "sun"),  # crontab accepts both 0 and 7 for Sunday
        ("1-5", "mon,tue,wed,thu,fri"),
        ("0,6", "sun,sat"),
        ("0-2", "sun,mon,tue"),
        ("5-7", "fri,sat,sun"),  # range wrapping past Saturday
        ("6-0", "sat,sun"),
        ("0-6", "sun,mon,tue,wed,thu,fri,sat"),
        ("0-7", "sun,mon,tue,wed,thu,fri,sat"),  # whole week, not an empty wrap
        ("*/2", "sun,tue,thu,sat"),
        ("1-5/2", "mon,wed,fri"),
        ("2/3", "tue,fri"),  # lone number with a step runs to the end of the week
        ("1/2", "mon,wed,fri,sun"),  # ...and that end is Sunday-as-7, not Saturday
        ("0-3/2", "sun,tue"),
        ("*", "*"),
        ("mon-fri", "mon,tue,wed,thu,fri"),  # names expand the same way numbers do
        ("MON-FRI", "mon,tue,wed,thu,fri"),
        ("mon-fri/2", "mon,wed,fri"),  # APScheduler drops the step from a named range
        ("sun", "sun"),
        ("4#2", "4#2"),  # nth-weekday syntax - left for CronTrigger to parse
        ("8", "8"),  # out of range - left for CronTrigger to reject
        ("foo", "foo"),  # unknown name - left for CronTrigger to reject
    ],
)
def test_normalize_day_of_week(day_of_week, expected):
    """Crontab weekday numbers translate to the equivalent APScheduler weekday names."""
    assert normalize_day_of_week(day_of_week) == expected


@pytest.mark.parametrize(
    "day_of_week,expected",
    [
        ("*", True),
        ("0-7", True),  # whole week spelled as a range, not the literal wildcard
        ("0-6", True),  # 0-6 is also every day (Sun..Sat)
        ("mon-fri", False),  # weekdays only, not every day
        ("1-5", False),
        ("sun", False),
        ("foo", False),  # unrecognised term never counts as "every day"
    ],
)
def test_is_every_day_of_week(day_of_week, expected):
    assert is_every_day_of_week(day_of_week) is expected
