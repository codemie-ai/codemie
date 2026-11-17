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

"""Live-Postgres proof of EPMCDME-17317: a Desktop-hosted session's own auto-generated chat title must not reach
the served prompt or title search. Exercises the real ingest, rollup and reader SQL end to end — the `entrypoint`
column added to `_sessions_cte`'s `sel`, `get_session_cost_facts`, `get_session_detail_meta`, `get_efficiency_facts`
and the title-search arm — none of which any other test runs against a real database; the reader tests only assert
the generated SQL text.

Three sessions share one window: a Code tab (`claude-desktop-3p`) and a Cowork (`local-agent`) session, each with
Claude Desktop's own auto-generated chat title and a distinct first prompt, and a plain `cli` session with a real
`--name`. Skipped (not failed) when the local analytics Postgres is unreachable.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.configs.llm_config import CostConfig
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from codemie.service.llm_service.llm_service import llm_service
from tests.codemie.repository.cli_analytics.postgres.test_ingestor import _gate
from tests.codemie.repository.cli_analytics.support.cowork_session import cowork_session
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE, EXCLUDE_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

# Two days back: the next UTC day's requests are already in the past, and the day never leaves a retention window.
_DAY = datetime.now(timezone.utc).date() - timedelta(days=2)
_START = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)

# `cowork_session`'s own usage events always price at this model and route; mocked here so get_sessions,
# get_session_detail and get_efficiency can price them without reaching the real catalog.
_OPUS_55 = "claude-opus-5-5"
_OPUS_55_ROUTE = "bedrock/eu.anthropic.claude-opus-5-5"
_OPUS_55_RATES = (5e-6, 25e-6, 0.5e-6, 6.25e-6, 6.25e-6)
_OPUS_55_ROUTE_RATES = (3e-6, 15e-6, 0.3e-6, 3.75e-6, 3.75e-6)


async def _get_engine() -> AnalyticsPgEngine:
    try:
        settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_URL=_LOCAL_PG_URL))
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        await PostgresAnalyticsRuntime(engine, settings).start()
        return engine
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")


async def _cleanup(engine: AnalyticsPgEngine, session_ids: list[str]) -> None:
    """Remove the sessions from every analytics table that has a session_id: raw rows, rollups and the queue."""
    async with engine.acquire() as conn:
        tables = await conn.fetch(
            """SELECT DISTINCT table_name FROM information_schema.columns
               WHERE table_schema = 'codemie_analytics' AND column_name = 'session_id'"""
        )
        for row in tables:
            await conn.execute(
                f"DELETE FROM codemie_analytics.{row['table_name']} WHERE session_id = ANY($1::text[])",  # noqa: S608
                session_ids,
            )


async def _refresh(engine: AnalyticsPgEngine, day) -> None:
    async with live_rollup_lock(engine):
        refresher = RollupRefresher(engine, batch_size=100)
        async with engine.acquire() as conn:
            await refresher.mark_dirty(day, day, conn)
        await refresher.refresh()


def _cost(rates: tuple) -> CostConfig:
    return CostConfig(
        input=rates[0],
        output=rates[1],
        cache_read_input_token_cost=rates[2],
        cache_creation_input_token_cost=rates[3],
        cache_creation_input_token_cost_above_1hr=rates[4],
    )


def _entry(name: str, rates: tuple) -> MagicMock:
    entry = MagicMock()
    entry.base_name, entry.deployment_name, entry.cost = name, None, _cost(rates)
    return entry


@contextmanager
def _catalog(models: list, routes: dict[str, CostConfig]):
    with (
        patch.object(llm_service, "get_all_llm_model_info", return_value=models),
        patch.object(llm_service, "_litellm_route_costs", routes),
        patch.object(llm_service, "catalog_source", return_value="litellm"),
    ):
        yield


def _session_events(
    session_id: str, project: str, start: datetime, entrypoint: str, title: str, first_prompt: str
) -> list[dict[str, Any]]:
    """`cowork_session`'s own 16 events (a prompt writing a file, a subagent run, a routed and a plain request, a
    final summary), re-stamped with `entrypoint`, Claude Desktop's own auto-title and a distinct first prompt —
    everything else stays a genuine, costed session."""
    events = []
    for event in cowork_session(session_id, project, start):
        event = {**event, "entrypoint": entrypoint}
        if event["type"] == "agent.session.start":
            event["session_title"] = title
        elif event["type"] == "agent.prompt.submit" and event["event_id"].endswith(":u1"):
            event["prompt_body"] = first_prompt
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_live_desktop_entrypoint_sessions_are_served_and_searched_by_first_prompt_not_title() -> None:
    """A Code tab (`claude-desktop-3p`) and a Cowork (`local-agent`) session each carry Desktop's own
    auto-generated chat title and are served and found by their first prompt instead, in the Sessions list,
    Session Detail and Efficiency's `worst_session_prompt` — while a plain `cli` session with a real `--name`
    keeps showing and matching its title."""
    project = f"t17317-{uuid.uuid4().hex[:8]}"
    ids = {name: f"t17317-{name}-{uuid.uuid4().hex[:8]}" for name in ("code_tab", "cowork", "cli")}
    entrypoints = {"code_tab": "claude-desktop-3p", "cowork": "local-agent", "cli": "cli"}
    titles = {
        "code_tab": "Hello world text and PDF",
        "cowork": "Helloworld.txt to PDF",
        "cli": "Renamed via --name",
    }
    prompts = {
        "code_tab": "Hey Desktop claude, run a few skills",
        "cowork": "hey cowork, run a few skills",
        "cli": "a plain first prompt",
    }
    events = [
        event
        for name, sid in ids.items()
        for event in _session_events(sid, project, _START, entrypoints[name], titles[name], prompts[name])
    ]
    engine = await _get_engine()
    try:
        ingestor = PostgresTelemetryIngestor(engine, clock=lambda: _DAY)
        gate = _gate(None, canonical_project=project)
        await ingestor.ingest_hook_events(events, "desktop.user@example.com", 0, gate=gate)
        await _refresh(engine, _DAY)

        day = _START.replace(hour=0)
        window = LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=day, end_dt=day + timedelta(days=1)).restricted_to(
            list(ids.values())
        )

        with _catalog([_entry(_OPUS_55, _OPUS_55_RATES)], {_OPUS_55_ROUTE: _cost(_OPUS_55_ROUTE_RATES)}):
            handler = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine))
            listed, _, _ = await handler.get_sessions(window, 0, 10, "start_time", None)
            by_id = {row["trace_id"]: row for row in listed["sessions"]}

            details = {}
            worst_prompt = {}
            for name, sid in ids.items():
                detail, _, _ = await handler.get_session_detail(sid, scope=DETAIL_SCOPE)
                assert detail is not None
                details[name] = detail
                efficiency, _ = await handler.get_efficiency(window.restricted_to([sid]))
                worst_prompt[name] = efficiency["kpis"]["worst_session_prompt"]

            found_by_desktop_title, _, _ = await handler.get_sessions(window, 0, 10, "start_time", titles["code_tab"])
            found_by_cli_title, _, _ = await handler.get_sessions(window, 0, 10, "start_time", titles["cli"])
    finally:
        await _cleanup(engine, list(ids.values()))
        await engine.close()

    for name in ("code_tab", "cowork"):
        assert by_id[ids[name]]["prompt"] == prompts[name]
        assert details[name]["prompt"] == prompts[name]
        assert worst_prompt[name] == prompts[name]

    assert by_id[ids["cli"]]["prompt"] == titles["cli"]
    assert details["cli"]["prompt"] == titles["cli"]
    assert worst_prompt["cli"] == titles["cli"]

    assert found_by_desktop_title["sessions"] == []
    assert [row["trace_id"] for row in found_by_cli_title["sessions"]] == [ids["cli"]]
