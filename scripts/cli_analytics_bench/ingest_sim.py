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

"""A concurrent ingest and rollup-refresher stand-in for the benchmarks of EPMCDME-16427. SCRATCH database only.

The real ingest writes raw events and the real refresher recomputes rollups from them; neither can be run without the
whole collector, so this stands in for both with the same writes the Sessions list sees:

- ingest: every 100 ms inserts `rate / 10` new `session_dims` rows (started now, no activity facts, no stamp), the
  state in which a session is in the Sessions window but not yet derived (set S of the reader);
- refresher: every `refresh_s` seconds (the shipped CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS is 30) takes the sessions
  ingested since the last cycle plus `touched` percent of that number of already derived recent sessions, writes their
  `cost_daily` rows (and skills for some), then, in the same transaction, runs the repository's own
  `derive_session_activity` and, when `step` is on, the stored-sort-keys step of `RollupRefresher._write_sort_keys`
  (`read_classifier_names` + `derive_session_sort_keys`); the step is timed alone.

`report()` gives the step times, the sessions per cycle and the heap-only-tuple (HOT) ratio of `session_dims` updates
over the run.
"""

from __future__ import annotations

import asyncio
import statistics
import time

import asyncpg

from codemie.repository.cli_analytics.postgres.rates_version import RatesVersionProvider
from codemie.repository.cli_analytics.postgres.session_activity import derive_session_activity
from codemie.repository.cli_analytics.postgres.session_derived import read_classifier_names
from codemie.repository.cli_analytics.postgres.session_sort_keys import derive_session_sort_keys
from codemie.service.analytics.cli_analytics_jobs import model_rates_by_name
from codemie.service.analytics.handlers.coding_agent_pricing import catalog_rate_fingerprint

_NEW = """
INSERT INTO session_dims (session_id, started_at, last_event_at, repository, branch, jwt_email, owner_email,
    subscription_type, project_origin, project_name, first_prompt, title)
SELECT md5($1 || g), now(), now(), 'repo' || (g % 200), 'main', 'u' || (g % 2000) || '@x.com',
       'u' || (g % 2000) || '@x.com', 'codemie', 'profile', 'p' || (g % 10), 'fix ingest ' || g, 'ingest ' || g
FROM generate_series($2::bigint, $3::bigint) g
RETURNING session_id"""
_TOUCHED = """
SELECT session_id FROM session_dims
WHERE has_activity AND started_at > now() - interval '3 days' AND started_at < now() - interval '1 minute'
ORDER BY random() LIMIT $1"""
_COST = """
INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
    cache_read_tokens, cache_creation_tokens, api_call_count)
SELECT current_date, s.session_id, s.jwt_email,
       CASE WHEN random() < 0.5 THEN 'us.anthropic.claude-sonnet-4-5-20250929-v1:0' ELSE 'claude-4-5-haiku' END,
       'main', 0, (random() * 2e5)::bigint, (random() * 5e4)::bigint, (random() * random() * 5e6)::bigint,
       (random() * 1e5)::bigint, 1 + (random() * 300)::bigint
FROM session_dims s WHERE s.session_id = ANY ($1::text[])
ON CONFLICT (day, session_id, user_email, model_name, query_source, speed, inference_geo, scope_kind, scope_name,
             agent_type)
DO UPDATE SET api_call_count = cost_daily.api_call_count + excluded.api_call_count,
              cache_read_tokens = cost_daily.cache_read_tokens + excluded.cache_read_tokens"""
_SKILLS = """
INSERT INTO session_skills (session_id, skill_name)
SELECT session_id, 'superpowers:brainstorming' FROM unnest($1::text[]) session_id
WHERE ('x' || substr(session_id, 1, 4))::bit(16)::int % 100 < 30 ON CONFLICT DO NOTHING"""
_HOT = "SELECT n_tup_upd, n_tup_hot_upd, n_tup_ins FROM pg_stat_user_tables WHERE relname = 'session_dims'"
TIMEOUT_S = 120.0


class IngestSim:
    def __init__(self, dsn: str, rate: float, refresh_s: float = 30.0, step: bool = True, touched_pct: int = 20):
        self.dsn, self.rate, self.refresh_s, self.step, self.touched_pct = dsn, rate, refresh_s, step, touched_pct
        self.stop = asyncio.Event()
        self.pending: list[str] = []
        self.counter = int(time.time()) * 1000
        self.cycles: list[dict] = []
        self._tasks: list[asyncio.Task] = []
        self._hot0: tuple[int, int, int] | None = None
        self.inserted = 0

    async def _stats(self) -> tuple[int, int, int]:
        await asyncio.sleep(1.5)  # backends flush their counters at most once a second
        conn = await asyncpg.connect(self.dsn, server_settings={"search_path": "codemie_analytics"})
        try:
            row = await conn.fetchrow(_HOT)
            return row["n_tup_upd"], row["n_tup_hot_upd"], row["n_tup_ins"]
        finally:
            await conn.close()

    async def start(self) -> None:
        self._hot0 = await self._stats()
        self._tasks = [asyncio.create_task(self._ingest()), asyncio.create_task(self._refresher())]

    async def _ingest(self) -> None:
        conn = await asyncpg.connect(self.dsn, server_settings={"search_path": "codemie_analytics"})
        per_tick = max(1, round(self.rate / 10))
        tag = f"sim{self.counter}-"
        while not self.stop.is_set():
            tick = time.perf_counter()
            first = self.counter + 1
            self.counter += per_tick
            rows = await conn.fetch(_NEW, tag, first, self.counter)
            self.pending.extend(r[0] for r in rows)
            self.inserted += per_tick
            await asyncio.sleep(max(0.0, 0.1 - (time.perf_counter() - tick)))
        await conn.close()

    async def _refresher(self) -> None:
        conn = await asyncpg.connect(self.dsn, server_settings={"search_path": "codemie_analytics"})
        version = RatesVersionProvider(catalog_rate_fingerprint)
        while True:
            with_stop = asyncio.create_task(self.stop.wait())
            await asyncio.wait({with_stop}, timeout=self.refresh_s)
            final = self.stop.is_set()
            with_stop.cancel()
            new, self.pending = self.pending, []
            touched = [r[0] for r in await conn.fetch(_TOUCHED, len(new) * self.touched_pct // 100)]
            ids = new + touched
            if ids:
                cycle = {"new": len(new), "touched": len(touched)}
                started = time.perf_counter()
                async with conn.transaction():
                    await conn.execute(_COST, ids)
                    await conn.execute(_SKILLS, ids)
                    t0 = time.perf_counter()
                    await derive_session_activity(conn, ids, TIMEOUT_S)
                    cycle["activity_s"] = time.perf_counter() - t0
                    if self.step:
                        t1 = time.perf_counter()
                        names = await read_classifier_names(conn, ids, TIMEOUT_S)
                        await derive_session_sort_keys(
                            conn, ids, names, model_rates_by_name, version.current(), TIMEOUT_S
                        )
                        cycle["step_s"] = time.perf_counter() - t1
                cycle["transaction_s"] = time.perf_counter() - started
                self.cycles.append(cycle)
            if final:
                break
        await conn.close()

    async def finish(self) -> dict:
        self.stop.set()
        await asyncio.gather(*self._tasks)
        upd, hot, ins = await self._stats()
        upd0, hot0, ins0 = self._hot0 or (0, 0, 0)
        return self.report(upd - upd0, hot - hot0, ins - ins0)

    def report(self, updates: int, hot: int, inserts: int) -> dict:
        steps = [c["step_s"] for c in self.cycles if "step_s" in c]
        acts = [c["activity_s"] for c in self.cycles]
        sessions = [c["new"] + c["touched"] for c in self.cycles]

        def pct(values: list[float], q: float) -> float | None:
            return (
                round(sorted(values)[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))] * 1000, 1)
                if values
                else None
            )

        return {
            "rate_per_s": self.rate,
            "refresh_s": self.refresh_s,
            "step": self.step,
            "inserted": self.inserted,
            "cycles": len(self.cycles),
            "sessions_per_cycle_p50": statistics.median(sessions) if sessions else None,
            "sessions_per_cycle_max": max(sessions) if sessions else None,
            "step_ms_p50": pct(steps, 0.5),
            "step_ms_max": pct(steps, 1.0),
            "activity_ms_p50": pct(acts, 0.5),
            "step_sessions_per_s": round(sum(sessions) / sum(steps), 0) if steps else None,
            "session_dims_updates": updates,
            "session_dims_hot_updates": hot,
            "hot_ratio": round(hot / updates, 3) if updates else None,
            "session_dims_inserts": inserts,
        }
