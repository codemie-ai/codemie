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


"""Connection fakes of the partition maintainer's tests: they record statements and answer the
session purge."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, date, datetime

import asyncpg

from codemie.repository.cli_analytics.postgres.maintenance import RetentionPolicy

POLICY = RetentionPolicy(raw_days=90, rollup_days=365, dedup_days=14, premake_weeks=4)
TODAY = date(2026, 9, 23)  # a Wednesday


NO_CANDIDATE = (0, 0, 0, [], [])  # a session purge batch that found no idle session
NOT_TAKEN = (0, 0, False, False)  # a part whose session_dims row is held, no longer idle or gone
NONE_LEFT = (0, [])  # the count after a session purge that left no idle session
COUNT = "SELECT count(*), (array_agg(session_id))[1:10] FROM session_dims WHERE"  # the count's prefix
PURGE_STARTED = datetime(2026, 9, 23, 2, 15, tzinfo=UTC)  # the database time the session purge reads


class RecordingConnection:
    """Records every statement; `today` answers the database-date query; `batches` are the
    (candidates, sessions, rows, held ids, heavy ids) answers of the session purge batches in turn,
    NO_CANDIDATE once they run out; `parts` are the (taken, rows, held, done) answers of the parts of
    heavy sessions in turn, NOT_TAKEN once they run out; `counts` are the (count, ids) answers of the
    count of idle sessions left in turn, NONE_LEFT once they run out; `clock_timestamp()` answers
    PURGE_STARTED."""

    def __init__(
        self,
        today: date = TODAY,
        batches: tuple[tuple[int, int, int, list[str], list[str]], ...] = (),
        parts: tuple[tuple[int, int, bool, bool], ...] = (),
        counts: tuple[tuple[int, list[str]], ...] = (),
    ) -> None:
        self.statements: list[str] = []
        self.args: list[tuple[str, tuple[object, ...]]] = []  # (statement, bind arguments) of every execute
        self.transactions: list[list[str]] = []  # the statements of each transaction, in order
        self._open: list[str] | None = None
        self.today = today
        self.batches = list(batches)
        self.parts = list(parts)
        self.counts = list(counts)

    async def execute(self, sql: str, *args: object, timeout: float | None = None) -> str:
        statement = " ".join(sql.split())
        self.statements.append(statement)
        self.args.append((statement, args))
        if self._open is not None:
            self._open.append(statement)
        return "DELETE 0"

    async def fetchval(self, sql: str, *args: object, timeout: float | None = None) -> object:
        statement = " ".join(sql.split())
        self.statements.append(statement)
        self.args.append((statement, args))
        if self._open is not None:
            self._open.append(statement)
        if "clock_timestamp()" in sql:
            return PURGE_STARTED
        return self.today if "now()" in sql else 0

    async def fetchrow(self, sql: str, *args: object, timeout: float | None = None) -> tuple[object, ...]:
        statement = " ".join(sql.split())
        self.statements.append(statement)
        self.args.append((statement, args))
        if self._open is not None:
            self._open.append(statement)
        if statement.startswith("WITH dims"):
            return self.parts.pop(0) if self.parts else NOT_TAKEN
        if statement.startswith(COUNT):
            return self.counts.pop(0) if self.counts else NONE_LEFT
        return self.batches.pop(0) if self.batches else NO_CANDIDATE

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[None]:
        self._open = []
        self.transactions.append(self._open)
        try:
            yield
        finally:
            self._open = None

    def transaction(self) -> AbstractAsyncContextManager[None]:
        return self._tx()


SERVER_TIMEOUT = asyncpg.exceptions.QueryCanceledError("canceling statement due to statement timeout")


class FailingConnection(RecordingConnection):
    """Fails every statement that contains `needle`, or only the `nth` of them (from 1); knows no
    existing partition."""

    def __init__(
        self,
        needle: str,
        error: BaseException = SERVER_TIMEOUT,
        nth: int | None = None,
        batches: tuple[tuple[int, int, int, list[str], list[str]], ...] = (),
        parts: tuple[tuple[int, int, bool, bool], ...] = (),
        counts: tuple[tuple[int, list[str]], ...] = (),
    ) -> None:
        super().__init__(batches=batches, parts=parts, counts=counts)
        self.needle = needle
        self.error = error
        self.nth = nth
        self._matched = 0

    def _fail(self, sql: str) -> None:
        if self.needle in sql:
            self._matched += 1
            if self.nth is None or self._matched == self.nth:
                raise self.error

    async def execute(self, sql: str, *args: object, timeout: float | None = None) -> str:
        self._fail(sql)
        return await super().execute(sql, *args, timeout=timeout)

    async def fetchval(self, sql: str, *args: object, timeout: float | None = None) -> object:
        self._fail(sql)
        return await super().fetchval(sql, *args, timeout=timeout)

    async def fetchrow(self, sql: str, *args: object, timeout: float | None = None) -> tuple[object, ...]:
        self._fail(sql)
        return await super().fetchrow(sql, *args, timeout=timeout)

    async def fetch(self, sql: str, *args: object) -> list[object]:
        return []


class PoolTimeoutConnection(RecordingConnection):
    """Times statements out like asyncpg: one without `timeout=` gets the pool's command
    timeout (a dashboard query's limit), which moving or purging a large table outlasts."""

    POOL_TIMEOUT_S = 35
    SLOW = ("WITH moved AS", "DELETE FROM", "ATTACH PARTITION", "WITH batch AS", "WITH dims AS", COUNT)
    DURATION_S = 120  # past the pool's limit, within the raised one

    def _run(self, sql: str, timeout: float | None) -> None:
        if any(s in sql for s in self.SLOW) and (timeout or self.POOL_TIMEOUT_S) < self.DURATION_S:
            raise TimeoutError

    async def execute(self, sql: str, *args: object, timeout: float | None = None) -> str:
        self._run(sql, timeout)
        return await super().execute(sql, *args, timeout=timeout)

    async def fetchval(self, sql: str, *args: object, timeout: float | None = None) -> object:
        self._run(sql, timeout)
        return await super().fetchval(sql, *args, timeout=timeout)

    async def fetchrow(self, sql: str, *args: object, timeout: float | None = None) -> tuple[object, ...]:
        self._run(sql, timeout)
        return await super().fetchrow(sql, *args, timeout=timeout)
