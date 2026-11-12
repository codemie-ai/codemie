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

"""Whether a Sessions-list request reads the stored sort keys of `session_dims` (EPMCDME-16427, part 2).

The stored read is taken when the kill switch (`session_sort_keys_control.enabled`) is on, the request's filters
are expressible over the stored columns and the window has at most `s_cap` sessions without a usable stamp (set S,
computed live and merged). Otherwise the request runs today's statements unchanged. Each fallback and each merge with
a non-empty S logs a WARNING, at most once per reason per `FALLBACK_LOG_INTERVAL_S` (no metrics facility exists).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any, get_args

from codemie.repository.cli_analytics.postgres.session_sort_keys import MODEL_GROUPS_REV
from codemie.repository.cli_analytics.scope import Mode, ViewerScope

logger = logging.getLogger(__name__)

DEFAULT_S_CAP = 5000
FALLBACK_LOG_INTERVAL_S = 60.0

# Fallback reasons: the switch is off, S is larger than the cap, the filter cannot be read from the stored columns,
# no current rates version (cost only).
DISABLED = "disabled"
S_OVER_CAP = "s_over_cap"
UNEXPRESSIBLE = "unexpressible"
NO_RATES_VERSION = "no_rates_version"  # the cost key: the reader has no current rates version to compare stamps with
S_MERGED = "s_merged"  # not a fallback: the stored read merged a non-empty S

# The stored model groups (EPMCDME-17051) have their own switch (`session_sort_keys_control.model_groups_enabled`) and
# their own cap: the live arm of a statement over the groups is expensive, so above `GROUPS_S_CAP` sessions without
# valid groups in the table the request reads everything live.
GROUPS_S_CAP = 1000
GROUPS_DISABLED = "model_groups_disabled"
GROUPS_S_OVER_CAP = "model_groups_s_over_cap"
GROUPS_S_MERGED = "model_groups_s_merged"  # not a fallback: the stored read merged a non-empty S
GROUPS_READ_ERROR = "model_groups_read_error"  # the switch or S could not be read: the request reads live
# The kill switch of the stored groups: on, and not recorded as turned off by an operator (that wins); no row is off.
GROUPS_ENABLED_SQL = (
    "SELECT coalesce(bool_and(model_groups_enabled AND NOT model_groups_operator_disabled), false) "
    "FROM session_sort_keys_control"
)
# S: the sessions whose groups are not valid (trusted iff `rates_version IS NOT NULL AND model_groups_rev = 1`),
# the never-valid ones included, one more than the cap so the caller can tell it was exceeded. The predicate is the
# one of the partial index `session_dims_model_groups_pending`.
# The revision is inlined: a bound parameter would keep the planner from proving the partial index applies.
GROUPS_STALE_SQL = (
    f"SELECT session_id FROM session_dims WHERE rates_version IS NULL OR model_groups_rev IS DISTINCT FROM "
    f"{MODEL_GROUPS_REV} ORDER BY session_id LIMIT $1"
)


@dataclass(frozen=True)
class StoredMode:
    """The read a request takes: `stored` with the ids of S (and, for the cost key, the rates `version` S was read
    against), or today's statements and the `reason`."""

    stored: bool
    reason: str | None = None
    stale: tuple[str, ...] = ()
    version: str | None = None


@dataclass(frozen=True)
class ModelGroupsMode:
    """How a request reads the stored model groups: `stored`, with the ids of S (the sessions in the table whose groups
    are not valid, bound as `$stale` by the caller, which keeps those of its own `sel`), or all-live and the
    `reason`."""

    stored: bool
    reason: str | None = None
    stale: tuple[str, ...] = ()


def expressible(scope: Any) -> bool:
    """Whether the viewer scope can be applied on the stored path. Every scope `sql_list_condition` knows is a
    condition over `session_dims` columns, which `sel` applies before any key is read; anything else (a malformed
    scope, a future mode) takes today's statements."""
    return isinstance(scope, ViewerScope) and scope.mode in get_args(Mode)


def report_framework_groups(groups: int, accepted_groups: int, total: int) -> None:
    """Reports the size of the stored framework read: the distinct name sets the rule was applied to (a handful
    when the data is healthy; a large count is the signal to look at the classifier names), how many it accepted and
    the sessions they hold."""
    logger.info(f"cli_analytics: framework groups={groups} accepted={accepted_groups} sessions={total}")


class RateLimitedWarnings:
    """A WARNING per reason at most once per `interval_s`."""

    def __init__(self, interval_s: float) -> None:
        self._interval_s = interval_s
        self._last: dict[str, float] = {}

    def warn(self, reason: str, stale: int | str, sort_by: str) -> None:
        now = time.monotonic()
        last = self._last.get(reason)
        if last is not None and now - last < self._interval_s:
            return
        self._last[reason] = now
        action = "merged S into the stored read" if reason == S_MERGED else "read today's statements"
        logger.warning(f"cli_analytics: sessions sort {sort_by} {action}: reason={reason} |S|{stale}")

    def warn_groups(self, reason: str, stale: int | str) -> None:
        """The same limit for the stored model groups, under their own reason names."""
        now = time.monotonic()
        last = self._last.get(reason)
        if last is not None and now - last < self._interval_s:
            return
        self._last[reason] = now
        action = "merged S into the stored read" if reason == GROUPS_S_MERGED else "read the groups live"
        logger.warning(f"cli_analytics: stored model groups {action}: reason={reason} |S|{stale}")

    def reset(self) -> None:
        self._last.clear()


FALLBACK_WARNINGS = RateLimitedWarnings(FALLBACK_LOG_INTERVAL_S)


async def model_groups_mode(
    conn: Any, session_ids: Sequence[str] | None = None, s_cap: int | None = None
) -> ModelGroupsMode:
    """Whether a request reads the stored model groups, on one connection (the request's repeatable-read
    transaction, so the switch and S are read from the snapshot the statement then runs in): the switch is on and S has
    at most `s_cap` sessions (default `GROUPS_S_CAP`). S is read from the pending index alone, not the window; the
    statement keeps the members that are in its own `sel`. `session_ids` (a request narrowed to some sessions) keeps
    only those, after the cap check. Each fallback and each merge of a non-empty S logs a rate-limited WARNING."""
    cap = GROUPS_S_CAP if s_cap is None else s_cap
    if not await conn.fetchval(GROUPS_ENABLED_SQL):
        FALLBACK_WARNINGS.warn_groups(GROUPS_DISABLED, " not read")
        return ModelGroupsMode(False, GROUPS_DISABLED)
    rows = await conn.fetch(GROUPS_STALE_SQL, cap + 1)
    stale = [r["session_id"] for r in rows]
    if len(stale) > cap:
        FALLBACK_WARNINGS.warn_groups(GROUPS_S_OVER_CAP, f">{cap}")
        return ModelGroupsMode(False, GROUPS_S_OVER_CAP)
    if session_ids is not None:
        asked = set(session_ids)
        stale = [s for s in stale if s in asked]
    if stale:
        FALLBACK_WARNINGS.warn_groups(GROUPS_S_MERGED, f"={len(stale)}")
    return ModelGroupsMode(True, None, tuple(stale))
