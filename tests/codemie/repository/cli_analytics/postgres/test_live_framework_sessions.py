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

"""Live-Postgres checks for EPMCDME-16435: `get_framework_sessions`, the per (user, repo) framework counts.

Skipped when the local analytics Postgres named by `TEST_LOCAL_PG_URL` is unreachable.
"""

import os
from datetime import datetime, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
_PREFIX = "test-epmcdme-16435-fw"  # each test seeds under `<prefix>-<test>-` so parallel workers do not collide
_STLC = "STLC Factory (QA factory)"
_FALLBACK = "Pure chat"
_RENAMED_FALLBACK = "Old plain chat"  # a stored label of a fallback that was renamed afterwards
_LABELS = [_STLC, "SDLC Factory"]  # the current non-fallback labels
_EMAIL = "Owner@X.com"
_START = datetime(2026, 8, 2, tzinfo=timezone.utc)
_END = datetime(2026, 8, 3, tzinfo=timezone.utc)
_INSIDE = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)
_AT_END = _END  # the window is [start, end): a session started exactly at `end` is outside
_OUTSIDE = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)

_INSERT_DIMS = """
    INSERT INTO session_dims (session_id, jwt_email, repo_remote, repository, delivery_framework, started_at)
    VALUES ($1, $2, $3, $4, $5, $6)
"""


async def _get_engine() -> AnalyticsPgEngine:
    try:
        engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL)))
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return engine
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")


async def _cleanup(engine: AnalyticsPgEngine, test: str) -> None:
    async with engine.acquire() as conn:
        await conn.execute("DELETE FROM session_dims WHERE session_id LIKE $1", f"{_PREFIX}-{test}-%")


async def _seed(engine: AnalyticsPgEngine, test: str, slug: str, email, remote, repository, framework, started) -> None:
    async with engine.acquire() as conn:
        await conn.execute(_INSERT_DIMS, f"{_PREFIX}-{test}-{slug}", email, remote, repository, framework, started)


async def _mine(engine: AnalyticsPgEngine, test: str) -> list[tuple[str, str, str, int]]:
    """The rows of the test's seeded sessions only (the database may hold other sessions)."""
    rows = await PostgresCliAnalyticsReader(engine).get_framework_sessions(_START, _END, _LABELS)
    return sorted(r for r in rows if f"16435-{test}" in r[1] or f"16435-{test}" in r[2])


@pytest.mark.asyncio
async def test_counts_current_framework_sessions_per_user_and_repo() -> None:
    engine = await _get_engine()
    test, remote = "counts", "https://git.example.com/test-epmcdme-16435-counts/repo.git"
    await _cleanup(engine, test)
    try:
        await _seed(engine, test, "stlc", _EMAIL, remote, "repo", _STLC, _INSIDE)
        await _seed(engine, test, "chat", "owner@x.com", remote, "repo", _FALLBACK, _INSIDE)
        await _seed(engine, test, "null", "OWNER@x.com", remote, "repo", None, _INSIDE)
        await _seed(engine, test, "renamed", _EMAIL, remote, "repo", _RENAMED_FALLBACK, _INSIDE)
        await _seed(engine, test, "outside", _EMAIL, remote, "repo", _STLC, _OUTSIDE)
        await _seed(engine, test, "at-end", _EMAIL, remote, "repo", _STLC, _AT_END)

        rows = await _mine(engine, test)

        assert rows == [("owner@x.com", remote, "repo", 1)]
    finally:
        await _cleanup(engine, test)


@pytest.mark.asyncio
async def test_remote_and_repository_stay_raw_and_null_becomes_empty() -> None:
    engine = await _get_engine()
    test, remote = "raw", "https://git.example.com/test-epmcdme-16435-raw/repo.git"
    await _cleanup(engine, test)
    try:
        await _seed(engine, test, "no-remote", _EMAIL, None, "test-epmcdme-16435-raw-local", _STLC, _INSIDE)
        await _seed(engine, test, "no-repo", _EMAIL, remote, None, _STLC, _INSIDE)

        rows = await _mine(engine, test)

        assert rows == sorted(
            [
                ("owner@x.com", "", "test-epmcdme-16435-raw-local", 1),
                ("owner@x.com", remote, "", 1),
            ]
        )
    finally:
        await _cleanup(engine, test)


_INSERT_PERSON_DIMS = """
    INSERT INTO session_dims (session_id, jwt_email, dev_name_max, developer_name, repo_remote, repository,
                              delivery_framework, started_at)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""


async def _seed_person(
    engine: AnalyticsPgEngine, test: str, slug: str, jwt_email, dev_name_max, developer_name, remote: str
) -> None:
    """A framework session inside the window whose person is named by the three columns (None or empty allowed)."""
    async with engine.acquire() as conn:
        await conn.execute(
            _INSERT_PERSON_DIMS,
            f"{_PREFIX}-{test}-{slug}",
            jwt_email,
            dev_name_max,
            developer_name,
            remote,
            "repo",
            _STLC,
            _INSIDE,
        )


@pytest.mark.asyncio
async def test_sessions_of_one_person_and_repo_are_counted_together() -> None:
    engine = await _get_engine()
    test, remote = "twice", "https://git.example.com/test-epmcdme-16435-twice/repo.git"
    await _cleanup(engine, test)
    try:
        await _seed(engine, test, "stlc", _EMAIL, remote, "repo", _STLC, _INSIDE)
        await _seed(engine, test, "sdlc", "owner@x.com", remote, "repo", "SDLC Factory", _INSIDE)

        rows = await _mine(engine, test)

        assert rows == [("owner@x.com", remote, "repo", 2)]
    finally:
        await _cleanup(engine, test)


@pytest.mark.asyncio
async def test_the_person_is_the_jwt_email_else_the_largest_developer_name_else_the_developer_name() -> None:
    engine = await _get_engine()
    test, remote = "person", "https://git.example.com/test-epmcdme-16435-person/repo.git"
    await _cleanup(engine, test)
    try:
        await _seed_person(engine, test, "jwt-wins", "Winner@X.com", "Max.Loser", "Dev.Loser", remote)
        await _seed_person(engine, test, "null-jwt", None, "Max.Name@Corp.com", "Dev.Loser", remote)
        await _seed_person(engine, test, "empty-jwt", "", "Other.Max", None, remote)
        await _seed_person(engine, test, "null-max", None, None, "Dev.Only", remote)
        await _seed_person(engine, test, "empty-max", "", "", "Dev.Empty", remote)

        rows = await _mine(engine, test)

        assert rows == sorted(
            [
                ("winner@x.com", remote, "repo", 1),
                ("max.name@corp.com", remote, "repo", 1),
                ("other.max", remote, "repo", 1),
                ("dev.only", remote, "repo", 1),
                ("dev.empty", remote, "repo", 1),
            ]
        )
    finally:
        await _cleanup(engine, test)


@pytest.mark.asyncio
async def test_a_session_without_any_person_is_absent() -> None:
    engine = await _get_engine()
    test, remote = "nobody", "https://git.example.com/test-epmcdme-16435-nobody/repo.git"
    await _cleanup(engine, test)
    try:
        await _seed_person(engine, test, "all-null", None, None, None, remote)
        await _seed_person(engine, test, "all-empty", "", "", "", remote)
        await _seed_person(engine, test, "named", "Owner@X.com", None, None, remote)

        rows = await _mine(engine, test)

        assert rows == [("owner@x.com", remote, "repo", 1)]
    finally:
        await _cleanup(engine, test)
