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

"""Live-Postgres checks for EPMCDME-16435: the classifier names of a session whose summary arrived.

A summary fills `session_dims.commands` and leaves `skills` empty, so the slash commands the user typed
(`stlc-factory:suite-health`) must still reach the delivery-framework classifier and the reader's
`get_skill_names_by_session`, from one shared SQL expression.

Skipped when the local analytics Postgres named by `TEST_LOCAL_PG_URL` is unreachable.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.session_derived import update_session_derived
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.delivery_framework import classify_delivery_framework

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
_PREFIX = "test-epmcdme-16435"
_TS = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)
_STLC = "STLC Factory (QA factory)"

_INSERT_DIMS = """
    INSERT INTO session_dims (session_id, skills, commands, summary_ts)
    VALUES ($1, $2::jsonb, $3::jsonb, $4)
"""
_TABLES = ("session_skills", "invocations_hourly", "session_dims")


async def _get_engine() -> AnalyticsPgEngine:
    try:
        engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL)))
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return engine
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")


async def _cleanup(engine: AnalyticsPgEngine, session_id: str) -> None:
    async with engine.acquire() as conn:
        for table in _TABLES:
            await conn.execute(f"DELETE FROM {table} WHERE session_id = $1", session_id)  # noqa: S608


async def _seed_summary_session(
    engine: AnalyticsPgEngine, session_id: str, commands: list | None, skills: dict | None = None
) -> None:
    """A session as a summary leaves it: `summary_ts` set, `skills` empty, `commands` as given."""
    async with engine.acquire() as conn:
        await conn.execute(
            _INSERT_DIMS,
            session_id,
            json.dumps({} if skills is None else skills),
            None if commands is None else json.dumps(commands),
            _TS,
        )


async def _names(engine: AnalyticsPgEngine, session_id: str) -> list[str]:
    rows = await PostgresCliAnalyticsReader(engine).get_skill_names_by_session([session_id])
    assert len(rows) == 1
    return list(rows[0]["skill_names"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_prompt_skill",
    ["stlc-factory:exploratory", "stlc-feature", "stlc-factory:stlc-foundation", "stlc-factory:suite-health"],
)
async def test_summary_commands_classify_stlc(first_prompt_skill: str) -> None:
    engine = await _get_engine()
    session_id = f"{_PREFIX}-commands-{first_prompt_skill.replace(':', '-')}"
    await _cleanup(engine, session_id)
    try:
        commands = ["reload-plugins", first_prompt_skill]
        await _seed_summary_session(engine, session_id, commands)

        names = await _names(engine, session_id)

        assert names == sorted(commands)
        assert classify_delivery_framework(names) == _STLC
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_summary_commands_example_from_ticket() -> None:
    engine = await _get_engine()
    session_id = f"{_PREFIX}-ticket-example"
    await _cleanup(engine, session_id)
    try:
        await _seed_summary_session(engine, session_id, ["reload-plugins", "stlc-factory:suite-health"])

        names = await _names(engine, session_id)

        assert names == ["reload-plugins", "stlc-factory:suite-health"]
        assert classify_delivery_framework(names) == _STLC
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_malformed_commands_elements_dropped() -> None:
    engine = await _get_engine()
    malformed, null_commands = f"{_PREFIX}-malformed", f"{_PREFIX}-null-commands"
    for sid in (malformed, null_commands):
        await _cleanup(engine, sid)
    try:
        await _seed_summary_session(engine, malformed, ["", None, 5, ["x"], "ok"])
        await _seed_summary_session(engine, null_commands, None)

        assert await _names(engine, malformed) == ["ok"]
        assert await _names(engine, null_commands) == []
    finally:
        for sid in (malformed, null_commands):
            await _cleanup(engine, sid)


@pytest.mark.asyncio
async def test_summary_session_unions_dispatch_and_session_skills() -> None:
    engine = await _get_engine()
    session_id = f"{_PREFIX}-union"
    await _cleanup(engine, session_id)
    try:
        await _seed_summary_session(engine, session_id, [])
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success)"
                " VALUES ($1, $2, 2, 'stlc-factory:exploratory', 1, 1)",
                _TS,
                session_id,
            )
            await conn.execute(
                "INSERT INTO session_skills (session_id, skill_name) VALUES ($1, 'superpowers:brainstorming')",
                session_id,
            )

        names = await _names(engine, session_id)

        assert names == ["stlc-factory:exploratory", "superpowers:brainstorming"]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_dispatch_long_after_the_last_dimension_hook_is_still_classified() -> None:
    # `started_at` and `last_event_at` are filled only by dimension hooks, so a kind-2 dispatch can land hours after
    # them: the dispatch names have no upper bound. A dispatch 30 minutes before `started_at` is kept (an hour of
    # margin below it).
    engine = await _get_engine()
    session_id = f"{_PREFIX}-late-dispatch"
    await _cleanup(engine, session_id)
    try:
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO session_dims (session_id, started_at, last_event_at) VALUES ($1, $2, $3)",
                session_id,
                _TS,  # 12:00
                _TS + timedelta(minutes=30),  # 12:30
            )
            for offset, name in (
                (timedelta(minutes=-30), "stlc-factory:before"),
                (timedelta(hours=5), "stlc-factory:suite-health"),
            ):
                await conn.execute(
                    "INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success)"
                    " VALUES ($1, $2, 2, $3, 1, 1)",
                    _TS + offset,
                    session_id,
                    name,
                )
        seen: list[list[str]] = []

        def classify(names: list[str]) -> str:
            seen.append(list(names))
            return classify_delivery_framework(names)

        async with engine.acquire() as conn:
            await update_session_derived(conn, [session_id], classify, timeout=30.0)

        assert await _names(engine, session_id) == ["stlc-factory:before", "stlc-factory:suite-health"]
        assert seen == [["stlc-factory:before", "stlc-factory:suite-health"]]
        async with engine.acquire() as conn:
            stored = await conn.fetchval(
                "SELECT delivery_framework FROM session_dims WHERE session_id = $1", session_id
            )
        assert stored == _STLC
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_list_names_equal_classifier_input() -> None:
    engine = await _get_engine()
    session_id = f"{_PREFIX}-list-equals-classifier"
    await _cleanup(engine, session_id)
    try:
        await _seed_summary_session(
            engine, session_id, ["reload-plugins", "stlc-factory:suite-health", ""], skills={"sdlc-factory:x": 1}
        )
        seen: list[list[str]] = []

        def classify(names: list[str]) -> str:
            seen.append(list(names))
            return classify_delivery_framework(names)

        async with engine.acquire() as conn:
            await update_session_derived(conn, [session_id], classify, timeout=30.0)

        assert len(seen) == 1
        assert await _names(engine, session_id) == seen[0]
    finally:
        await _cleanup(engine, session_id)


async def _insert_hourly(engine: AnalyticsPgEngine, session_id: str, hour: datetime, kind: int, name: str) -> None:
    async with engine.acquire() as conn:
        await conn.execute(
            "INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success) VALUES ($1, $2, $3, $4, 1, 1)",
            hour,
            session_id,
            kind,
            name,
        )


@pytest.mark.asyncio
async def test_dispatch_far_before_the_session_start_is_outside_the_lower_bound() -> None:
    # The only bound is below: from one hour before the hour of `started_at`; an earlier row is not read.
    engine = await _get_engine()
    session_id = f"{_PREFIX}-early-dispatch"
    await _cleanup(engine, session_id)
    try:
        async with engine.acquire() as conn:
            await conn.execute("INSERT INTO session_dims (session_id, started_at) VALUES ($1, $2)", session_id, _TS)
        await _insert_hourly(engine, session_id, _TS - timedelta(hours=5), 2, "stlc-factory:early")
        await _insert_hourly(engine, session_id, _TS - timedelta(hours=1), 2, "stlc-factory:edge")

        assert await _names(engine, session_id) == ["stlc-factory:edge"]
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_a_session_without_started_at_keeps_its_dispatch_names() -> None:
    engine = await _get_engine()
    session_id = f"{_PREFIX}-null-started-at"
    await _cleanup(engine, session_id)
    try:
        async with engine.acquire() as conn:
            await conn.execute("INSERT INTO session_dims (session_id) VALUES ($1)", session_id)
        await _insert_hourly(engine, session_id, _TS - timedelta(days=40), 2, "stlc-factory:suite-health")

        names = await _names(engine, session_id)

        assert names == ["stlc-factory:suite-health"]
        assert classify_delivery_framework(names) == _STLC
    finally:
        await _cleanup(engine, session_id)


@pytest.mark.asyncio
async def test_a_slash_command_row_alone_classifies_a_session_without_summary_or_dispatch() -> None:
    # Kind 4 (the slash-command prompts rollups write) exists before any summary: no `commands`, no dispatch row.
    engine = await _get_engine()
    session_id = f"{_PREFIX}-slash-only"
    await _cleanup(engine, session_id)
    try:
        async with engine.acquire() as conn:
            await conn.execute("INSERT INTO session_dims (session_id, started_at) VALUES ($1, $2)", session_id, _TS)
        await _insert_hourly(engine, session_id, _TS, 4, "stlc-factory:x")

        names = await _names(engine, session_id)

        assert names == ["stlc-factory:x"]
        assert classify_delivery_framework(names) == _STLC
    finally:
        await _cleanup(engine, session_id)
