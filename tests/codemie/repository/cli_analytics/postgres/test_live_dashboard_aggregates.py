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

"""Live-Postgres check of EPMCDME-17052: the statements behind Overview, Repositories and Efficiency
(`get_overview_*`, `get_repository_*`, `get_efficiency_*`) return what the Python aggregation and the
Python pricer over the per-session statements they replace return (`aggregating`, the handler's former arithmetic):
the facts, the ranked and paged groups, the spend and estimate money with its unpriced models, and the dead sessions.

Sessions are seeded straight into the tables under one unique email and every read is filtered to it. Skipped when
`TEST_LOCAL_PG_URL` is not set or unreachable: point it at a disposable PostgreSQL (the live suite migrates it).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import migrations
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.maintenance import (
    MaintenanceReport,
    PartitionMaintainer,
    RetentionPolicy,
)
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.handlers.coding_agent_pricing import model_rates_for
from tests.codemie.repository.cli_analytics.postgres.test_live_session_subagent_cost import (
    _COST_MODEL,
    _KNOWN_MODEL,
    _lookup,
)
from tests.codemie.repository.cli_analytics.support.aggregating import aggregating
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_PRICING = "codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config"
_UNPRICED_MODEL = "not-a-real-model-xyz"
_TABLES = (
    "session_dims",
    "cost_daily",
    "session_usage",
    "turns_daily",
    "tool_facts_daily",
    "session_files_daily",
    "lines_daily",
)
_PER_SESSION = (
    "get_session_cost_facts",
    "get_turns_by_session",
    "get_file_facts_by_session",
    "get_tool_success_by_session",
    "get_session_durations",
    "get_lines_by_session",
    "get_lines_totals",
    "get_class_lists",
    "get_session_model_costs",
    "get_session_funding_costs",
)


async def _engine() -> AnalyticsPgEngine:
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL))
    try:
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    migrations.run_migrations(settings)  # idempotent: a migrated database is left as it is
    today = datetime.now(timezone.utc).date()  # a fresh database has the partitions of the days around today only
    maintainer = PartitionMaintainer(engine, RetentionPolicy.from_settings(settings), clock=lambda: today)
    async with engine.acquire() as conn:
        await maintainer._ensure_partitions(conn, today, MaintenanceReport())
    return engine


class _Seed:
    """Eleven sessions of one user: (number, repository, branch, project, subscription, origin, funding route, summary
    turns, summary tool calls, cost rows [(model, input, output, cache read, calls)], subagent-only cache read, turns,
    tool facts, files [(path, written, edited)], lines). Session 6 has no repository, 10 is Personal-paid, 11 has only
    subagent totals, 5 has no cost, 6 an unpriced model and 3 files without tool facts."""

    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex[:10]
        self.email = f"agg-{self.suffix}@example.com"
        self.t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=5)
        k, c, u = _KNOWN_MODEL, _COST_MODEL, _UNPRICED_MODEL
        self.rows = [
            (1, "repo-a", "main", "P1", "codemie", "profile", "api_based", None, None, [(c, 100, 20, 600, 6)], None, 3, True, [("a", 1, 0), ("b", 0, 1)], (5, 2)),
            (2, "repo-a", "main", "P2", "codemie", "profile", "subscription", 7, 40, [(c, 200, 30, 600, 6), (k, 50, 5, 0, 1)], None, 0, True, [], None),
            (3, "repo-a", "dev", "P1", "corporate", "linked_folder", None, None, None, [(k, 80, 10, 90, 3)], None, 12, False, [("c", 1, 0)], (0, 0)),
            (4, "repo-b", "main", "", "codemie", "profile", "weird", None, None, [(c, 70, 10, 50, 2)], None, 30, True, [("d", 1, 0)], (3, 0)),
            (5, "repo-b", "main", "P1", "codemie", "profile", "api_based", None, 9, [], None, 1, True, [], None),
            (6, "", "", "P3", "codemie", "linked_folder", "api_based", None, None, [(u, 100, 10, 100, 7)], None, 60, True, [("e", 0, 1)], (0, 4)),
            (7, "repo-c", "dev", "P1", "codemie", "profile", "subscription", None, None, [(k, 10, 5, 10, 0)], None, 0, False, [], None),
            (8, "repo-a", "main", "P1", "codemie", "profile", "api_based", None, None, [(c, 100, 20, 600, 6)], None, 2, True, [], None),
            (9, "repo-b", "feature", "P2", "corporate", "profile", "subscription", 0, 0, [(k, 5, 5, 5, 5)], None, 51, True, [("f", 1, 1)], (7, 7)),
            (10, "repo-c", "dev", "P1", "personal", "profile", "subscription", None, None, [(c, 400, 40, 400, 4)], None, 4, True, [], None),
            (11, "repo-d", "main", "P4", "codemie", "profile", "api_based", None, None, [], 300, 2, True, [], None),
        ]  # fmt: skip

    def sid(self, number: int) -> str:
        return f"test-agg-{self.suffix}-{number:02d}"

    def ids(self) -> list[str]:
        return [self.sid(row[0]) for row in self.rows]

    def filter(self) -> LocalAnalyticsFilter:
        naive = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)  # noqa: E731
        return LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE,
            start_dt=naive(self.t0 - timedelta(days=1)),
            end_dt=naive(self.t0 + timedelta(days=1)),
            users=[self.email],
        )

    async def insert(self, engine: AnalyticsPgEngine) -> None:
        day = self.t0.date()
        async with engine.acquire() as conn:
            for n, repo, branch, project, sub, origin, route, s_turns, s_tools, costs, subagent, *rest in self.rows:
                turns, tools, files, lines = rest
                sid, started = self.sid(n), self.t0 + timedelta(minutes=n)
                await conn.execute(
                    "INSERT INTO session_dims (session_id, started_at, last_event_at, repository, branch, project_name,"
                    " jwt_email, subscription_type, project_origin, funding_route, first_prompt, summary_ts, turns,"
                    " tool_calls) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)",
                    sid, started, started + timedelta(minutes=1), repo, branch, project, self.email, sub, origin, route,
                    f"prompt {n}", started if s_turns is not None or s_tools is not None else None, s_turns, s_tools,
                )  # fmt: skip
                for model, tokens_in, tokens_out, cache_read, calls in costs:
                    await conn.execute(
                        "INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd,"
                        " input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, api_call_count)"
                        " VALUES ($1, $2, $3, $4, 'main', 0, $5, $6, $7, 0, $8)",
                        day, sid, self.email, model, tokens_in, tokens_out, cache_read, calls,
                    )  # fmt: skip
                if subagent:
                    await conn.execute(
                        "INSERT INTO session_usage (session_id, day, model, speed, inference_geo, scope_kind,"
                        " scope_name, agent_id, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,"
                        " attrs) VALUES ($1, $2, $3, '', '', '', '', 'ag1', 100, 20, $4, 0, $5::jsonb)",
                        sid, day, _COST_MODEL, subagent, json.dumps({"token_source": "subagent_usage"}),
                    )  # fmt: skip
                if turns:
                    await conn.execute(
                        "INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, $3)", day, sid, turns
                    )
                if tools:
                    await conn.execute(
                        "INSERT INTO tool_facts_daily (day, session_id, tool_calls, agent_count, skill_count)"
                        " VALUES ($1, $2, 5, 0, 0)",
                        day, sid,
                    )  # fmt: skip
                for path, written, edited in files:
                    await conn.execute(
                        "INSERT INTO session_files_daily (day, session_id, file_path, is_written, is_edited)"
                        " VALUES ($1, $2, $3, $4, $5)",
                        day, sid, path, bool(written), bool(edited),
                    )  # fmt: skip
                if lines:
                    await conn.execute(
                        "INSERT INTO lines_daily (day, session_id, user_email, model_name, lines_added, lines_removed)"
                        " VALUES ($1, $2, $3, $4, $5, $6)",
                        day, sid, self.email, _COST_MODEL, lines[0], lines[1],
                    )  # fmt: skip
            await conn.execute(  # the stored activity facts the selection's started arm reads
                "UPDATE session_dims SET has_activity = true, resumed = false, last_activity_day = $2"
                " WHERE session_id = ANY($1::text[])",
                self.ids(),
                day,
            )

    async def cleanup(self, engine: AnalyticsPgEngine) -> None:
        async with engine.acquire() as conn:
            for table in _TABLES:
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.ids())  # noqa: S608


def _norm(value):
    return float(value) if isinstance(value, Decimal) else value


def _same(actual, expected) -> bool:
    """Lists as sorted lists (the order of an aggregate is not part of the contract); money within a millionth of a
    dollar (a sum of 9-decimal rounded parts differs from a sum of floats by far less than a dollar figure shows)."""
    if isinstance(
        expected, list
    ):  # an aggregate over no row is NULL where the oracle has []: the handler reads both as none
        return sorted(actual or []) == sorted(expected)
    if isinstance(expected, (float, Decimal)):
        return actual is not None and abs(float(actual) - float(expected)) <= 1e-6
    return actual == expected


_IDENTITY = ("key", "project_name", "repository", "branch", "funding_route", "personal_billed", "subscription_type")


def _assert_parts_equal(got: list[dict], want: list[dict], parts: tuple[str, ...], label) -> None:
    """For every part the oracle names, the same rows; the oracle's columns decide what is compared."""
    for part in parts:
        actual = {tuple(r.get(k) for k in _IDENTITY): r for r in got if r["part"] == part}
        expected = {tuple(r.get(k) for k in _IDENTITY): r for r in want if r["part"] == part}
        if part == "total" and not actual and all(not r.get("cost_usd") for r in expected.values()):
            continue  # a selection without a session has no money row; the oracle reports an all-zero one
        assert actual.keys() == expected.keys(), (label, part, sorted(map(str, actual)), sorted(map(str, expected)))
        for k, row in expected.items():
            wrong = {c: (actual[k].get(c), v) for c, v in row.items() if not _same(actual[k].get(c), v)}
            assert not wrong, (label, part, k, wrong)


@pytest.mark.asyncio
async def test_the_dashboard_statements_equal_the_python_aggregation_and_pricer():
    engine, seed = await _engine(), _Seed()
    try:
        await seed.insert(engine)
        real, f = PostgresCliAnalyticsReader(engine), seed.filter()
        oracle = aggregating(SimpleNamespace(**{name: getattr(real, name) for name in _PER_SESSION}))
        with patch(_PRICING, _lookup):
            rates = [model_rates_for(model) for model in await real.get_cost_models(f)]
            assert len(await real.get_session_cost_facts(f, include_costless=True)) == len(seed.rows)

            _assert_parts_equal(
                await real.get_overview_facts(f), await oracle.get_overview_facts(f), ("facts",), "facts"
            )
            got, want = await real.get_overview_money(f, rates), await oracle.get_overview_money(f, rates)
            _assert_parts_equal(got, want, ("money", "dead"), "overview")
            assert [r["unpriced_models"] for r in got if r["part"] == "money" and r["key"] == "spend"] == [
                [_UNPRICED_MODEL]
            ]

            for include_branches in (True, False):
                for search in (None, "repo-a", "REPO_"):
                    for page, per_page in ((0, 2), (1, 2), (0, None), (9, 2)):
                        label = (include_branches, search, page, per_page)
                        got = await real.get_repository_ranking(f, include_branches, search, rates, page, per_page)
                        want = await oracle.get_repository_ranking(f, include_branches, search, rates, page, per_page)
                        _assert_parts_equal(got, want, ("group", "count", "total", "bucket"), label)
                        named = [r["repository"] for r in got if r["part"] == "group" and r["repository"]]
                        unattributed = any(r["part"] == "group" and r["repository"] is None for r in got)
                        got = await real.get_repository_facts(f, include_branches, search, named, unattributed)
                        want = await oracle.get_repository_facts(f, include_branches, search, named, unattributed)
                        _assert_parts_equal(got, want, ("group",), label)
                        if not include_branches:
                            got = await real.get_repository_buckets(f, search, rates, named, unattributed)
                            want = await oracle.get_repository_buckets(f, search, rates, named, unattributed)
                            _assert_parts_equal(got, want, ("bucket",), label)

            # What the oracle comparison above only implies, stated where each case discriminates:
            page = await real.get_repository_ranking(f, False, None, rates, 0, 2)
            groups = [r for r in page if r["part"] == "group"]
            spend = next(r for r in page if r["part"] == "total" and r["key"] == "spend")
            assert [r["repository"] for r in groups] == [r["repository"] for r in groups if r["repository"]]
            assert spend["unpriced_models"] == [_UNPRICED_MODEL]  # the unpriced session is off the page, still reported
            searched = await real.get_repository_ranking(f, False, "repo-a", rates, 0, 2)
            assert next(r for r in searched if r["part"] == "total" and r["key"] == "spend")["unpriced_models"] == []
            branches = [r for r in await real.get_repository_ranking(f, True, None, rates) if r["part"] == "group"]
            assert {r["project_name"] for r in branches if r["repository"] == "repo-a"} == {
                "P1",
                "P2",
            }  # one repository
            by_repo = {r["repository"]: r for r in branches if r["branch"] == "main"}
            assert by_repo["repo-d"]["cost_usd"] > 0  # subagent totals only: priced, listed
            estimates = await real.get_repository_ranking(f, True, None, rates)
            routes = sum(r["cost_usd"] for r in estimates if r["part"] == "total" and r["key"] == "route")
            assert routes > next(r for r in estimates if r["part"] == "total" and r["key"] == "spend")["cost_usd"]
            assert any(
                r["part"] == "bucket" and r["personal_billed"] is True for r in estimates
            )  # Personal-paid: not spend

            _assert_parts_equal(
                await real.get_efficiency_facts(f),
                await oracle.get_efficiency_facts(f),
                ("worst", "depth", "all", "code"),
                "efficiency facts",
            )
            # The worst session: sessions 1 and 8 tie on the ratio (100), the lowest id wins; its prompt and title are read
            # for it alone (a NULL title reads as ''); a selection without a call has no worst session.
            worst = next(r for r in await real.get_efficiency_facts(f) if r["part"] == "worst")
            assert (worst["session_id"], worst["prompt"], worst["session_title"]) == (seed.sid(1), "prompt 1", "")
            no_calls = f.restricted_to([seed.sid(5), seed.sid(7), seed.sid(11)])
            got = await real.get_efficiency_facts(no_calls)
            _assert_parts_equal(
                got, await oracle.get_efficiency_facts(no_calls), ("worst", "depth", "all", "code"), "no calls"
            )
            assert not any(r["part"] == "worst" for r in got)
            got, want = await real.get_efficiency_money(f, rates), await oracle.get_efficiency_money(f, rates)
            _assert_parts_equal(got, want, ("money", "dead"), "efficiency")
    finally:
        await seed.cleanup(engine)
