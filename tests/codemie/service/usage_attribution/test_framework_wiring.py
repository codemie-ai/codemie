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

"""A5 framework sessions counted in the CLI analytics Postgres: the rows are read in the API process before the job is
dispatched (only when the evidence is read now), and merged into the Elasticsearch session rows in the worker."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from codemie.clients.postgres import PostgresClient
from codemie.configs.customer_config import CustomerConfig
from codemie.repository.cli_analytics import factory
from codemie.repository.cli_analytics.ports import CliAnalyticsStorageConfigError
from codemie.service.analytics import delivery_framework
from codemie.service.usage_attribution import app_db, postgres_sources, worker
from codemie.service.usage_attribution.analysis import (
    AnalysisService,
    AnalysisUnavailableError,
    _gunzip_json,
    resolve_range,
)
from codemie.service.usage_attribution.constants import INPUTS_VERSION, PG_DEGRADED_TTL_S
from codemie.service.usage_attribution.fetch import Fetcher
from codemie.service.usage_attribution.settings import build_defaults, build_effective
from tests.codemie.service.usage_attribution.builders import app_db as app_db_facts
from tests.codemie.service.usage_attribution.builders import inputs, person, run, settings_with, tool
from tests.codemie.service.usage_attribution.test_analysis_service import FakeRunner, NoRedis, _gz, counted, eff, rng
from tests.codemie.service.usage_attribution.test_worker import MONTH, _Session, _unzip, job

PG_ROWS = [("u1@x.com", "https://github.com/Acme/Core.git", "/home/u1/core", 1)]
AGGREGATED = [["u1@x.com", "acme/core", 1]]


class FakeReader:
    def __init__(self, rows=None, error: Exception | None = None, delay: float = 0.0):
        self.rows = rows if rows is not None else PG_ROWS
        self.error = error
        self.delay = delay
        self.calls: list[tuple] = []

    async def get_framework_sessions(self, start, end, framework_labels):
        self.calls.append((start, end, list(framework_labels)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.rows


class LogRecorder:
    """The module logger of this application does not propagate to caplog: record the warnings itself."""

    def __init__(self):
        self.text = ""

    def warning(self, msg, *args, **_kwargs):
        self.text += (msg % args if args else msg) + "\n"


@pytest.fixture
def warnings(monkeypatch):
    log = LogRecorder()
    monkeypatch.setattr("codemie.service.usage_attribution.postgres_sources.logger", log)
    monkeypatch.setattr("codemie.service.usage_attribution.framework_merge.logger", log)
    return log


class Clock:
    """The monotonic clock of the in-process cache, moved by the test: entries expire when it is advanced."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr("codemie.service.usage_attribution.cache.time", fake)
    return fake


class FakeStorage:
    def __init__(self, reader):
        self.reader = reader


@pytest.fixture
def enabled(monkeypatch):
    """The CLI analytics feature is on and its storage hands out `reader` (set `enabled.reader`)."""
    state = type("State", (), {"reader": FakeReader(), "feature": True})()
    monkeypatch.setattr(
        CustomerConfig, "is_feature_enabled", lambda self, name: state.feature and name == "cliAnalytics"
    )
    monkeypatch.setattr(factory, "get_cli_analytics_storage", lambda: FakeStorage(state.reader))
    monkeypatch.setattr(
        delivery_framework, "get_framework_labels", lambda: ["STLC Factory", "SDLC Factory", "Plain chat"]
    )
    return state


async def rows_now():
    r = rng()
    return await postgres_sources.pg_framework_rows(r.evidence_start, r.end)


def service(runner=None):
    return AnalysisService(cache=NoRedis(), job_runner=runner or FakeRunner())


class Numbered(FakeRunner):
    """Every job marks its meta and its summary section with its own number, so a cached copy shows whose it is."""

    def __call__(self, job):
        result = super().__call__(job)
        n = len(self.jobs)
        result["sections"] = {**result["sections"], "summary": _gz({"tiles": {"cli": n}})}
        result["meta"] = {**result["meta"], "es_ms": float(n)}
        return result


async def last_good(svc):
    """(meta es_ms, summary tile) of the last good copy of the default range, or None when there is none."""
    handle = await svc._last_good(rng(), eff())
    if handle is None:
        return None
    summary = _gunzip_json(await svc.cache.get(f"{handle.prefix}:summary"))
    return handle.meta["es_ms"], summary["tiles"]["cli"]


@pytest.mark.asyncio
class TestRowsBeforeDispatch:
    async def test_aggregated_rows_for_the_evidence_window(self, enabled):
        r = rng()
        assert await postgres_sources.pg_framework_rows(r.evidence_start, r.end) == AGGREGATED
        assert enabled.reader.calls == [(r.evidence_start, r.end, ["STLC Factory", "SDLC Factory"])]
        assert r.end == datetime(2026, 10, 13, 14, 45, tzinfo=timezone.utc)

    async def test_feature_off_gives_no_rows_and_no_query(self, enabled):
        enabled.feature = False
        assert await rows_now() == []
        assert enabled.reader.calls == []

    async def test_a_storage_build_failure_is_a_failed_read_logged_at_warning(self, enabled, monkeypatch, warnings):
        def refuse():
            raise CliAnalyticsStorageConfigError("refused for secret-token")

        monkeypatch.setattr(factory, "get_cli_analytics_storage", refuse)
        assert await rows_now() is None
        assert "CliAnalyticsStorageConfigError" in warnings.text
        assert "secret-token" not in warnings.text

    async def test_a_failing_feature_check_is_a_failed_read_instead_of_an_error(self, enabled, monkeypatch, warnings):
        def broken(self, name):
            raise RuntimeError("customer config unreadable")

        monkeypatch.setattr(CustomerConfig, "is_feature_enabled", broken)
        assert await rows_now() is None
        assert "RuntimeError" in warnings.text

    async def test_a_broken_label_configuration_is_a_failed_read_not_an_error(self, enabled, monkeypatch, warnings):
        def broken():
            raise ValueError("bad rule pattern for acme/core")

        monkeypatch.setattr(delivery_framework, "get_framework_labels", broken)
        assert await rows_now() is None
        assert enabled.reader.calls == []
        assert "ValueError" in warnings.text
        assert "acme/core" not in warnings.text

    async def test_failing_query_is_a_failure_and_logs_no_personal_data(self, enabled, warnings):
        enabled.reader = FakeReader(error=RuntimeError("boom for secret.person@x.com in acme/core"))
        assert await rows_now() is None
        assert "RuntimeError" in warnings.text
        assert "secret.person@x.com" not in warnings.text
        assert "acme/core" not in warnings.text

    async def test_a_slow_query_is_a_failure_after_the_timeout(self, enabled, warnings, monkeypatch):
        monkeypatch.setattr(postgres_sources, "PG_FRAMEWORK_TIMEOUT_S", 0.05)
        enabled.reader = FakeReader(delay=5.0)
        assert await rows_now() is None
        assert "TimeoutError" in warnings.text

    async def test_empty_result_gives_no_rows(self, enabled):
        enabled.reader = FakeReader(rows=[])
        assert await rows_now() == []

    async def test_the_fallback_label_is_never_asked_for_even_when_a_rule_shares_it(self, enabled, monkeypatch):
        monkeypatch.setattr(
            delivery_framework,
            "get_framework_labels",
            lambda: ["STLC Factory", "Plain chat", "SDLC Factory", "STLC Factory", "Plain chat"],
        )
        await rows_now()
        assert enabled.reader.calls[0][2] == ["STLC Factory", "SDLC Factory"]

    async def test_without_rule_labels_nothing_is_queried_and_it_is_not_a_failure(self, enabled, monkeypatch):
        monkeypatch.setattr(delivery_framework, "get_framework_labels", lambda: ["Plain chat", "Plain chat"])
        assert await rows_now() == []
        assert enabled.reader.calls == []


@pytest.mark.asyncio
class TestDispatch:
    async def test_the_rows_go_into_the_job_when_the_evidence_is_read_now(self, enabled):
        runner = FakeRunner()
        await service(runner).get_analysis(rng(), eff(), "u", {})
        assert runner.jobs[0]["evidence_blob"] is None
        assert runner.jobs[0]["pg_framework_rows"] == AGGREGATED
        assert len(enabled.reader.calls) == 1

    async def test_a_reused_evidence_runs_no_query(self, enabled):
        runner = FakeRunner()
        svc = service(runner)
        await svc.get_analysis(rng(), eff(), "u", {})
        week = resolve_range(
            "last_7_days", None, None, 90, 30, now=datetime(2026, 10, 13, 14, 52, 31, tzinfo=timezone.utc)
        )
        await svc.get_analysis(week, eff(), "u", {})
        assert runner.jobs[1]["evidence_blob"] == b"evidence"
        assert not runner.jobs[1].get("pg_framework_rows")
        assert len(enabled.reader.calls) == 1  # the first job's read only

    async def test_a_failed_read_leaves_no_inputs_or_evidence_for_other_ranges(self, enabled):
        enabled.reader = FakeReader(error=RuntimeError("down"))
        runner = FakeRunner()
        svc = service(runner)
        await svc.get_analysis(rng(), eff(), "u", {})
        assert not runner.jobs[0]["pg_framework_rows"]
        week = resolve_range(
            "last_7_days", None, None, 90, 30, now=datetime(2026, 10, 13, 14, 52, 31, tzinfo=timezone.utc)
        )
        await svc.get_analysis(week, eff(), "u", {})
        assert runner.jobs[1]["evidence_blob"] is None  # the Elasticsearch-only evidence was not stored
        assert runner.jobs[1]["inputs_blob"] is None
        assert len(enabled.reader.calls) == 2  # the next request reads again

    async def test_a_failed_read_is_not_kept_for_the_hour(self, enabled, clock):
        enabled.reader = FakeReader(error=RuntimeError("down"))
        runner = Numbered()
        svc = service(runner)
        first = await svc.get_analysis(rng(), eff(), "u", {})
        assert (first.cache, first.meta["ttl_s"]) == ("miss", PG_DEGRADED_TTL_S)
        assert (await svc.get_analysis(rng(), eff(), "u", {})).cache == "hit"  # within the short lifetime
        assert len(enabled.reader.calls) == 1
        assert await last_good(svc) is None  # no complete analysis existed: the ES-only one is not kept as one
        enabled.reader = FakeReader()  # Postgres is back
        clock.advance(PG_DEGRADED_TTL_S + 1)
        again = await svc.get_analysis(rng(), eff(), "u", {})
        assert (again.cache, again.meta["ttl_s"]) == ("miss", 15 * 60)  # computed anew, the complete analysis
        assert len(enabled.reader.calls) == 1  # the new reader was asked once, by the same range
        assert [j["pg_framework_rows"] for j in runner.jobs] == [[], AGGREGATED]
        assert (await svc.get_analysis(rng(), eff(), "u", {})).cache == "hit"
        assert await last_good(svc) == (2.0, 2)

    async def test_a_degraded_analysis_leaves_the_complete_last_good_copy_intact(self, enabled, clock):
        svc = service(Numbered())
        first = await svc.get_analysis(rng(), eff(), "u", {})
        assert first.meta["ttl_s"] == 15 * 60
        assert await last_good(svc) == (1.0, 1)  # the complete analysis
        enabled.reader = FakeReader(error=RuntimeError("down"))
        clock.advance(15 * 60 + 1)  # the analysis itself expired, the last good copy did not
        stale = await svc.get_analysis(rng(), eff(), "u", {})
        assert (stale.cache, stale.extra["refreshing"]) == ("stale", True)
        await asyncio.gather(*svc._inflight.values())
        assert len(enabled.reader.calls) == 1
        current = await svc.get_analysis(rng(), eff(), "u", {})
        assert (current.cache, current.meta["ttl_s"], current.meta["es_ms"]) == ("hit", PG_DEGRADED_TTL_S, 2.0)
        assert await last_good(svc) == (1.0, 1)  # not replaced by the Elasticsearch-only one (second job)

    async def test_a_successful_read_is_kept_for_the_full_time(self, enabled, clock):
        runner = FakeRunner()
        svc = service(runner)
        first = await svc.get_analysis(rng(), eff(), "u", {})
        assert first.meta["ttl_s"] == 15 * 60
        clock.advance(PG_DEGRADED_TTL_S + 1)
        assert (await svc.get_analysis(rng(), eff(), "u", {})).cache == "hit"
        assert len(runner.jobs) == 1
        assert len(enabled.reader.calls) == 1

    async def test_a_failed_merge_in_the_worker_is_kept_as_briefly_as_a_failed_read(self, enabled):
        class MergeFailed(FakeRunner):
            def __call__(self, job):
                result = super().__call__(job)
                result["meta"] = {**result["meta"], "postgres_merge_failed": True}
                return result

        runner = MergeFailed()
        svc = service(runner)
        first = await svc.get_analysis(rng(), eff(), "u", {})
        assert first.meta["ttl_s"] == PG_DEGRADED_TTL_S
        week = resolve_range(
            "last_7_days", None, None, 90, 30, now=datetime(2026, 10, 13, 14, 52, 31, tzinfo=timezone.utc)
        )
        await svc.get_analysis(week, eff(), "u", {})
        assert runner.jobs[1]["evidence_blob"] is None  # the Elasticsearch-only evidence was not stored for the hour
        assert runner.jobs[1]["inputs_blob"] is None
        assert await last_good(svc) is None  # and it is not the last good copy either

    async def test_shutdown_while_the_job_is_completed_starts_no_worker(self, enabled):
        runner = FakeRunner()
        svc = service(runner)

        class ClosingReader(FakeReader):
            async def get_framework_sessions(self, start, end, framework_labels):
                svc.shutdown()  # the application stops while the rows are being read
                return await super().get_framework_sessions(start, end, framework_labels)

        enabled.reader = ClosingReader()
        with pytest.raises(AnalysisUnavailableError) as exc:
            await svc.get_analysis(rng(), eff(), "u", {})
        assert "restarting" in exc.value.message
        assert runner.jobs == []
        assert await counted(svc, "u") == (0, 0)  # the computation gave its counts back

    async def test_a_busy_refusal_reads_nothing_from_postgres(self, enabled):
        svc = service()
        e = eff()
        e.values["analysisTimeoutSeconds"] = 0.1
        await svc._slots["default"].acquire()  # another default computation is running
        with pytest.raises(AnalysisUnavailableError):
            await svc.get_analysis(rng(), e, "u", {})
        assert enabled.reader.calls == []

    async def test_the_feature_off_job_carries_no_rows(self, enabled):
        enabled.feature = False
        runner = FakeRunner()
        await service(runner).get_analysis(rng(), eff(), "u", {})
        assert not runner.jobs[0].get("pg_framework_rows")


# ---------------------------------------------------------------------------------------------------------------------
# Worker merge
# ---------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def sessions_read(monkeypatch):
    """No ES and no app DB, except: the A5 read returns ES rows, and the people of the app DB are `people`."""
    monkeypatch.setattr(worker, "_max_buckets", lambda client: 65536)
    monkeypatch.setattr(PostgresClient, "get_engine", classmethod(lambda cls: None))
    monkeypatch.setattr("sqlmodel.Session", _Session)
    static = {"shared": [], "projects": [], "budgets": [], "datasources": [], "members": []}
    monkeypatch.setattr(app_db, "load_static", lambda session, timings: dict(static))
    monkeypatch.setattr(
        app_db, "load_funding", lambda session, timings: {"assignments": [], "allocations": [], "stops": []}
    )
    state = type("State", (), {})()
    state.es_sess = [["u1", "acme/core", 3, 0, 0, 0], ["u2", "other/repo", 2, 0, 0, 1]]
    state.people = [["u1", "Una One", "", "", "", "", "", "", "u1@x.com"]]
    state.load_people_uids = []

    def load_people(session, uids, timings, enrichment=True):
        state.load_people_uids.append(list(uids))
        timings.append({"query": "people", "rows": len(state.people), "ms": 0.0})
        return state.people

    monkeypatch.setattr(app_db, "load_people", load_people)

    def execute(self, specs):
        for sp in specs:
            if sp.key in ("persons", "ev_persons"):
                sp.result = {"u1": {"name": "u1", "spend": 1.0, "pairs": {}}}
            elif sp.key == "sess":
                sp.result = [list(r) for r in state.es_sess]
            else:
                sp.result = sp.new_acc()

    monkeypatch.setattr(Fetcher, "execute", execute)
    monkeypatch.setattr(worker, "_es_client", lambda concurrency: object())
    return state


def read_job(pg_rows=None, **kw) -> dict:
    j = job(MONTH, **kw)
    if pg_rows is not None:
        j["pg_framework_rows"] = pg_rows
    return j


def sess_of(evidence: bytes) -> dict:
    return {(r[0], r[1]): r for r in _unzip(evidence)["sess"]}


class TestWorkerMerge:
    def test_postgres_framework_raises_the_elasticsearch_count(self, sessions_read):
        inputs_, evidence = worker.fetch_inputs(read_job([["u1@x.com", "acme/core", 1]]))
        assert sess_of(evidence)[("u1", "acme/core")] == ["u1", "acme/core", 3, 0, 0, 1]
        assert inputs_["sess"] == _unzip(evidence)["sess"]
        assert sess_of(evidence)[("u2", "other/repo")][5] == 1  # no Postgres row: the ES value stays
        assert sessions_read.load_people_uids == [["u1", "u2"]]  # one read for the merge and for the analysis people
        assert [q["query"] for q in inputs_["meta"]["db_queries"]].count("people") == 1
        assert [p[0] for p in inputs_["app_db"]["people"]] == ["u1"]  # only the people of the analysis are kept

    def test_the_count_never_exceeds_the_sessions(self, sessions_read):
        _inputs, evidence = worker.fetch_inputs(read_job([["u1@x.com", "acme/core", 9]]))
        assert sess_of(evidence)[("u1", "acme/core")][5] == 3

    def test_a_failing_merge_keeps_the_elasticsearch_rows(self, sessions_read, warnings):
        _inputs, evidence = worker.fetch_inputs(read_job([["u1@x.com", "acme/core"]]))  # a row of the wrong shape
        assert sess_of(evidence)[("u1", "acme/core")][5] == 0
        assert "ValueError" in warnings.text
        assert "u1@x.com" not in warnings.text

    def test_a_failing_merge_is_flagged_in_the_meta_and_a_good_one_is_not(self, sessions_read, warnings):
        failed, _evidence = worker.fetch_inputs(read_job([["u1@x.com", "acme/core"]]))  # a row of the wrong shape
        assert failed["meta"]["postgres_merge_failed"] is True
        merged, _evidence = worker.fetch_inputs(read_job([["u1@x.com", "acme/core", 1]]))
        assert "postgres_merge_failed" not in merged["meta"]
        none, _evidence = worker.fetch_inputs(read_job([]))
        assert "postgres_merge_failed" not in none["meta"]

    def test_a_job_without_rows_leaves_the_rows_unchanged(self, sessions_read):
        for pg_rows in (None, []):
            _inputs, evidence = worker.fetch_inputs(read_job(pg_rows))
            assert sess_of(evidence)[("u1", "acme/core")][5] == 0
        assert sessions_read.load_people_uids == [["u1"], ["u1"]]  # the analysis people only

    def test_reused_evidence_is_not_merged(self, sessions_read):
        _inputs, evidence = worker.fetch_inputs(read_job())
        sessions_read.load_people_uids.clear()
        pg_rows = [["u1@x.com", "acme/core", 3]]
        inputs_, again = worker.fetch_inputs(read_job(pg_rows, evidence_blob=evidence))
        assert again is None
        assert {(r[0], r[1]): r[5] for r in inputs_["sess"]}[("u1", "acme/core")] == 0
        assert sessions_read.load_people_uids == [["u1"]]


# ---------------------------------------------------------------------------------------------------------------------
# Analysis and cache key
# ---------------------------------------------------------------------------------------------------------------------
def test_a_merged_row_makes_the_pair_a_framework_pair():
    data = inputs(
        {"u1": person("u1@x.com", {"acme/core": (10,)})},
        tools=[tool("u1", "acme/core", sessions=2, days=2)],
        sess=[["u1", "acme/core", 3, 0, 0, 1]],
        app_db=app_db_facts(),
    )
    signals = {x["id"] for x in run(data, settings_with()).K["acme/core"]["signals"]}
    assert "how.framework" in signals
    data["sess"] = [["u1", "acme/core", 3, 0, 0, 0]]
    assert "how.framework" not in {x["id"] for x in run(data, settings_with()).K["acme/core"]["signals"]}


def test_the_inputs_version_changes_the_fetch_hash(monkeypatch):
    assert INPUTS_VERSION == "4"
    current = build_effective(build_defaults({}), {}).fetch_hash
    monkeypatch.setattr("codemie.service.usage_attribution.constants.INPUTS_VERSION", "3")
    assert build_effective(build_defaults({}), {}).fetch_hash != current


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cached_blobs", "reads"),
    [((b"inputs", None), 0), ((None, b"evidence"), 0), ((None, None), 1)],
    ids=["inputs", "evidence", "nothing"],
)
async def test_postgres_is_read_only_when_nothing_is_cached(enabled, monkeypatch, cached_blobs, reads):
    runner = FakeRunner()
    svc = service(runner)

    async def cached(self, rng_, inputs_key, quarter_key, hour_key):
        return cached_blobs

    monkeypatch.setattr(AnalysisService, "_load_cached_inputs", cached)
    await svc.get_analysis(rng(), eff(), "u", {})
    assert runner.jobs[0]["pg_framework_rows"] == (AGGREGATED if reads else [])
    assert len(enabled.reader.calls) == reads


class TestSessionsQueryAndParsing:
    def test_sessions_body_counts_framework_sessions_by_regexp(self):
        from codemie.service.usage_attribution import queries

        body = queries.sessions_body(
            datetime(2026, 10, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 2, tzinfo=timezone.utc),
            "stlc.*",
            None,
            10,
            5,
        )
        repos = body["aggs"]["users"]["aggs"]["repos"]
        assert set(repos["aggs"]) == {"sessions", "mcp_any", "mcp_work", "framework"}
        assert "stlc.*" in str(repos["aggs"]["framework"]["filter"])

    def test_parse_sessions_reads_the_framework_count(self):
        from codemie.service.usage_attribution import parsing

        bucket = {
            "key": "u1",
            "repos": {
                "buckets": [
                    {
                        "key": "acme/core",
                        "sessions": {"value": 3},
                        "mcp_any": {"s": {"value": 1}},
                        "mcp_work": {"s": {"value": 0}},
                        "framework": {"s": {"value": 2}},
                    }
                ]
            },
        }
        assert parsing.parse_sessions([bucket]) == [["u1", "acme/core", 3, 1, 0, 2]]

    def test_pipeline_build_sess_and_key_for_use_the_merged_rows(self):
        from codemie.service.usage_attribution.pipeline import Analysis

        rows = [["u1", "acme/core", 3, 0, 0, 1]]
        assert Analysis._build_sess(rows)[("u1", "acme/core")] == {
            "sessions": 3,
            "mcp_any": 0,
            "mcp_work": 0,
            "framework": 1,
        }
