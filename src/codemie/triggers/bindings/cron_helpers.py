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

"""Crontab day_of_week parsing shared by cron.py and cron_stagger.py."""

import re
from typing import Optional

# Index = crontab weekday number; crontab accepts both 0 and 7 for Sunday.
CRONTAB_WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat", "sun")

# One comma-separated term of a crontab day_of_week field: "4", "1-5", "*/2", "mon-fri/3".
CRONTAB_WEEKDAY_TERM = re.compile(
    r"(?P<start>\*|\d+|[a-z]+)(?:-(?P<end>\d+|[a-z]+))?(?:/(?P<step>\d+))?",
    re.IGNORECASE,
)


def weekday_number(value: str) -> Optional[int]:
    """Return the crontab weekday number for "4" or "thu", or None if unrecognised."""
    if value.isdigit():
        return int(value) if int(value) <= 7 else None
    return CRONTAB_WEEKDAYS.index(value.lower()) if value.lower() in CRONTAB_WEEKDAYS else None


def expand_weekday_term(term: str) -> Optional[list[str]]:
    """Expand one crontab day_of_week term ("4", "1-5", "*/2") to weekday names.

    Returns None if the term is not something this understands, so the caller can
    hand the field to CronTrigger untouched.
    """
    match = CRONTAB_WEEKDAY_TERM.fullmatch(term)
    if not match:
        return None

    start, end, step = match.group("start"), match.group("end"), match.group("step")
    if start == "*":
        first, last = 0, 6
    elif end:
        first, last = weekday_number(start), weekday_number(end)
    elif step:
        # A lone weekday with a step runs through Sunday-as-7, because crontab counts
        # Sunday at both ends of the week: "1/2" is Mon,Wed,Fri,Sun, not Mon,Wed,Fri.
        first, last = weekday_number(start), 7
    else:
        first = last = weekday_number(start)

    if first is None or last is None:
        return None

    # Ranges wrap around the week ("5-7" is Fri-Sun, "6-0" is Sat-Sun), except for
    # "0-7", which spans the whole week rather than being an empty wrap.
    span = 7 if (first, last) == (0, 7) else (last - first) % 7
    return [CRONTAB_WEEKDAYS[(first + offset) % 7] for offset in range(0, span + 1, int(step or 1))]


def normalize_day_of_week(day_of_week: str) -> str:
    """Translate a crontab day_of_week field into APScheduler weekday names.

    APScheduler 3.x numbers weekdays 0=Monday while standard crontab numbers them
    0=Sunday (7 is also Sunday), so passing the field through unchanged shifts every
    numeric weekday by a day -- "* * * * 4" fires on Friday instead of Thursday.

    Rather than renumber the field and have to reason about how ranges and steps
    survive the shift, expand it to the plain list of weekdays it means and emit
    those as names. Names carry the same meaning in both systems, so "4" becomes
    "thu" and "1-5/2" becomes "mon,wed,fri" with no arithmetic left to get wrong.

    Weekday names are accepted too, since APScheduler drops the step from a named
    range ("mon-fri/2" silently fires every working day); expanding them here means
    the step is applied before APScheduler ever sees the field.

    Anything this cannot read -- nth-weekday syntax, unknown names, out-of-range
    numbers -- is returned untouched so CronTrigger keeps applying its own parsing
    and raises its usual ValueError for invalid input.
    """
    if day_of_week.strip() == "*":
        return "*"

    weekdays = []
    for term in day_of_week.split(","):
        expanded = expand_weekday_term(term.strip())
        if expanded is None:
            return day_of_week
        weekdays.extend(expanded)

    # "0-7" and overlapping terms repeat days; dict.fromkeys drops repeats in order.
    return ",".join(dict.fromkeys(weekdays))


def is_every_day_of_week(day_of_week: str) -> bool:
    """Return True when ``day_of_week`` (as the user wrote it) denotes every day.

    Covers both the literal ``"*"`` and any range/list spelling that is semantically
    a full week (e.g. ``"0-7"``, which crontab treats as Sun-Sun inclusive rather than
    an empty wrap).
    """
    if day_of_week.strip() == "*":
        return True
    normalized = normalize_day_of_week(day_of_week)
    return set(normalized.split(",")) == set(CRONTAB_WEEKDAYS)
