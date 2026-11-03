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

"""The analysis service: cache, single flight, worker processes and timeouts (architecture.md §5).

- **Cache keys.** The range end is rounded down to 15 minutes, so viewers in the same window share one analysis.
  Inputs (Elasticsearch and app DB reads) are cached under the retrieval settings hash, the analysis under the
  retrieval and analysis hashes plus the classifier version. Display settings are in neither key.
- **Slow reads.** The evidence (sizes, repository values, tools, sessions and people over at least 30 days) is
  reused for the rest of the hour by periods of a day or more: the first computation of the hour reads it up to its
  own end, and the later ones, of any period, read only the spend of their period. Their evidence may therefore end
  up to 45 minutes before the range end. Rolling periods shorter than a day reuse only evidence read up to their own
  end, in the same quarter-hour.
- **Single flight.** One computation per key per process (an in-process task), and across pods a lock in the
  shared cache, renewed while the computation runs: one pod computes while the others wait for the result, and the
  lock of a pod that died frees the key within ``_LOCK_TTL_S``. Requests wait at most ``WAIT_FOR_OTHER_POD_S``,
  then get the last good analysis or a 503 to retry.
- **Worker processes.** Each computation runs in its own ``spawn`` process, so the API's event loop never runs it,
  the memory is released afterwards, and stopping one computation never touches another. One computation with the
  default settings and one with custom settings run at a time per API process, so requests with the default
  settings never wait for custom ones. The timeout counts from the start of the computation, not from the wait for
  a free slot; the wait is bounded in time and in the number of waiters.
- **Limits.** Every computation counts against its user's hourly budget; custom settings also have their own. A
  computation refused before its worker starts counts nothing. A refusal by these limits is remembered for the user
  and key until it ends: meanwhile their requests get the last good analysis with the limit and the time to wait, or
  the same 429, and start nothing.
- **Failure.** When a computation fails or times out, the last good analysis for the same period and settings is
  served, marked stale, when there is one. For ``FAILURE_BACKOFF_S`` after a failure, requests for the same key get
  that answer without starting another read (Recompute clears it).
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import logging
import math
import multiprocessing
import time
from collections import Counter, OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any

from codemie.service.usage_attribution.cache import SharedCache
from codemie.service.usage_attribution.constants import (
    CACHE_NAMESPACE,
    FAILURE_BACKOFF_S,
    LAST_GOOD_TTL_S,
    LIST_SECTIONS,
    MAX_WAITING_PER_KIND,
    PG_DEGRADED_TTL_S,
    REFRESH_MIN_INTERVAL_S,
    SLOW_READ_REUSE_S,
    WAIT_FOR_OTHER_POD_S,
    WORKER_MEMORY_EXIT_CODE,
)
from codemie.service.usage_attribution.limits import (
    Admission,
    CustomLimitError,
    admit_computation,
    end_computation,
    refusal_note,
)
from codemie.service.usage_attribution.postgres_sources import pg_framework_rows
from codemie.service.usage_attribution.settings import EffectiveSettings

logger = logging.getLogger(__name__)

_DAY_BASED = {"last_7_days", "last_30_days", "last_60_days", "last_year"}
_PERIODS = {
    "last_hour": timedelta(hours=1),
    "last_6_hours": timedelta(hours=6),
    "last_24_hours": timedelta(hours=24),
    "last_7_days": timedelta(days=7),
    "last_30_days": timedelta(days=30),
    "last_60_days": timedelta(days=60),
    "last_year": timedelta(days=365),
}
TIME_PERIODS = tuple(_PERIODS)
# The earliest custom date: nothing is older, and dates near year 1 would overflow the range arithmetic.
_EARLIEST = datetime(2000, 1, 1, tzinfo=timezone.utc)
_SLOT = timedelta(minutes=15)
# Two analyses of list sections (a second period or a Recompute on the same pod) stay decoded.
_DECODED_MAX = 12


class AnalysisUnavailableError(Exception):
    """The analysis could not be computed and there is no earlier one to serve."""

    def __init__(self, message: str, status_code: int = 503, retry_after: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after


class AnalysisBusyError(AnalysisUnavailableError):
    """No worker slot became free in time: other computations are running on this API process."""

    def __init__(self, retry_after: int = 30):
        super().__init__("Other analyses are running on this server. Try again in a minute.", 503, retry_after)


class RangeError(ValueError):
    """An invalid period; its message is safe to show."""


class RefreshTooSoonError(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Recompute runs at most once a minute for a period and settings")
        self.retry_after = max(1, retry_after)


# ---------------------------------------------------------------------------------------------------------------------
# Ranges
# ---------------------------------------------------------------------------------------------------------------------
def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _floor(dt: datetime) -> datetime:
    dt = dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return dt - timedelta(minutes=dt.minute % 15)


def _floor_to(dt: datetime, seconds: int) -> datetime:
    return datetime.fromtimestamp(int(dt.timestamp()) // seconds * seconds, tz=timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class RangeInfo:
    start: datetime
    end: datetime
    requested_start: datetime
    requested_end: datetime
    clamped: bool
    evidence_start: datetime
    spec: str

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86400.0

    @property
    def scale(self) -> float:
        return self.days / 30.0

    @property
    def slow_slot(self) -> datetime:
        """The hour the range ends in: the evidence is reused within it."""
        return _floor_to(self.end, SLOW_READ_REUSE_S)

    @property
    def short_rolling(self) -> bool:
        """A rolling period shorter than a day: evidence from earlier in the hour doesn't fit it (see
        ``AnalysisService.evidence_quarter_key``)."""
        return self.days < 1 and not self.spec.startswith("custom:")

    def window(self) -> dict[str, Any]:
        return {"from": _iso(self.start), "to": _iso(self.end), "days": round(self.days, 4)}

    def evidence_window(self) -> dict[str, Any]:
        days = (self.end - self.evidence_start).total_seconds() / 86400.0
        return {"from": _iso(self.evidence_start), "to": _iso(self.end), "days": round(days, 4)}

    def info(self) -> dict[str, Any]:
        return {
            "from": _iso(self.start),
            "to": _iso(self.end),
            "days": round(self.days, 2),
            "clamped": self.clamped,
            "requested_from": _iso(self.requested_start),
            "requested_to": _iso(self.requested_end),
            "evidence_from": _iso(self.evidence_start),
        }


def _invalid_time_period_error() -> RangeError:
    return RangeError(f"Invalid time_period. Valid options are: {', '.join(_PERIODS)}")


def _range_from_period(time_period: str, slot_end: datetime) -> tuple[datetime, datetime, str]:
    delta = _PERIODS.get(time_period)
    if delta is None:
        raise _invalid_time_period_error()
    end = slot_end
    start = (
        (end - delta).replace(hour=0, minute=0, second=0, microsecond=0) if time_period in _DAY_BASED else end - delta
    )
    return start, end, time_period


def _range_from_dates(
    start_date: datetime | None, end_date: datetime | None, now: datetime
) -> tuple[datetime, datetime, str]:
    end = _aware(end_date) if end_date else now
    if end < _EARLIEST or (start_date and _aware(start_date) < _EARLIEST):
        raise RangeError(f"Dates before {_EARLIEST:%Y-%m-%d} are not supported")
    start = _aware(start_date) if start_date else end - timedelta(days=30)
    if end > now + timedelta(minutes=1):
        raise RangeError("end_date cannot be in the future")
    if start >= end:
        raise RangeError("start_date must be before end_date")
    start, end = _floor(start), _floor(end)
    if start >= end:
        raise RangeError("A custom range must span at least 15 minutes once rounded down to 15-minute steps")
    return start, end, f"custom:{_iso(start)}:{_iso(end)}"


def _default_range(slot_end: datetime) -> tuple[datetime, datetime, str]:
    end = slot_end
    start = (end - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end, "default"


def resolve_range(
    time_period: str | None,
    start_date: datetime | None,
    end_date: datetime | None,
    max_days: int,
    min_evidence_days: int,
    now: datetime | None = None,
) -> RangeInfo:
    """The selected range, clamped to ``max_days``. Raises RangeError.

    Ends (and the starts of custom ranges) are rounded down to 15 minutes, so every viewer of a period in the same
    15 minutes shares one analysis, and a custom range cannot be shifted by seconds to force new computations.
    """
    now = _aware(now or datetime.now(timezone.utc))
    slot_end = _floor(now)
    if time_period:
        start, end, spec = _range_from_period(time_period, slot_end)
    elif time_period == "":
        raise _invalid_time_period_error()
    elif start_date or end_date:
        start, end, spec = _range_from_dates(start_date, end_date, now)
    else:
        start, end, spec = _default_range(slot_end)
    requested_start, requested_end = start, end
    clamped = False
    if end - start > timedelta(days=max_days):
        start = end - timedelta(days=max_days)
        clamped = True
    # From midnight, like the day-based periods: every period ending in the same hour shares one evidence window,
    # so its read (repository values, tools, sessions) is cached once and reused (see AnalysisService.evidence_key).
    evidence_start = min(start, (end - timedelta(days=min_evidence_days)).replace(hour=0, minute=0, second=0))
    return RangeInfo(start, end, requested_start, requested_end, clamped, evidence_start, spec)


# ---------------------------------------------------------------------------------------------------------------------
# Handles
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class AnalysisHandle:
    prefix: str
    meta: dict[str, Any]
    cache: str  # hit | miss | stale | shared
    extra: dict[str, Any] = field(default_factory=dict)


def _gunzip_json(blob: bytes | None) -> Any:
    if blob is None:
        return None
    return json.loads(gzip.decompress(blob))


def _gzip_json(obj: Any) -> bytes:
    return gzip.compress(json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8"), compresslevel=5)


def _slow_reads_ttl(rng: RangeInfo, ttl: int, now: datetime) -> int:
    """The evidence is kept until the end of the hour it is reused in, with a margin for the clocks of other pods,
    and at least as long as the analysis."""
    left = (rng.slow_slot + timedelta(seconds=SLOW_READ_REUSE_S) - now).total_seconds()
    return max(ttl, int(left) + _SLOW_READS_MARGIN_S)


def _inputs_note(inputs_blob: bytes | None, meta: dict[str, Any]) -> str:
    """How the inputs were obtained, for the log: cached, or read, with the evidence of earlier in the hour."""
    if inputs_blob:
        return "cached"
    return "read, evidence reused" if meta.get("evidence_reused") else "read"


# ---------------------------------------------------------------------------------------------------------------------
# Computation processes
# ---------------------------------------------------------------------------------------------------------------------
class WorkerFailedError(RuntimeError):
    """The computation process raised an error (its type is kept), or stopped without an answer."""

    def __init__(self, error_type: str, message: str):
        super().__init__(f"{error_type}: {message}")
        self.error_type = error_type


def serve_job(conn: Connection, run: Callable[[dict], dict], job: dict) -> None:
    """In the computation process: run the job and send back its result, or the type and text of its error."""
    try:
        message: tuple = ("ok", run(job))
    except BaseException as exc:  # noqa: BLE001 - every failure is reported to the API process  # NOSONAR
        message = ("error", type(exc).__name__, str(exc)[:500])
    try:
        conn.send(message)
    finally:
        conn.close()


def _computation_process(conn: Connection, job: dict) -> None:
    """Entry point of a computation process."""
    from codemie.service.usage_attribution.worker import run_job

    serve_job(conn, run_job, job)


def _receive(conn: Connection) -> tuple | None:
    try:
        return conn.recv()
    except (EOFError, OSError):
        return None  # the process ended without an answer


def _job_result(message: tuple | None, exitcode: int | None) -> dict:
    if message and message[0] == "ok":
        return message[1]
    if message and message[0] == "error":
        raise WorkerFailedError(message[1], message[2])
    if exitcode == WORKER_MEMORY_EXIT_CODE:
        raise WorkerFailedError("MemoryLimit", "the computation went above its memory limit")
    raise WorkerFailedError("WorkerStopped", f"the computation process stopped (exit code {exitcode})")


_STILL_COMPUTING = "The analysis is still being computed. Try again in a few seconds."
_RESTARTING = "The server is restarting. Try again in a few seconds."
_FAILED_NOTE = "the latest computation failed. Recompute to try again."
_COMPUTING_NOTE = "a newer analysis is being computed and replaces it when ready."
# The lock of a computation lives this long and is renewed while the computation runs, so the lock of a pod that
# died frees the key within this time.
_LOCK_TTL_S = 30
_LOCK_RENEW_S = 10
_SLOW_READS_MARGIN_S = 120


class AnalysisService:
    """One per API process."""

    def __init__(
        self,
        cache: SharedCache | None = None,
        job_runner: Callable[[dict], dict] | None = None,
        process_target: Callable[[Connection, dict], None] | None = None,
    ):
        self.cache = cache or SharedCache()
        # Tests run jobs in a thread (job_runner), or in a process with another entry point (process_target).
        self._job_runner = job_runner
        self._process_target = process_target or _computation_process
        # One computation of each kind at a time per API process, each in a process of its own.
        self._slots: dict[str, asyncio.Semaphore] = {"default": asyncio.Semaphore(1), "custom": asyncio.Semaphore(1)}
        self._waiting: Counter[str] = Counter()
        self._running: dict[str, BaseProcess] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self._decoded: OrderedDict[str, Any] = OrderedDict()
        self._closing = False

    # ------------------------------------------------------------------ keys
    @staticmethod
    def _window_part(rng: RangeInfo) -> str:
        return f"{int(rng.start.timestamp())}:{int(rng.end.timestamp())}"

    def inputs_key(self, rng: RangeInfo, eff: EffectiveSettings) -> str:
        return f"{CACHE_NAMESPACE}:in:{self._window_part(rng)}:{eff.fetch_hash}"

    def evidence_key(self, rng: RangeInfo, eff: EffectiveSettings) -> str:
        """The evidence part of the inputs, reused for the rest of the hour by every period of a day or more whose
        evidence starts in the same hour. The first computation of the hour reads it up to its own end."""
        start = int(_floor_to(rng.evidence_start, SLOW_READ_REUSE_S).timestamp())
        return f"{CACHE_NAMESPACE}:ev:{start}:{int(rng.slow_slot.timestamp())}:{eff.fetch_hash}"

    def evidence_quarter_key(self, rng: RangeInfo, eff: EffectiveSettings) -> str:
        """The evidence read up to this range end, kept as long as the analysis. Rolling periods shorter than a day
        reuse only this one: the hour's evidence may end 45 minutes before them, most of a short period. They still
        share the evidence read by any period in the same quarter-hour."""
        return f"{CACHE_NAMESPACE}:ev:{int(rng.evidence_start.timestamp())}:{int(rng.end.timestamp())}:{eff.fetch_hash}"

    def analysis_prefix(self, rng: RangeInfo, eff: EffectiveSettings) -> str:
        return f"{CACHE_NAMESPACE}:an:{self._window_part(rng)}:{eff.fetch_hash}:{eff.analysis_hash}"

    def _last_prefix(self, rng: RangeInfo, eff: EffectiveSettings) -> str:
        return f"{CACHE_NAMESPACE}:last:{rng.spec}:{eff.fetch_hash}:{eff.analysis_hash}"

    def _lock_key(self, prefix: str) -> str:
        return prefix.replace(":an:", ":lock:", 1)

    def _failed_key(self, prefix: str) -> str:
        return prefix.replace(":an:", ":failed:", 1)

    def _refused_key(self, prefix: str, user_id: str) -> str:
        return f"{prefix.replace(':an:', ':refused:', 1)}:{user_id}"

    # ------------------------------------------------------------------ public
    async def get_analysis(
        self, rng: RangeInfo, eff: EffectiveSettings, user_id: str, job_extra: dict[str, Any]
    ) -> AnalysisHandle:
        """The cached analysis for the range and settings, computing it when it is missing."""
        if self._closing:
            raise AnalysisUnavailableError(_RESTARTING, 503, 10)
        prefix = self.analysis_prefix(rng, eff)
        refused_key = self._refused_key(prefix, user_id)
        meta_blob, failed_blob, refused_blob = await self.cache.mget(
            [f"{prefix}:meta", self._failed_key(prefix), refused_key]
        )
        meta = _gunzip_json(meta_blob)
        if meta is not None:
            return AnalysisHandle(prefix, meta, "hit")
        # No await from here until the task is registered: concurrent requests must find it.
        task = self._inflight.get(prefix)
        if task is None:
            failure = _gunzip_json(failed_blob)
            if failure is not None:
                return await self._after_failure(rng, eff, prefix, failure)
            refusal = _gunzip_json(refused_blob)
            if refusal is not None:
                return await self._after_refusal(rng, eff, refused_key, refusal)
            # The computation runs as its own task: a request that goes away does not cancel it, and every request
            # for the same key waits for the same result.
            task = asyncio.create_task(self._compute_or_wait(rng, eff, prefix, user_id, job_extra))
            self._inflight[prefix] = task
            task.add_done_callback(lambda t, key=prefix: self._finished(key, t))
        # The previous analysis of this period and settings answers at once while the new one is computed.
        stale = await self._last_good(rng, eff)
        if stale is not None:
            stale.extra.update(error=_COMPUTING_NOTE, refreshing=True)
            return stale
        try:
            # A request waits as long as on another pod; the computation itself goes on.
            return await asyncio.wait_for(asyncio.shield(task), WAIT_FOR_OTHER_POD_S)
        except asyncio.TimeoutError:
            return await self._still_computing(rng, eff)

    def _finished(self, prefix: str, task: asyncio.Task) -> None:
        self._inflight.pop(prefix, None)
        if not task.cancelled():
            task.exception()  # retrieved here, so a failure nobody awaits is not reported as unhandled

    def _decoded_sections(self, version: str, names: list[str]) -> tuple[dict[str, Any], list[str]]:
        """Already-decoded sections for ``names``, and the names still missing."""
        out: dict[str, Any] = {}
        missing: list[str] = []
        for name in names:
            key = f"{version}:{name}"
            if key not in self._decoded:
                missing.append(name)
                continue
            self._decoded.move_to_end(key)
            out[name] = self._decoded[key]
        return out, missing

    def _remember_decoded(self, version: str, name: str, value: Any) -> None:
        if name not in LIST_SECTIONS:
            return
        self._decoded[f"{version}:{name}"] = value
        while len(self._decoded) > _DECODED_MAX:
            self._decoded.popitem(last=False)

    async def _fetch_sections(self, prefix: str, version: str, missing: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        blobs = await self.cache.mget([f"{prefix}:{name}" for name in missing])
        for name, blob in zip(missing, blobs, strict=True):
            value = _gunzip_json(blob)
            if value is None:
                raise AnalysisUnavailableError("The analysis expired while it was read. Reload the tab.", 503, 5)
            out[name] = value
            self._remember_decoded(version, name, value)
        return out

    async def load_sections(self, handle: AnalysisHandle, names: list[str]) -> dict[str, Any]:
        """Decoded sections of an analysis (list sections are kept decoded for a short while)."""
        # The computation time is part of the key: after Recompute, another pod must not serve the old lists.
        version = f"{handle.prefix}:{handle.meta.get('computed_at')}"
        out, missing = self._decoded_sections(version, names)
        if missing:
            out.update(await self._fetch_sections(handle.prefix, version, missing))
        return out

    async def refresh(self, rng: RangeInfo, eff: EffectiveSettings) -> None:
        """Drop the cached analysis and inputs of a range and settings, at most once a minute."""
        prefix = self.analysis_prefix(rng, eff)
        gate = prefix.replace(":an:", ":refresh:", 1)
        token = await self.cache.acquire(gate, REFRESH_MIN_INTERVAL_S)
        if token is None:
            raise RefreshTooSoonError(int(await self.cache.ttl(gate)) or REFRESH_MIN_INTERVAL_S)
        keys = [
            f"{prefix}:meta",
            self.inputs_key(rng, eff),
            self.evidence_key(rng, eff),
            self.evidence_quarter_key(rng, eff),
            self._failed_key(prefix),
        ]
        await self.cache.delete(*keys)
        for key in [k for k in self._decoded if k.startswith(prefix)]:
            self._decoded.pop(key, None)

    def shutdown(self) -> None:
        """Application shutdown: stop the running computations and start no new ones."""
        self._closing = True
        for process in list(self._running.values()):
            with contextlib.suppress(Exception):  # the process may already be gone
                process.kill()
        self._running.clear()

    # ------------------------------------------------------------------ compute
    async def _compute_or_wait(
        self, rng: RangeInfo, eff: EffectiveSettings, prefix: str, user_id: str, job_extra: dict[str, Any]
    ) -> AnalysisHandle:
        timeout_s = eff.values["analysisTimeoutSeconds"]
        lock_key = self._lock_key(prefix)
        token = await self.cache.acquire(lock_key, _LOCK_TTL_S)
        if token is None:
            handle = await self._wait_for_other(rng, eff, prefix)
            if handle is not None:
                return handle
            # The other computation ended without a result or an error (its process died): compute here.
            token = await self.cache.acquire(lock_key, _LOCK_TTL_S)
            if token is None:
                return await self._still_computing(rng, eff)
        renew = asyncio.create_task(self._keep_lock(lock_key, token))
        admission: Admission | None = None
        try:
            # Someone may have finished between our miss and taking the lock.
            meta = _gunzip_json(await self.cache.get(f"{prefix}:meta"))
            if meta is not None:
                return AnalysisHandle(prefix, meta, "hit")
            admission = await self._admit(eff, prefix, user_id, timeout_s)
            return await self._compute(rng, eff, prefix, job_extra, timeout_s, admission)
        finally:
            renew.cancel()
            # Refused before its worker started (no free worker, shutdown): the counts are given back.
            await end_computation(self.cache, admission)
            await self.cache.release(lock_key, token)

    async def _admit(self, eff: EffectiveSettings, prefix: str, user_id: str, timeout_s: float) -> Admission:
        """Count the computation against the user's limits. A refusal is remembered for the user and key until it
        ends, so their next requests answer at once instead of trying again (see get_analysis)."""
        tuning = eff.values["tuning"]
        # The custom slot covers the wait for a worker plus the computation.
        custom = (int(tuning["maxCustomComputationsPerHour"]), math.ceil(2 * timeout_s) + 30) if eff.is_custom else None
        try:
            admission = await admit_computation(self.cache, user_id, int(tuning["maxComputationsPerHour"]), custom)
        except CustomLimitError as exc:
            refusal = {"message": exc.message, "note": exc.note, "option": exc.option, "retry_after": exc.retry_after}
            await self.cache.set_many({self._refused_key(prefix, user_id): _gzip_json(refusal)}, exc.retry_after)
            raise
        if eff.is_custom:
            logger.info(
                "usage attribution custom computation: user_id=%s overridden=%s fetch_hash=%s analysis_hash=%s",
                user_id,
                sorted(eff.overrides),
                eff.fetch_hash,
                eff.analysis_hash,
            )
        return admission

    async def _keep_lock(self, key: str, token: str) -> None:
        while True:
            await asyncio.sleep(_LOCK_RENEW_S)
            if not await self.cache.extend(key, token, _LOCK_TTL_S):
                logger.warning("usage attribution: the computation lock %s was lost", key)
                return

    async def _wait_for_other(self, rng: RangeInfo, eff: EffectiveSettings, prefix: str) -> AnalysisHandle | None:
        """Another pod computes this analysis: its result or its failure; None when it stopped without either."""
        deadline = time.monotonic() + WAIT_FOR_OTHER_POD_S
        released = False
        while time.monotonic() < deadline and not released:
            await asyncio.sleep(0.5)
            # Read after the lock check too: the holder writes its result before it releases the lock.
            released = await self.cache.ttl(self._lock_key(prefix)) <= 0
            meta_blob, failed_blob = await self.cache.mget([f"{prefix}:meta", self._failed_key(prefix)])
            meta = _gunzip_json(meta_blob)
            if meta is not None:
                return AnalysisHandle(prefix, meta, "shared")
            failure = _gunzip_json(failed_blob)
            if failure is not None:
                return await self._after_failure(rng, eff, prefix, failure)
        if released:
            return None
        return await self._still_computing(rng, eff)

    async def _still_computing(self, rng: RangeInfo, eff: EffectiveSettings) -> AnalysisHandle:
        stale = await self._last_good(rng, eff)
        if stale is not None:
            stale.extra.update(error=_COMPUTING_NOTE, refreshing=True)
            return stale
        raise AnalysisUnavailableError(_STILL_COMPUTING, 503, 10)

    async def _load_cached_inputs(
        self, rng: RangeInfo, inputs_key: str, quarter_key: str, hour_key: str
    ) -> tuple[bytes | None, bytes | None]:
        inputs_blob = await self.cache.get(inputs_key)
        if inputs_blob:
            return inputs_blob, None
        # The evidence of an earlier computation in this hour, when there was one. Short rolling periods take
        # only evidence read up to their own end.
        evidence_blob = await self.cache.get(quarter_key if rng.short_rolling else hour_key)
        return inputs_blob, evidence_blob

    async def _record_failure(self, prefix: str, error: AnalysisUnavailableError) -> None:
        failure = {"message": error.message, "status_code": error.status_code}
        await self.cache.set_many({self._failed_key(prefix): _gzip_json(failure)}, FAILURE_BACKOFF_S)

    async def _on_compute_failure(
        self,
        exc: Exception,
        rng: RangeInfo,
        eff: EffectiveSettings,
        prefix: str,
        inputs_blob: bytes | None,
        timeout_s: float,
    ) -> AnalysisHandle:
        if self._closing:
            raise AnalysisUnavailableError(_RESTARTING, 503, 10) from exc
        error = self._unavailable(exc, timeout_s)
        if not isinstance(exc, AnalysisBusyError):
            logger.error(
                "usage attribution analysis failed for %s (%s): %s",
                rng.spec,
                "cached inputs" if inputs_blob else "new read",
                f"{type(exc).__name__}: {str(exc)[:500]}",
            )
            await self._record_failure(prefix, error)
        stale = await self._last_good(rng, eff)
        if stale is not None:
            stale.extra["error"] = _FAILED_NOTE
            return stale
        raise error from exc

    @staticmethod
    def _build_meta(result: dict, rng: RangeInfo, ttl: int, duration_ms: float, now: datetime) -> dict[str, Any]:
        meta = dict(result.get("meta") or {})
        meta.update({"computed_at": _iso(now), "duration_ms": duration_ms, "window": rng.window(), "ttl_s": ttl})
        # The window actually read: evidence reused from earlier in the hour ends earlier than the range.
        meta.setdefault("evidence_window", rng.evidence_window())
        return meta

    async def _store_evidence(
        self, rng: RangeInfo, result: dict, quarter_key: str, hour_key: str, ttl: int, now: datetime
    ) -> None:
        if not result.get("evidence_blob"):
            return
        slow_ttl = _slow_reads_ttl(rng, ttl, now)
        # The quarter-hour key first: a range ending on the hour may have the same key for both, which then keeps
        # the hour's longer lifetime.
        await self.cache.set_many({quarter_key: result["evidence_blob"]}, ttl)
        await self.cache.set_many({hour_key: result["evidence_blob"]}, slow_ttl)

    async def _save_last_good(
        self, rng: RangeInfo, eff: EffectiveSettings, sections: dict[str, bytes], meta_blob: bytes, ttl: int
    ) -> None:
        last = self._last_prefix(rng, eff)
        # The last good copy, kept a day, answers standard periods with default settings while a newer analysis is
        # computed or fails. A custom range or custom settings keep it only as long as the analysis: a copy per date
        # picked or setting tried, kept a day, would grow the Redis the rest of CodeMie shares.
        last_ttl = LAST_GOOD_TTL_S if not rng.spec.startswith("custom:") and not eff.is_custom else ttl
        await self.cache.set_many({f"{last}:{name}": blob for name, blob in sections.items()}, last_ttl)
        await self.cache.set_many({f"{last}:meta": meta_blob}, last_ttl)

    @staticmethod
    def _log_computed(rng: RangeInfo, duration_ms: float, inputs_blob: bytes | None, meta: dict[str, Any]) -> None:
        logger.info(
            "usage attribution analysis for %s computed in %.1f s (inputs %s, es %s ms, db %s ms, analysis %s ms, "
            "peak %s MB, %s requests)",
            rng.spec,
            duration_ms / 1000.0,
            _inputs_note(inputs_blob, meta),
            meta.get("es_ms"),
            meta.get("db_ms"),
            meta.get("analysis_ms"),
            meta.get("peak_rss_mb"),
            meta.get("requests"),
        )

    async def _store_computed_result(
        self,
        rng: RangeInfo,
        eff: EffectiveSettings,
        prefix: str,
        result: dict,
        inputs_key: str,
        quarter_key: str,
        hour_key: str,
        inputs_blob: bytes | None,
        started: float,
    ) -> AnalysisHandle:
        duration_ms = round((time.perf_counter() - started) * 1000.0, 1)
        full_ttl = int(eff.values["analysisCacheMinutes"]) * 60
        # An analysis without the Postgres framework sessions is served briefly, so the same range is computed again.
        ttl = min(full_ttl, PG_DEGRADED_TTL_S) if result.get("degraded") else full_ttl
        now = datetime.now(timezone.utc)
        meta = self._build_meta(result, rng, ttl, duration_ms, now)
        sections: dict[str, bytes] = result["sections"]
        meta_blob = _gzip_json(meta)
        writes = {f"{prefix}:{name}": blob for name, blob in sections.items()}
        if result.get("inputs_blob"):
            await self.cache.set_many({inputs_key: result["inputs_blob"]}, ttl)
        await self._store_evidence(rng, result, quarter_key, hour_key, ttl, now)
        # Sections first, then the meta key that marks the analysis complete.
        await self.cache.set_many(writes, ttl + 60)
        await self.cache.set_many({f"{prefix}:meta": meta_blob}, ttl)
        if not result.get("degraded"):
            # A degraded analysis (without the Postgres framework sessions) never replaces the complete last good copy.
            await self._save_last_good(rng, eff, sections, meta_blob, full_ttl)
        self._log_computed(rng, duration_ms, inputs_blob, meta)
        return AnalysisHandle(prefix, meta, "miss")

    async def _compute(
        self,
        rng: RangeInfo,
        eff: EffectiveSettings,
        prefix: str,
        job_extra: dict[str, Any],
        timeout_s: float,
        admission: Admission,
    ) -> AnalysisHandle:
        inputs_key = self.inputs_key(rng, eff)
        hour_key, quarter_key = self.evidence_key(rng, eff), self.evidence_quarter_key(rng, eff)
        inputs_blob, evidence_blob = await self._load_cached_inputs(rng, inputs_key, quarter_key, hour_key)
        job = {
            "window": rng.window(),
            "evidence_window": rng.evidence_window(),
            "settings": eff.values,
            "inputs_blob": inputs_blob,
            "evidence_blob": evidence_blob,
            "pg_framework_rows": [],
            **job_extra,
        }
        postgres_failed = False

        async def read_postgres(started_job: dict[str, Any]) -> None:
            # The worker reuses cached inputs or evidence without fetching: read only when neither blob is cached.
            nonlocal postgres_failed
            if inputs_blob or evidence_blob:
                return
            rows = await pg_framework_rows(rng.evidence_start, rng.end)
            postgres_failed = rows is None
            started_job["pg_framework_rows"] = rows or []

        kind = "custom" if eff.is_custom else "default"
        started = time.perf_counter()
        try:
            result = await self._run_job(kind, job, timeout_s, admission, read_postgres)
        except Exception as exc:  # noqa: BLE001 - any failure falls back to the last good analysis
            return await self._on_compute_failure(exc, rng, eff, prefix, inputs_blob, timeout_s)
        if postgres_failed or (result.get("meta") or {}).get("postgres_merge_failed"):
            # The read or the merge of the Postgres sessions failed: the Elasticsearch-only inputs and evidence are not
            # stored, and the analysis itself stays only PG_DEGRADED_TTL_S (the last good copy keeps its lifetime), so
            # the same range is computed, and Postgres read, again after that.
            result = {**result, "inputs_blob": None, "evidence_blob": None, "degraded": True}
        return await self._store_computed_result(
            rng, eff, prefix, result, inputs_key, quarter_key, hour_key, inputs_blob, started
        )

    async def _last_good(self, rng: RangeInfo, eff: EffectiveSettings) -> AnalysisHandle | None:
        last = self._last_prefix(rng, eff)
        meta = _gunzip_json(await self.cache.get(f"{last}:meta"))
        if meta is None:
            return None
        return AnalysisHandle(last, meta, "stale")

    async def _after_failure(
        self, rng: RangeInfo, eff: EffectiveSettings, prefix: str, failure: dict[str, Any]
    ) -> AnalysisHandle:
        """A computation for this key failed moments ago: the last good analysis, or the same error, without a read."""
        stale = await self._last_good(rng, eff)
        if stale is not None:
            stale.extra["error"] = _FAILED_NOTE
            return stale
        left = await self.cache.ttl(self._failed_key(prefix))
        raise AnalysisUnavailableError(
            str(failure.get("message") or "The analysis failed."),
            int(failure.get("status_code") or 503),
            retry_after=max(1, math.ceil(left)) if left > 0 else FAILURE_BACKOFF_S,
        )

    async def _after_refusal(
        self, rng: RangeInfo, eff: EffectiveSettings, refused_key: str, refusal: dict[str, Any]
    ) -> AnalysisHandle:
        """A limit refused this user's computation of this key, and still does: the last good analysis with the limit
        and the time to wait, not refreshing, so the page stops asking; without one, the same 429. Nothing starts."""
        left = await self.cache.ttl(refused_key)
        limit = CustomLimitError(
            str(refusal.get("message") or "Too many analyses."),
            max(1, math.ceil(left)) if left > 0 else int(refusal.get("retry_after") or 1),
            str(refusal.get("note") or ""),
            str(refusal.get("option") or ""),
        )
        stale = await self._last_good(rng, eff)
        if stale is None:
            raise limit
        stale.extra["error"] = refusal_note(limit)
        return stale

    @staticmethod
    def _unavailable(exc: Exception, timeout_s: float) -> AnalysisUnavailableError:
        if isinstance(exc, AnalysisUnavailableError):
            return exc
        if isinstance(exc, asyncio.TimeoutError):
            return AnalysisUnavailableError(
                f"The analysis took longer than {timeout_s:g} s and was stopped. Try again later or narrow the period.",
                504,
                retry_after=FAILURE_BACKOFF_S,
            )
        if isinstance(exc, WorkerFailedError) and exc.error_type == "MemoryLimit":
            return AnalysisUnavailableError(
                "The analysis needed more memory than allowed and was stopped. Narrow the period, or ask an "
                "administrator to raise workerMemoryLimitMb.",
                503,
                retry_after=FAILURE_BACKOFF_S,
            )
        name = exc.error_type if isinstance(exc, WorkerFailedError) else type(exc).__name__
        return AnalysisUnavailableError(f"The analysis failed: {name}", 503, retry_after=FAILURE_BACKOFF_S)

    # ------------------------------------------------------------------ worker
    async def _run_job(
        self,
        kind: str,
        job: dict[str, Any],
        timeout_s: float,
        admission: Admission,
        before_start: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Run one computation after waiting (bounded in time and in number of waiters) for a free slot of its kind.
        ``before_start`` completes the job once the slot is held, so a refused (busy) request reads nothing for it.

        The timeout counts from the start of the computation, not from the wait. The computation counts against its
        user's limits from its start: refused before (busy, shutdown), it gives its counts back.
        """
        slot = self._slots[kind]
        if slot.locked() and self._waiting[kind] >= MAX_WAITING_PER_KIND:
            raise AnalysisBusyError
        self._waiting[kind] += 1
        try:
            await asyncio.wait_for(slot.acquire(), timeout_s)
        except asyncio.TimeoutError:
            raise AnalysisBusyError from None
        finally:
            self._waiting[kind] -= 1
        try:
            if self._closing:
                raise AnalysisUnavailableError(_RESTARTING, 503, 10)
            admission.started = True
            if before_start is not None:
                await before_start(job)
                if self._closing:  # shutdown began while the job was being completed: start no process
                    admission.started = False
                    raise AnalysisUnavailableError(_RESTARTING, 503, 10)
            if self._job_runner is not None:
                return await asyncio.wait_for(asyncio.to_thread(self._job_runner, job), timeout_s)
            return await self._run_in_process(kind, job, timeout_s)
        finally:
            slot.release()

    async def _run_in_process(self, kind: str, job: dict, timeout_s: float) -> dict:
        """A ``spawn`` process of its own: memory is released afterwards, and stopping it touches nothing else."""
        ctx = multiprocessing.get_context("spawn")
        receiver, sender = ctx.Pipe(duplex=False)
        process = ctx.Process(
            target=self._process_target, args=(sender, job), name=f"usage-attribution-{kind}", daemon=True
        )
        # Starting sends the job (with the cached inputs) to the process: off the event loop.
        await asyncio.to_thread(process.start)
        sender.close()  # the process holds the only sending end now, so the receive ends when the process does
        self._running[kind] = process
        receive = asyncio.ensure_future(asyncio.to_thread(_receive, receiver))
        try:
            message = await asyncio.wait_for(asyncio.shield(receive), timeout_s)
        finally:
            # Timed out, cancelled or done: make sure the process is gone, then collect it.
            if not receive.done():
                with contextlib.suppress(Exception):
                    process.kill()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(asyncio.shield(receive), 10)
            self._running.pop(kind, None)
            await asyncio.to_thread(process.join, 10)
            if receive.done():
                receiver.close()
        return _job_result(message, process.exitcode)


_service: AnalysisService | None = None


def get_analysis_service() -> AnalysisService:
    global _service
    if _service is None:
        _service = AnalysisService()
    return _service


def shutdown_analysis_service() -> None:
    """Stop the worker processes (application shutdown)."""
    if _service is not None:
        _service.shutdown()
