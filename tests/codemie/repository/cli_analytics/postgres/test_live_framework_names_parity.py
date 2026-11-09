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

"""Live-Postgres parity of the two spellings of the classifier names (EPMCDME-16427, AC6).

`CLASSIFIER_NAMES_SQL` (one correlated subquery per session: the delivery_framework column and the row's details) and
`classifier_names_by_session_sql` (set-wise, for the framework filter of a whole window) must return the same names
for every source, or the filter lists a different session set than each row's framework shows. Every name source is
seeded: `session_skills`, `invocations_hourly` kind 2 and 4 (and a kind outside them, rows well before the
lower bound, on the bound and after the session), the keys of `skills` and `agents`, and the string elements of
`commands`.

Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable: point it at a disposable, migrated PostgreSQL.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.session_derived import (
    CLASSIFIER_NAMES_SQL,
    classifier_names_by_session_sql,
)
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_START = datetime(2026, 8, 2, 12, 30, tzinfo=timezone.utc)  # the lower bound of the hourly rows: 11:00
_TARGET = "target-name"


async def _engine() -> AnalyticsPgEngine:
    try:
        engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL)))
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return engine
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")


class _Seed:
    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex
        self.email = f"names-{self.suffix}@example.com"

    def sid(self, slug: str) -> str:
        return f"test-names-{self.suffix}-{slug}"

    def filter(self) -> LocalAnalyticsFilter:
        naive = _START.replace(tzinfo=None)
        return LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE,
            start_dt=naive - timedelta(days=1),
            end_dt=naive + timedelta(days=1),
            users=[self.email],
        )

    async def session(
        self,
        conn,
        slug: str,
        minute: int,
        skills: dict | None = None,
        agents: dict | None = None,
        commands: list | None = None,
        started: bool = True,
    ) -> None:
        at = _START + timedelta(minutes=minute)
        await conn.execute(
            "INSERT INTO session_dims (session_id, jwt_email, started_at, last_event_at, skills, agents, commands)"
            " VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb)",
            self.sid(slug),
            self.email,
            at if started else None,
            at,
            json.dumps(skills or {}),
            json.dumps(agents or {}),
            None if commands is None else json.dumps(commands),
        )
        # a session with no activity row is not admitted to `sel` (the window rule): one prompt day makes it count
        await conn.execute(
            "INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, 1)", at.date(), self.sid(slug)
        )

    async def skill(self, conn, slug: str, name: str) -> None:
        await conn.execute("INSERT INTO session_skills (session_id, skill_name) VALUES ($1, $2)", self.sid(slug), name)

    async def hourly(self, conn, slug: str, hour: datetime, kind: int, name: str) -> None:
        await conn.execute(
            "INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success) VALUES ($1,$2,$3,$4,1,1)",
            hour.replace(minute=0, second=0, microsecond=0),
            self.sid(slug),
            kind,
            name,
        )

    async def insert(self, engine: AnalyticsPgEngine) -> None:
        async with engine.acquire() as conn:
            await self.session(conn, "all", 0, {"sk-b": 1, "sk-a": 2, "": 3}, {"ag-1": 1}, ["/cmd", 5, "", None, "x"])
            await self.skill(conn, "all", "ss-one")
            await self.skill(conn, "all", "sk-a")  # the same name from two sources counts once
            await self.hourly(conn, "all", _START, 2, "inv-two")
            await self.hourly(conn, "all", _START, 4, "inv-four")
            await self.hourly(conn, "all", _START, 3, "inv-kind3")  # a kind outside 2 and 4
            await self.hourly(conn, "all", _START, 1, "inv-kind1")
            await self.hourly(conn, "all", _START - timedelta(hours=1), 2, "inv-on-bound")  # 11:00: inside
            await self.hourly(conn, "all", _START - timedelta(hours=2), 2, "inv-before")  # 10:00: outside
            await self.hourly(conn, "all", _START - timedelta(days=30), 4, "inv-far")
            await self.hourly(conn, "all", _START + timedelta(hours=3), 2, "inv-after")  # no upper bound
            await self.session(conn, "bare", 1)  # no source at all: no name, no row set-wise
            await self.session(conn, "empty-name", 2, {"": 1}, None, [""])  # only empty names
            await self.session(conn, "no-start", 3, started=False)  # a NULL start bounds nothing
            await self.hourly(conn, "no-start", _START - timedelta(days=400), 2, "inv-no-start")
            await self.session(conn, "bad-json", 4, None, None, [])
            await conn.execute(
                "UPDATE session_dims SET skills = '[1]'::jsonb, agents = '\"x\"'::jsonb, commands = '{}'::jsonb"
                " WHERE session_id = $1",
                self.sid("bad-json"),
            )  # keys only from an object, elements only from an array: nothing raises, nothing is named
            # one session per source of the target name, and two decoys that must not match
            await self.session(conn, "t-skill-table", 10)
            await self.skill(conn, "t-skill-table", _TARGET)
            await self.session(conn, "t-kind2", 11)
            await self.hourly(conn, "t-kind2", _START, 2, _TARGET)
            await self.session(conn, "t-kind4", 12)
            await self.hourly(conn, "t-kind4", _START - timedelta(hours=1), 4, _TARGET)
            await self.session(conn, "t-skills-key", 13, {_TARGET: 1})
            await self.session(conn, "t-agents-key", 14, None, {_TARGET: 1})
            await self.session(conn, "t-commands", 15, None, None, [_TARGET])
            await self.session(conn, "d-kind3", 16)
            await self.hourly(conn, "d-kind3", _START, 3, _TARGET)
            await self.session(conn, "d-before-bound", 17)
            await self.hourly(conn, "d-before-bound", _START - timedelta(hours=2), 2, _TARGET)

    def ids(self) -> list[str]:
        slugs = ["all", "bare", "empty-name", "no-start", "bad-json", "d-kind3", "d-before-bound"]
        slugs += ["t-skill-table", "t-kind2", "t-kind4", "t-skills-key", "t-agents-key", "t-commands"]
        return [self.sid(s) for s in slugs]

    async def cleanup(self, engine: AnalyticsPgEngine) -> None:
        async with engine.acquire() as conn:
            for table in ("session_skills", "invocations_hourly", "turns_daily", "session_dims"):
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.ids())  # noqa: S608


@pytest.mark.asyncio
async def test_the_set_wise_names_equal_the_per_session_names_for_every_source() -> None:
    engine, seed = await _engine(), _Seed()
    try:
        await seed.insert(engine)
        async with engine.acquire() as conn:
            per_session = {
                r["session_id"]: list(r["names"])
                for r in await conn.fetch(
                    f"SELECT sd.session_id, {CLASSIFIER_NAMES_SQL} AS names FROM session_dims sd"  # noqa: S608
                    " WHERE sd.session_id = ANY($1::text[])",
                    seed.ids(),
                )
                if r["names"] is not None
            }
            set_wise = {
                r["session_id"]: list(r["names"])
                for r in await conn.fetch(
                    "WITH sel AS (SELECT session_id, started_at FROM session_dims WHERE session_id = ANY($1::text[]))"
                    f" SELECT nm.session_id, nm.names FROM {classifier_names_by_session_sql('sel')}",  # noqa: S608
                    seed.ids(),
                )
            }

        assert set_wise == per_session
        # the seed exercises every source and bound, not an empty equality
        assert per_session[seed.sid("all")] == [
            "/cmd", "ag-1", "inv-after", "inv-four", "inv-on-bound", "inv-two", "sk-a", "sk-b", "ss-one", "x",
        ]  # fmt: skip
        assert per_session[seed.sid("no-start")] == ["inv-no-start"]
        for slug in ("bare", "empty-name", "bad-json", "d-kind3", "d-before-bound"):
            assert seed.sid(slug) not in per_session, slug
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_framework_page_keeps_exactly_the_sessions_whose_per_session_names_match() -> None:
    engine, seed = await _engine(), _Seed()
    try:
        await seed.insert(engine)
        async with engine.acquire() as conn:
            expected = {
                r["session_id"]
                for r in await conn.fetch(
                    f"SELECT sd.session_id FROM session_dims sd WHERE sd.session_id = ANY($1::text[])"  # noqa: S608, F541
                    f" AND $2::text = ANY(coalesce({CLASSIFIER_NAMES_SQL}, ARRAY[]::text[]))",
                    seed.ids(),
                    _TARGET,
                )
            }
        assert expected == {
            seed.sid(s) for s in ("t-skill-table", "t-kind2", "t-kind4", "t-skills-key", "t-agents-key", "t-commands")
        }

        total, ids, _ = await PostgresCliAnalyticsReader(engine).get_framework_page(
            seed.filter(), "start_time", 0, 50, lambda sets: [names for names in sets if _TARGET in names]
        )

        assert total == len(expected)
        assert set(ids) == expected
    finally:
        await seed.cleanup(engine)
        await engine.close()
