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

"""The enable command nulls every matching stamp, also the ones a writer held, before it turns the readers on."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from codemie.repository.cli_analytics.postgres import session_sort_keys as keys

_SINCE = datetime(2026, 11, 1, tzinfo=timezone.utc)


class _Conn:
    """`held` ids are locked by a writer during the first sweep and free from the second; `fail_wait` makes the
    second sweep give up on a lock."""

    def __init__(self, stamped: list[str], held: set[str], fail_wait: bool = False) -> None:
        self.stamped, self.held, self.fail_wait = list(stamped), set(held), fail_wait
        self.log: list[str] = []
        self.waiting = False

    @asynccontextmanager
    async def _tx(self):
        self.waiting = False
        yield

    def transaction(self):
        return self._tx()

    async def fetch(self, sql, limit, last, *args, timeout=None):
        skip = "SKIP LOCKED" in sql
        self.log.append("fetch:skip" if skip else "fetch:wait")
        if not skip and self.fail_wait and any(s in self.held for s in self.stamped):
            raise TimeoutError("canceling statement due to lock timeout")
        rows = [s for s in sorted(self.stamped) if s > last and not (skip and s in self.held)]
        return [{"session_id": s} for s in rows[:limit]]

    async def execute(self, sql, *args, timeout=None):
        if sql.startswith("SET LOCAL lock_timeout"):
            self.log.append(sql)
        elif sql == keys._NULL_STAMPS:
            self.log.append("null")
            self.stamped = [s for s in self.stamped if s not in args[0]]
        elif sql == keys._SET_ENABLED:
            self.log.append("enable")
            self.held.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("since", [_SINCE, None])
async def test_a_stamp_a_writer_held_is_nulled_by_a_second_sweep_that_waits_before_the_readers_are_enabled(since):
    conn = _Conn(["a", "b", "c"], held={"b"})

    nulled = await keys.enable_session_sort_keys(conn, since, batch=10)  # type: ignore[arg-type]

    assert nulled == 3 and conn.stamped == []
    assert conn.log.index("fetch:wait") > conn.log.index("fetch:skip")  # the skipping sweep first
    assert f"SET LOCAL lock_timeout = '{keys._WAIT_LOCK_TIMEOUT}'" in conn.log  # and the waiting one is bounded
    assert conn.log[-1] == "enable" and conn.log.count("enable") == 1


@pytest.mark.asyncio
async def test_the_readers_stay_off_when_a_held_stamp_cannot_be_locked_in_time():
    conn = _Conn(["a", "b"], held={"b"}, fail_wait=True)

    with pytest.raises(TimeoutError):
        await keys.enable_session_sort_keys(conn, _SINCE, batch=10)  # type: ignore[arg-type]

    assert "enable" not in conn.log  # a stale stamp that stays is never served
