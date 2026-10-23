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

"""Analysis service: ranges, cache levels, single flight, limits, failures and refresh; the shared cache."""

from __future__ import annotations

import asyncio
import gzip
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from codemie.service.usage_attribution import analysis as analysis_module
from codemie.service.usage_attribution import limits
from codemie.service.usage_attribution.analysis import (
    AnalysisService,
    AnalysisUnavailableError,
    RefreshTooSoonError,
    resolve_range,
)
from codemie.service.usage_attribution.cache import SharedCache
from codemie.service.usage_attribution.limits import CustomLimitError, start_custom_computation
from codemie.service.usage_attribution.settings import build_defaults, build_effective
from tests.codemie.service.usage_attribution import process_targets

NOW = datetime(2026, 10, 13, 14, 52, 31, tzinfo=timezone.utc)


def _gz(obj) -> bytes:
    return gzip.compress(json.dumps(obj).encode())


class NoRedis(SharedCache):
    """The in-process store only (as when Redis is down)."""

    def _client(self):
        return None


class FakeRunner:
    def __init__(self, fail: Exception | None = None, delay: float = 0.0):
        self.jobs = []
        self.fail = fail
        self.delay = delay
        self.lock = threading.Lock()

    def __call__(self, job: dict) -> dict:
        with self.lock:
            self.jobs.append(job)
        if self.delay:
            import time

            time.sleep(self.delay)
        if self.fail:
            raise self.fail
        read = not job["inputs_blob"]
        return {
            "sections": {"summary": _gz({"tiles": {"cli": 1}}), "people": _gz([{"uid": "u1"}]), "pdetail:3": _gz({})},
            "inputs_blob": b"inputs" if read else None,
            "evidence_blob": b"evidence" if read and not job.get("evidence_blob") else None,
            "meta": {"es_ms": 1.0, "headline": {"coverage": {"share": 99.9}}},
        }


class EvidenceByEnd(FakeRunner):
    """Evidence read now is labelled with the end of its window, so a later job shows whose read it reused."""

    def __call__(self, job: dict) -> dict:
        result = super().__call__(job)
        if result["evidence_blob"]:
            result["evidence_blob"] = f"evidence to {job['evidence_window']['to']}".encode()
        return result


@pytest.fixture
def spawnable(monkeypatch):
    """Spawned processes import the test entry points by name: they need the repository root on their path."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[4]))


@pytest.fixture
def admissions(monkeypatch) -> list[str]:
    """The users of every computation that asked to be admitted (counted against the limits)."""
    calls: list[str] = []
    admit = analysis_module.admit_computation

    async def recording(cache, user_id, *args):
        calls.append(user_id)
        return await admit(cache, user_id, *args)

    monkeypatch.setattr(analysis_module, "admit_computation", recording)
    return calls


def service(runner=None):
    return AnalysisService(cache=NoRedis(), job_runner=runner or FakeRunner())


async def counted(svc: AnalysisService, user_id: str) -> tuple[int, ...]:
    """The user's computations counted this hour: all of them, and those with custom settings."""
    blobs = await svc.cache.mget([limits._all_count_key(user_id), limits._count_key(user_id)])
    return tuple(int(blob or 0) for blob in blobs)


def eff(**overrides):
    return build_effective(build_defaults({}), overrides)


def rng():
    return resolve_range("last_30_days", None, None, 90, 30, now=NOW)


class TestRanges:
    def test_end_is_rounded_down_to_15_minutes(self):
        r = resolve_range("last_30_days", None, None, 90, 30, now=NOW)
        assert r.end == datetime(2026, 10, 13, 14, 45, tzinfo=timezone.utc)
        assert r.start == datetime(2026, 9, 13, tzinfo=timezone.utc)
        assert r.evidence_start == r.start
        assert r.slow_slot == datetime(2026, 10, 13, 14, tzinfo=timezone.utc)  # the hour its evidence serves
        assert "previous_from" not in r.info()  # no previous period is read

    def test_hour_periods_follow_the_slot(self):
        a = resolve_range("last_24_hours", None, None, 90, 30, now=NOW)
        b = resolve_range("last_24_hours", None, None, 90, 30, now=NOW + timedelta(minutes=5))
        assert (a.start, a.end) == (b.start, b.end)

    def test_short_ranges_use_a_longer_evidence_window(self):
        r = resolve_range("last_7_days", None, None, 90, 30, now=NOW)
        assert r.evidence_start == datetime(2026, 9, 13, tzinfo=timezone.utc)  # 30 days back, from midnight
        assert r.end - r.evidence_start >= timedelta(days=30)
        # Every period ending at the same time shares the evidence window of the 30-day period.
        month = resolve_range("last_30_days", None, None, 90, 30, now=NOW)
        day = resolve_range("last_24_hours", None, None, 90, 30, now=NOW)
        assert r.evidence_window() == month.evidence_window() == day.evidence_window() == month.window()

    def test_slow_read_keys(self):
        svc = service()
        month, week = rng(), resolve_range("last_7_days", None, None, 90, 30, now=NOW)
        early = resolve_range("last_30_days", None, None, 90, 30, now=NOW.replace(minute=7))
        # One evidence read for every period whose evidence starts in the same hour, for the whole hour.
        assert svc.evidence_key(month, eff()) == svc.evidence_key(week, eff()) == svc.evidence_key(early, eff())
        assert svc.evidence_key(month, eff(repositoriesPerPerson=15)) != svc.evidence_key(month, eff())
        # A clamped range starts later at every slot; its evidence is still read once an hour.
        year = [resolve_range("last_year", None, None, 90, 30, now=NOW.replace(minute=m)) for m in (7, 52)]
        assert year[0].start != year[1].start
        assert svc.evidence_key(year[0], eff()) == svc.evidence_key(year[1], eff())

    def test_slow_reads_are_kept_until_the_end_of_their_hour(self):
        early = resolve_range("last_30_days", None, None, 90, 30, now=NOW.replace(minute=7))
        now = NOW.replace(minute=8)
        left = (datetime(2026, 10, 13, 15, tzinfo=timezone.utc) - now).total_seconds()
        assert analysis_module._slow_reads_ttl(early, 900, now) == int(left) + 120
        assert analysis_module._slow_reads_ttl(rng(), 900, NOW) == 900  # late in the hour: as long as the analysis

    def test_log_note_names_the_reused_evidence(self):
        note = analysis_module._inputs_note
        assert note(b"x", {"evidence_reused": True}) == "cached"
        assert note(None, {"evidence_reused": True}) == "read, evidence reused"
        assert note(None, {"evidence_reused": False}) == "read"
        assert note(None, {}) == "read"

    def test_ranges_are_clamped_to_the_maximum(self):
        r = resolve_range("last_year", None, None, 90, 30, now=NOW)
        assert r.clamped is True
        assert r.end - r.start == timedelta(days=90)
        assert r.requested_start < r.start

    def test_custom_range(self):
        r = resolve_range(None, NOW - timedelta(days=10), NOW - timedelta(days=2), 90, 30, now=NOW)
        assert r.spec.startswith("custom:")
        assert r.days == pytest.approx(8, abs=0.02)

    @pytest.mark.parametrize(
        ("period", "start", "end"),
        [
            ("next_week", None, None),
            ("", None, None),
            (None, NOW, NOW - timedelta(days=1)),
            (None, None, NOW + timedelta(days=1)),
            # Dates near year 1 would overflow the date arithmetic: a 400, not a 500.
            (None, datetime(1, 1, 1, tzinfo=timezone.utc), datetime(1, 1, 2, tzinfo=timezone.utc)),
        ],
    )
    def test_invalid_ranges(self, period, start, end):
        with pytest.raises(ValueError):
            resolve_range(period, start, end, 90, 30, now=NOW)


def test_two_analyses_stay_decoded():
    """A second period or a Recompute on the same pod must not evict the first analysis's lists."""
    from codemie.service.usage_attribution import analysis
    from codemie.service.usage_attribution.constants import LIST_SECTIONS

    assert 2 * len(LIST_SECTIONS) <= analysis._DECODED_MAX


@pytest.mark.asyncio
class TestService:
    async def test_miss_then_hit(self):
        runner = FakeRunner()
        svc = service(runner)
        first = await svc.get_analysis(rng(), eff(), "u", {"framework_regex": "x"})
        second = await svc.get_analysis(rng(), eff(), "u", {"framework_regex": "x"})
        assert (first.cache, second.cache) == ("miss", "hit")
        assert len(runner.jobs) == 1
        sections = await svc.load_sections(second, ["summary", "people", "pdetail:3"])
        assert sections["summary"] == {"tiles": {"cli": 1}}
        assert second.meta["headline"]["coverage"]["share"] == 99.9

    async def test_single_flight(self):
        runner = FakeRunner(delay=0.2)
        svc = service(runner)
        handles = await asyncio.gather(*[svc.get_analysis(rng(), eff(), "u", {}) for _ in range(5)])
        assert len(runner.jobs) == 1
        assert len({h.prefix for h in handles}) == 1

    async def test_single_flight_when_cache_reads_yield(self):
        """Like Redis, every cache read suspends: concurrent requests must still share one computation."""

        class YieldingCache(NoRedis):
            async def get(self, key):
                await asyncio.sleep(0.01)
                return await super().get(key)

            async def mget(self, keys):
                await asyncio.sleep(0.01)
                return await super().mget(keys)

        runner = FakeRunner(delay=0.2)
        svc = AnalysisService(cache=YieldingCache(), job_runner=runner)
        handles = await asyncio.gather(*[svc.get_analysis(rng(), eff(), "u", {}) for _ in range(6)])
        assert len(runner.jobs) == 1
        assert {h.cache for h in handles} == {"miss"}

    async def test_display_overrides_reuse_the_default_analysis(self):
        runner = FakeRunner()
        svc = service(runner)
        await svc.get_analysis(rng(), eff(), "u", {})
        handle = await svc.get_analysis(rng(), eff(sharedProjectThresholdPer30Days=10), "u", {})
        assert handle.cache == "hit"
        assert len(runner.jobs) == 1

    async def test_classification_overrides_reuse_the_cached_inputs(self):
        runner = FakeRunner()
        svc = service(runner)
        await svc.get_analysis(rng(), eff(), "u", {})
        await svc.get_analysis(rng(), eff(minimumConfidence=55), "u", {})
        assert runner.jobs[0]["inputs_blob"] is None
        assert runner.jobs[1]["inputs_blob"] == b"inputs"
        assert runner.jobs[1]["settings"]["minimumConfidence"] == 55

    async def test_retrieval_overrides_read_again(self):
        runner = FakeRunner()
        svc = service(runner)
        await svc.get_analysis(rng(), eff(), "u", {})
        await svc.get_analysis(rng(), eff(repositoriesPerPerson=15), "u", {})
        assert runner.jobs[1]["inputs_blob"] is None

    async def test_custom_computations_are_limited(self):
        runner = FakeRunner()
        svc = service(runner)
        for i in range(10):
            await svc.get_analysis(rng(), eff(minimumConfidence=30 + i), "u", {})
        with pytest.raises(CustomLimitError) as exc:
            await svc.get_analysis(rng(), eff(minimumConfidence=95), "u", {})
        assert exc.value.retry_after > 0
        # Another user is not affected, nor is the default analysis.
        await svc.get_analysis(rng(), eff(minimumConfidence=95), "other", {})
        await svc.get_analysis(rng(), eff(), "u", {})
        # A cache hit with custom settings is not a computation.
        handle = await svc.get_analysis(rng(), eff(minimumConfidence=30), "u", {})
        assert handle.cache == "hit"

    async def test_one_custom_computation_at_a_time(self):
        cache = NoRedis()
        await start_custom_computation(cache, "u", 10, 120)
        with pytest.raises(CustomLimitError, match="already running"):
            await start_custom_computation(cache, "u", 10, 120)

    async def test_over_the_limit_the_last_good_analysis_answers_with_the_time_to_wait(self, admissions):
        runner = FakeRunner()
        svc = service(runner)
        custom = eff(minimumConfidence=55)
        custom.values["tuning"]["maxCustomComputationsPerHour"] = 1
        await svc.get_analysis(rng(), custom, "u", {})
        # The next window of the same period and settings is over the limit.
        later = resolve_range("last_30_days", None, None, 90, 30, now=NOW + timedelta(minutes=15))
        first = await svc.get_analysis(later, custom, "u", {})
        assert first.cache == "stale"  # may be marked refreshing: the limit refuses inside the computation
        await asyncio.gather(*svc._inflight.values(), return_exceptions=True)
        # The refusal is remembered: the next request shows the last good analysis with the limit, and stops polling.
        again = await svc.get_analysis(later, custom, "u", {})
        assert again.cache == "stale" and not again.extra.get("refreshing")
        assert again.extra["error"] == (
            "the limit of 1 computation with custom settings an hour is reached; try again in 60 minutes, "
            "or reset to the defaults."
        )
        assert svc._inflight == {}
        assert admissions == ["u", "u"]  # the first computation and the refused one: nothing started again
        assert len(runner.jobs) == 1
        assert await counted(svc, "u") == (1, 1)  # the refused computation counted nothing

    async def test_over_the_limit_without_an_earlier_analysis(self, admissions):
        runner = FakeRunner()
        svc = service(runner)
        e = eff()
        e.values["tuning"]["maxComputationsPerHour"] = 1
        await svc.get_analysis(rng(), e, "u", {})
        week = resolve_range("last_7_days", None, None, 90, 30, now=NOW)
        with pytest.raises(CustomLimitError) as first:
            await svc.get_analysis(week, e, "u", {})
        with pytest.raises(CustomLimitError) as again:
            await svc.get_analysis(week, e, "u", {})
        assert again.value.message == first.value.message
        assert 0 < again.value.retry_after <= first.value.retry_after
        assert admissions == ["u", "u"]  # the second request answered from the refusal, without trying again
        assert len(runner.jobs) == 1
        assert await counted(svc, "u") == (1, 0)

    async def test_a_refused_custom_computation_leaves_the_hourly_budget_unchanged(self):
        svc = service()
        first, second = eff(minimumConfidence=55), eff(minimumConfidence=60)
        for custom in (first, second):
            custom.values["tuning"]["maxCustomComputationsPerHour"] = 1
        await svc.get_analysis(rng(), first, "u", {})
        assert await counted(svc, "u") == (1, 1)
        with pytest.raises(CustomLimitError, match="custom settings"):
            await svc.get_analysis(rng(), second, "u", {})
        assert await counted(svc, "u") == (1, 1)

    async def test_a_busy_refusal_counts_nothing(self):
        svc = service()
        custom = eff(minimumConfidence=55)
        custom.values["analysisTimeoutSeconds"] = 0.1
        await svc._slots["custom"].acquire()  # another computation with custom settings is running
        with pytest.raises(AnalysisUnavailableError) as exc:
            await svc.get_analysis(rng(), custom, "u", {})
        assert (exc.value.status_code, exc.value.retry_after) == (503, 30)
        assert await counted(svc, "u") == (0, 0)
        # The user's custom slot was released too: once the worker is free, the same computation runs and counts.
        svc._slots["custom"].release()
        assert (await svc.get_analysis(rng(), custom, "u", {})).cache == "miss"
        assert await counted(svc, "u") == (1, 1)

    async def test_last_good_is_kept_a_day_only_for_standard_periods_with_default_settings(self):
        """Each custom range or custom setting would otherwise leave its own copy in the shared Redis for a day."""
        svc = service(FakeRunner())

        async def last_good_ttl(r, e):
            handle = await svc.get_analysis(r, e, "u", {})
            return await svc.cache.ttl(f"{svc._last_prefix(r, e)}:meta"), handle.meta["ttl_s"]

        kept, _ = await last_good_ttl(rng(), eff())
        assert kept > 12 * 3600
        custom_range = resolve_range(None, NOW - timedelta(days=10), NOW - timedelta(days=1), 90, 30, now=NOW)
        kept, ttl = await last_good_ttl(custom_range, eff())
        assert kept <= ttl
        kept, ttl = await last_good_ttl(rng(), eff(groupMinSignals=3))
        assert kept <= ttl

    async def test_failure_serves_the_last_good_analysis(self):
        runner = FakeRunner()
        svc = service(runner)
        r1 = rng()
        await svc.get_analysis(r1, eff(), "u", {})
        # The next window of the same period fails.
        r2 = resolve_range("last_30_days", None, None, 90, 30, now=NOW + timedelta(minutes=15))
        runner.fail = RuntimeError("es down")
        handle = await svc.get_analysis(r2, eff(), "u", {})
        # The previous analysis answers at once while the new one is computed...
        assert (handle.cache, handle.extra.get("refreshing")) == ("stale", True)
        assert (await svc.load_sections(handle, ["summary"]))["summary"] == {"tiles": {"cli": 1}}
        await asyncio.gather(*svc._inflight.values(), return_exceptions=True)
        # ...which fails: the next request gets the previous analysis with the failure, without another read.
        again = await svc.get_analysis(r2, eff(), "u", {})
        assert again.cache == "stale" and not again.extra.get("refreshing")
        assert "failed" in again.extra["error"]
        assert len(runner.jobs) == 2

    async def test_failure_without_an_earlier_analysis(self):
        runner = FakeRunner(fail=RuntimeError("es down"))
        svc = service(runner)
        with pytest.raises(AnalysisUnavailableError) as first:
            await svc.get_analysis(rng(), eff(), "u", {})
        assert first.value.status_code == 503
        # For a while the same error comes back without another read.
        with pytest.raises(AnalysisUnavailableError) as again:
            await svc.get_analysis(rng(), eff(), "u", {})
        assert again.value.message == first.value.message
        assert 0 < again.value.retry_after <= 60
        assert len(runner.jobs) == 1
        # Recompute clears it, and the lock was released, so the next request computes again.
        svc._job_runner = FakeRunner()
        await svc.refresh(rng(), eff())
        assert (await svc.get_analysis(rng(), eff(), "u", {})).cache == "miss"

    async def test_waiting_for_a_slot_is_bounded_and_is_not_a_failure(self):
        runner = FakeRunner()
        svc = service(runner)
        e = eff()
        e.values["analysisTimeoutSeconds"] = 0.1
        await svc._slots["default"].acquire()  # another computation is running
        with pytest.raises(AnalysisUnavailableError) as exc:
            await svc.get_analysis(rng(), e, "u", {})
        assert (exc.value.status_code, exc.value.retry_after) == (503, 30)
        assert runner.jobs == []
        # Custom settings have their own slot.
        custom = eff(minimumConfidence=55)
        custom.values["analysisTimeoutSeconds"] = 0.1
        assert (await svc.get_analysis(rng(), custom, "u", {})).cache == "miss"
        # Being busy is not remembered as a failure: once the slot is free the default analysis runs.
        svc._slots["default"].release()
        assert (await svc.get_analysis(rng(), e, "u", {})).cache == "miss"

    async def test_a_timeout_stops_only_its_own_computation(self, spawnable):
        """Real processes: the 30-day computation times out and is killed; the 7-day one, which waited for the
        slot, is not failed by it, and its own timeout counts from its own start."""
        svc = AnalysisService(cache=NoRedis(), process_target=process_targets.slow_for_long_ranges)
        e = eff()
        e.values["analysisTimeoutSeconds"] = 5.0
        slow = asyncio.create_task(svc.get_analysis(rng(), e, "u", {}))
        await asyncio.sleep(1.5)
        week = resolve_range("last_7_days", None, None, 90, 30, now=NOW)
        fast = await svc.get_analysis(week, e, "u", {})
        assert fast.cache == "miss"
        with pytest.raises(AnalysisUnavailableError) as exc:
            await slow
        assert exc.value.status_code == 504
        assert svc._running == {}

    @pytest.mark.parametrize(
        ("target", "message"),
        [
            (process_targets.failing, "The analysis failed: LookupError"),
            (process_targets.out_of_memory, "more memory than allowed"),
            (process_targets.crashing, "The analysis failed: WorkerStopped"),
        ],
    )
    async def test_process_failures(self, spawnable, target, message):
        svc = AnalysisService(cache=NoRedis(), process_target=target)
        with pytest.raises(AnalysisUnavailableError) as exc:
            await svc.get_analysis(rng(), eff(), "u", {})
        assert message in exc.value.message
        assert exc.value.status_code == 503

    async def test_shutdown_stops_computations_without_recording_a_failure(self, spawnable):
        svc = AnalysisService(cache=NoRedis(), process_target=process_targets.slow_for_long_ranges)
        task = asyncio.create_task(svc.get_analysis(rng(), eff(), "u", {}))
        for _ in range(100):
            await asyncio.sleep(0.1)
            if svc._running:
                break
        svc.shutdown()
        with pytest.raises(AnalysisUnavailableError) as exc:
            await task
        assert "restarting" in exc.value.message
        prefix = svc.analysis_prefix(rng(), eff())
        assert await svc.cache.get(svc._failed_key(prefix)) is None  # other pods are not told it failed
        with pytest.raises(AnalysisUnavailableError):
            await svc.get_analysis(rng(), eff(), "u", {})

    async def test_every_computation_counts_against_the_hourly_budget(self):
        runner = FakeRunner()
        svc = service(runner)
        e = eff()
        e.values["tuning"]["maxComputationsPerHour"] = 2
        week = resolve_range("last_7_days", None, None, 90, 30, now=NOW)
        day = resolve_range("last_24_hours", None, None, 90, 30, now=NOW)
        await svc.get_analysis(rng(), e, "u", {})
        await svc.get_analysis(week, e, "u", {})
        with pytest.raises(CustomLimitError):
            await svc.get_analysis(day, e, "u", {})
        # Cache hits are free, and other users have their own budget.
        assert (await svc.get_analysis(rng(), e, "u", {})).cache == "hit"
        assert (await svc.get_analysis(day, e, "other", {})).cache == "miss"

    async def test_waiting_computations_are_bounded_in_number(self):
        runner = FakeRunner(delay=0.5)
        svc = service(runner)
        e = eff()
        e.values["tuning"]["maxComputationsPerHour"] = 100
        ranges = [resolve_range(None, NOW - timedelta(days=3 + i), NOW, 90, 30, now=NOW) for i in range(6)]
        results = await asyncio.gather(*[svc.get_analysis(r, e, "u", {}) for r in ranges], return_exceptions=True)
        busy = [r for r in results if isinstance(r, AnalysisUnavailableError)]
        assert busy and all(r.retry_after == 30 for r in busy)
        assert len(runner.jobs) == len(results) - len(busy)

    async def test_a_lock_left_by_a_dead_pod_frees_the_key(self, monkeypatch):
        monkeypatch.setattr(analysis_module, "_LOCK_TTL_S", 1)
        runner = FakeRunner()
        svc = service(runner)
        e = eff()
        prefix = svc.analysis_prefix(rng(), e)
        await svc.cache.acquire(svc._lock_key(prefix), 1)  # a pod that died while computing
        handle = await svc.get_analysis(rng(), e, "u", {})
        assert handle.cache == "miss"
        assert len(runner.jobs) == 1

    async def test_custom_ranges_are_rounded_to_15_minutes(self):
        a = resolve_range(None, NOW - timedelta(days=5, seconds=7), NOW - timedelta(seconds=3), 90, 30, now=NOW)
        b = resolve_range(None, NOW - timedelta(days=5, seconds=50), NOW - timedelta(seconds=40), 90, 30, now=NOW)
        assert (a.start, a.end) == (b.start, b.end)
        assert a.start.minute % 15 == 0 and a.end.minute % 15 == 0

    async def test_refresh_drops_the_cache_at_most_once_a_minute(self):
        runner = FakeRunner()
        svc = service(runner)
        await svc.get_analysis(rng(), eff(), "u", {})
        await svc.refresh(rng(), eff())
        with pytest.raises(RefreshTooSoonError):
            await svc.refresh(rng(), eff())
        handle = await svc.get_analysis(rng(), eff(), "u", {})
        assert (handle.cache, handle.extra.get("refreshing")) == ("stale", True)  # the previous one, while it reruns
        await asyncio.gather(*svc._inflight.values(), return_exceptions=True)
        assert (await svc.get_analysis(rng(), eff(), "u", {})).cache == "hit"
        assert runner.jobs[1]["inputs_blob"] is None  # the inputs were dropped too
        assert runner.jobs[1]["evidence_blob"] is None

    async def test_evidence_is_read_once_for_every_period_ending_at_the_same_time(self):
        runner = FakeRunner()
        svc = service(runner)
        await svc.get_analysis(rng(), eff(), "u", {})
        week = resolve_range("last_7_days", None, None, 90, 30, now=NOW)
        await svc.get_analysis(week, eff(), "u", {})
        assert runner.jobs[0]["evidence_blob"] is None
        assert runner.jobs[1]["evidence_blob"] == b"evidence"  # the 30-day read, reused
        later = resolve_range("last_7_days", None, None, 90, 30, now=NOW + timedelta(minutes=15))
        await svc.get_analysis(later, eff(), "u", {})
        await asyncio.gather(*svc._inflight.values(), return_exceptions=True)
        assert runner.jobs[2]["evidence_blob"] is None  # the next hour: read again

    async def test_evidence_is_reused_for_the_rest_of_the_hour(self):
        runner = FakeRunner()
        svc = service(runner)
        for minute in (7, 52):  # the 14:00 and 14:45 computations
            r = resolve_range("last_30_days", None, None, 90, 30, now=NOW.replace(minute=minute))
            await svc.get_analysis(r, eff(), "u", {})
            await asyncio.gather(*svc._inflight.values(), return_exceptions=True)
        assert runner.jobs[0]["evidence_blob"] is None
        assert runner.jobs[1]["evidence_blob"] == b"evidence"
        nxt = resolve_range("last_30_days", None, None, 90, 30, now=NOW + timedelta(minutes=15))
        await svc.get_analysis(nxt, eff(), "u", {})
        await asyncio.gather(*svc._inflight.values(), return_exceptions=True)
        assert runner.jobs[2]["evidence_blob"] is None
        assert "previous_window" not in runner.jobs[0]  # no previous period is read

    async def test_short_periods_do_not_reuse_evidence_from_earlier_in_the_hour(self):
        runner = FakeRunner()
        svc = service(runner)
        for minute in (7, 52):
            r = resolve_range("last_6_hours", None, None, 90, 30, now=NOW.replace(minute=minute))
            await svc.get_analysis(r, eff(), "u", {})
            await asyncio.gather(*svc._inflight.values(), return_exceptions=True)
        assert runner.jobs[1]["evidence_blob"] is None  # the 14:00 evidence would end 45 minutes before 14:45

    async def test_short_periods_reuse_only_the_evidence_of_their_quarter_hour(self):
        runner = EvidenceByEnd()
        svc = service(runner)
        early = resolve_range("last_30_days", None, None, 90, 30, now=NOW.replace(minute=7))
        month = rng()
        hour = resolve_range("last_hour", None, None, 90, 30, now=NOW)
        six = resolve_range("last_6_hours", None, None, 90, 30, now=NOW)
        for r in (early, month, hour, six):  # 14:00, then three periods at 14:45
            await svc.get_analysis(r, eff(), "u", {})
            await asyncio.gather(*svc._inflight.values(), return_exceptions=True)
        assert runner.jobs[1]["evidence_blob"] == b"evidence to 2026-10-13T14:00:00Z"  # a month reuses the hour's
        assert runner.jobs[2]["evidence_blob"] is None  # the last hour does not: it reads up to its own end
        assert runner.jobs[3]["evidence_blob"] == b"evidence to 2026-10-13T14:45:00Z"  # shared in its quarter-hour
        assert 0 < await svc.cache.ttl(svc.evidence_quarter_key(six, eff())) <= 15 * 60  # as long as the analysis
        # Recompute drops both.
        await svc.refresh(six, eff())
        keys = [svc.evidence_key(six, eff()), svc.evidence_quarter_key(six, eff())]
        assert await svc.cache.mget(keys) == [None, None]

    async def test_meta_shows_the_windows_actually_read(self):
        read_for = {"from": "2026-09-13T00:00:00Z", "to": "2026-10-13T14:00:00Z", "days": 30.5833}

        class Reusing(FakeRunner):
            def __call__(self, job):
                result = super().__call__(job)
                result["meta"]["evidence_window"] = read_for
                return result

        handle = await service(Reusing()).get_analysis(rng(), eff(), "u", {})
        assert handle.meta["evidence_window"] == read_for
        assert "previous_window" not in handle.meta

    async def test_cache_lifetimes(self):
        class Recording(NoRedis):
            def __init__(self):
                super().__init__()
                self.ttls: dict[str, int] = {}

            async def set_many(self, mapping, ttl):
                self.ttls.update(dict.fromkeys(mapping, ttl))
                await super().set_many(mapping, ttl)

        cache = Recording()
        r = resolve_range("last_30_days", None, None, 90, 30, now=datetime.now(timezone.utc))
        await AnalysisService(cache=cache, job_runner=FakeRunner()).get_analysis(r, eff(), "u", {})
        by_kind = {key.split(":")[3]: ttl for key, ttl in cache.ttls.items()}
        assert by_kind["last"] == 24 * 3600  # a first visit the next morning still gets it at once
        assert by_kind["an"] == 15 * 60
        # The evidence lasts until the end of the hour, and at least as long as the analysis.
        assert 15 * 60 <= by_kind["ev"] <= 3600 + 120
        assert "prev" not in by_kind

    async def test_first_computation_of_a_period_is_awaited(self):
        runner = FakeRunner(delay=0.2)
        svc = service(runner)
        handle = await svc.get_analysis(rng(), eff(), "u", {})
        assert handle.cache == "miss"  # nothing earlier to show

    async def test_another_pod_computing(self):
        svc = service()
        e = eff()
        prefix = svc.analysis_prefix(rng(), e)
        await svc.cache.acquire(svc._lock_key(prefix), 120)  # held by another pod

        async def finish_elsewhere():
            await asyncio.sleep(0.6)
            await svc.cache.set_many({f"{prefix}:meta": _gz({"computed_at": "x"})}, 60)

        task = asyncio.create_task(finish_elsewhere())
        handle = await svc.get_analysis(rng(), e, "u", {})
        await task
        assert handle.cache == "shared"


class BrokenRedis:
    """A Redis client whose every call fails."""

    def __getattr__(self, name):
        async def fail(*args, **kwargs):
            raise ConnectionError("redis down")

        return fail

    def pipeline(self, transaction=True):
        raise ConnectionError("redis down")


@pytest.mark.asyncio
class TestSharedCache:
    async def test_falls_back_to_the_in_process_store(self):
        cache = SharedCache(redis_factory=BrokenRedis)
        await cache.set_many({"k": b"v"}, 60)
        assert await cache.get("k") == b"v"
        assert cache.backend == "in-process"

    async def test_locks_and_counters(self):
        cache = NoRedis()
        token = await cache.acquire("lock", 60)
        assert token and await cache.acquire("lock", 60) is None
        await cache.release("lock", "wrong-token")
        assert await cache.acquire("lock", 60) is None
        await cache.release("lock", token)
        assert await cache.acquire("lock", 60)
        assert (await cache.incr("count", 60))[0] == 1
        assert (await cache.incr("count", 60))[0] == 2
        await cache.decr("count")
        assert (await cache.incr("count", 60))[0] == 2

    async def test_local_store_is_bounded(self):
        cache = NoRedis()
        cache.local.max_bytes = 10
        await cache.set_many({"a": b"12345", "b": b"12345", "c": b"12345"}, 60)
        assert await cache.get("a") is None
        assert await cache.get("c") == b"12345"
