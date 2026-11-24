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

"""The spend backfill service: after each spend uploader feed, overwrites metric document money with ledger figures."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from http import HTTPStatus
from time import monotonic
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from codemie.configs import logger
from codemie.configs.config import config
from codemie.repository.spend_backfill_metrics_repository import (
    MetricDoc,
    MetricsIndexUnavailableError,
    SpendBackfillMetricsRepository,
    UnsettledDocsQuery,
    UpdateStatus,
    VersionCheckedUpdate,
)
from codemie.service.spend.coverage import LedgerCoverage, TimeRange
from codemie.service.spend.ledger_registry import ledger_coverage
from codemie.service.spend_backfill.routing_call_usage import RoutingCallUsage
from codemie.service.spend_backfill.spend_doc_type import DocUpdate, SpendDocType

DOC_TYPES: tuple[SpendDocType, ...] = (RoutingCallUsage(),)

# Margin for clock differences between CodeMie and LiteLLM.
_CLOCK_SKEW = timedelta(seconds=120)
_MAX_LOGGED_ITEM_ERRORS = 3
_MAX_REASON_CHARS = 200


class SpendBackfillService:
    """Created once per process, so its scan progress, history pass and mapping state carry over between runs."""

    def __init__(self) -> None:
        self._scan_progress = _ScanProgress()
        self._is_mapping_applied = False

    async def backfill(self, feed_until: datetime, end_margin: timedelta) -> None:
        """Backfills documents older than `end_margin` before `feed_until`."""
        repository = SpendBackfillMetricsRepository()
        settings = SpendBackfillSettings.from_config()
        if not settings.dry_run and not await self._ensure_ledger_mapping(repository):
            return
        deadline = asyncio.get_running_loop().time() + settings.timeout_seconds
        coverage = ledger_coverage()
        if not coverage.activated:
            logger.info("Spend backfill skipped: ledger not activated")
            return
        run = _Run(repository, coverage, settings, now=_get_utc_now(), deadline=deadline)
        docs_until = feed_until - end_margin - _CLOCK_SKEW
        has_timed_out = False
        for doc_type in DOC_TYPES:
            for time_range in self._plan_time_ranges(doc_type, run, docs_until=docs_until, ledger_until=feed_until):
                if has_timed_out or run.seconds_left() <= 0:
                    logger.info(f"Spend backfill window skipped: window={time_range.name}, reason=deadline")
                    continue
                has_timed_out = await self._backfill_time_range(doc_type, time_range, run)

    async def _ensure_ledger_mapping(self, repository: SpendBackfillMetricsRepository) -> bool:
        """Without the mapping a dynamically typed integer field could truncate money, so a failure skips the run."""
        if self._is_mapping_applied:
            return True
        try:
            await repository.apply_ledger_mapping()
        except MetricsIndexUnavailableError:
            logger.error("Spend backfill skipped: could not apply the ledger mapping, retrying next run", exc_info=True)
            return False
        self._is_mapping_applied = True
        return True

    def _plan_time_ranges(
        self, doc_type: SpendDocType, run: _Run, *, docs_until: datetime, ledger_until: datetime
    ) -> list[_ScanRange]:
        """Returns the recent range, then the history range until one run has scanned it to the end."""
        recent_start = run.now - run.settings.recent_window
        time_ranges: list[_ScanRange] = []
        recent = _clip_to_ledger_coverage(run.coverage, recent_start, docs_until)
        if recent is None:
            logger.info(f"Spend backfill window skipped: window={_TimeRangeName.RECENT}, reason=empty_window")
        else:
            time_ranges.append(
                _ScanRange(
                    name=_TimeRangeName.RECENT,
                    start=recent.start,
                    end=recent.end,
                    is_end_inclusive=True,
                    ledger_until=ledger_until,
                )
            )

        history_start = run.settings.history_start
        if history_start is None or self._scan_progress.is_history_complete(doc_type):
            return time_ranges
        history = _clip_to_ledger_coverage(run.coverage, history_start, recent_start)
        if history is None:
            logger.info(f"Spend backfill window skipped: window={_TimeRangeName.HISTORY}, reason=empty_window")
        else:
            time_ranges.append(
                _ScanRange(
                    name=_TimeRangeName.HISTORY,
                    start=history.start,
                    end=history.end,
                    is_end_inclusive=False,
                    ledger_until=recent_start + _CLOCK_SKEW,
                )
            )
        return time_ranges

    async def _backfill_time_range(self, doc_type: SpendDocType, time_range: _ScanRange, run: _Run) -> bool:
        """Returns whether the run's time ran out."""
        started = monotonic()
        position = self._scan_progress.get_position(doc_type, time_range.name)
        if position.last_timestamp is not None and position.last_timestamp < time_range.start:
            position.rewind()
        scan = _RangeScan(run, doc_type, time_range, position, _RangeTotals())
        is_scanned_to_end = False
        run_timeout = asyncio.timeout_at(run.deadline)
        try:
            async with run_timeout:
                await self._scan_time_range(scan)
            is_scanned_to_end = True
        except TimeoutError:
            if not run_timeout.expired():
                raise
            scan.totals.has_timed_out = True
        except MetricsIndexUnavailableError:
            logger.error(f"Spend backfill window failed: window={time_range.name}", exc_info=True)
        except (SQLAlchemyError, OSError):
            # asyncpg raises connection failures as a bare OSError. The ledger statement timeout is the time left,
            # so a cancel at the deadline is a timeout.
            scan.totals.has_timed_out = run.seconds_left() <= 0
            if not scan.totals.has_timed_out:
                logger.error(f"Spend backfill ledger read failed: window={time_range.name}", exc_info=True)
        _log_time_range_result(time_range, scan.totals, monotonic() - started)
        if time_range.name == _TimeRangeName.HISTORY:
            self._finish_history_run(doc_type, scan.totals, is_scanned_to_end=is_scanned_to_end)
        return scan.totals.has_timed_out

    def _finish_history_run(self, doc_type: SpendDocType, totals: _RangeTotals, *, is_scanned_to_end: bool) -> None:
        """A pass whose writes failed on some items starts over next run, as its position has moved past them."""
        failures = self._scan_progress.add_history_write_failures(doc_type, totals)
        if not is_scanned_to_end:
            return
        if failures.item_errors or failures.conflicts:
            self._scan_progress.restart_history_pass(doc_type)
            logger.warning(
                "Spend backfill history pass incomplete: "
                f"item_errors={failures.item_errors}, conflicts={failures.conflicts}, retrying next run"
            )
            return
        self._scan_progress.mark_history_complete(doc_type)
        logger.info("Spend backfill history pass complete")

    async def _scan_time_range(self, scan: _RangeScan) -> None:
        resume_from = scan.position.last_timestamp
        time_range = scan.time_range
        async with scan.run.repository.open_point_in_time(timeout=scan.run.seconds_left()) as pit_id:
            await self._scan_pages(scan, pit_id, time_range.narrowed_to(start=resume_from))
            if time_range.name == _TimeRangeName.RECENT and not scan.run.settings.dry_run:
                await self._rescan_before_resume_point(scan, pit_id, resume_from)

    async def _rescan_before_resume_point(self, scan: _RangeScan, pit_id: str, resume_from: datetime | None) -> None:
        """Retries pending documents before this run's start, not past it, as the point-in-time misses its writes."""
        scan.position.rewind()
        if resume_from is not None:
            await self._scan_pages(scan, pit_id, scan.time_range.narrowed_to(end=resume_from, is_end_inclusive=False))

    async def _scan_pages(self, scan: _RangeScan, pit_id: str, pages_range: _ScanRange) -> None:
        run, doc_type = scan.run, scan.doc_type
        search_after: list[Any] | None = None
        while True:
            query = UnsettledDocsQuery(
                metric_name=doc_type.metric_name,
                start=pages_range.start,
                end=pages_range.end,
                is_end_inclusive=pages_range.is_end_inclusive,
                projects=run.settings.projects,
                settlement_version=doc_type.settlement_version,
                source_fields=doc_type.source_fields,
                size=run.settings.batch_size,
                search_after=search_after,
            )
            docs = await run.repository.find_unsettled_docs(query, pit_id=pit_id, timeout=run.seconds_left())
            unread_docs = [doc for doc in docs if not scan.position.was_read(doc)]
            if unread_docs:
                await self._backfill_page(scan, unread_docs)
            scan.position.advance_past(docs)
            if len(docs) < run.settings.batch_size:
                return
            search_after = docs[-1].sort_values

    async def _backfill_page(self, scan: _RangeScan, docs: list[MetricDoc]) -> None:
        run = scan.run
        ledger_period = TimeRange(docs[0].timestamp - run.coverage.emission_lag, scan.time_range.ledger_until)
        doc_updates = await scan.doc_type.build_updates(docs, ledger_period, run.now, run.seconds_left())
        writes = [
            VersionCheckedUpdate(doc.index, doc.id, doc.seq_no, doc.primary_term, doc_update.attributes)
            for doc, doc_update in zip(docs, doc_updates, strict=True)
            if doc_update.attributes is not None
        ]
        counted_updates = doc_updates
        if writes and not run.settings.dry_run:
            statuses = await run.repository.update_docs(writes, run.seconds_left())
            scan.totals.count_update_statuses(statuses)
            counted_updates = _drop_failed_writes(doc_updates, statuses)
        scan.totals.selected_count += len(docs)
        scan.totals.count_doc_updates(counted_updates)


@dataclass(frozen=True)
class SpendBackfillSettings:
    dry_run: bool
    recent_window: timedelta
    batch_size: int
    timeout_seconds: float
    history_start: datetime | None
    projects: tuple[str, ...]

    @classmethod
    def from_config(cls) -> SpendBackfillSettings:
        history_start = config.SPEND_BACKFILL_HISTORY_START
        return cls(
            dry_run=config.SPEND_BACKFILL_DRY_RUN,
            recent_window=timedelta(hours=config.SPEND_BACKFILL_MAX_AGE_HOURS),
            batch_size=config.SPEND_BACKFILL_BATCH,
            timeout_seconds=float(config.SPEND_BACKFILL_TIMEOUT_SECONDS),
            history_start=history_start.astimezone(UTC) if history_start is not None else None,
            projects=tuple(project.strip() for project in config.SPEND_BACKFILL_PROJECTS.split(",") if project.strip()),
        )


def _clip_to_ledger_coverage(coverage: LedgerCoverage, start: datetime, end: datetime) -> TimeRange | None:
    if start >= end:
        return None
    return coverage.admitted(TimeRange(start, end))


def _drop_failed_writes(doc_updates: Sequence[DocUpdate], statuses: Sequence[UpdateStatus]) -> list[DocUpdate]:
    """`statuses` belong, in order, to the updates that have attributes; updates without attributes are kept."""
    written_statuses = iter(statuses)
    return [
        doc_update
        for doc_update in doc_updates
        if doc_update.attributes is None or _is_successful(next(written_statuses))
    ]


def _is_successful(item: UpdateStatus) -> bool:
    return HTTPStatus.OK <= item.status < HTTPStatus.MULTIPLE_CHOICES


def _log_time_range_result(time_range: _ScanRange, totals: _RangeTotals, elapsed_seconds: float) -> None:
    logger.info(
        f"Spend backfill window finished: window={time_range.name}, {totals.format_as_log_fields()}, "
        f"elapsed={elapsed_seconds:.2f}s, timed_out={str(totals.has_timed_out).lower()}"
    )
    for item in totals.logged_item_errors:
        reason = (item.reason or "")[:_MAX_REASON_CHARS]
        logger.warning(
            f"Spend backfill bulk item failed: window={time_range.name}, status={item.status}, "
            f"type={item.error_type}, reason={reason}"
        )


def _get_utc_now() -> datetime:
    return datetime.now(UTC)


class _TimeRangeName(StrEnum):
    RECENT = "recent"
    HISTORY = "history"


@dataclass(frozen=True)
class _Run:
    """What every step of one run shares; `deadline` is in event loop time."""

    repository: SpendBackfillMetricsRepository
    coverage: LedgerCoverage
    settings: SpendBackfillSettings
    now: datetime
    deadline: float

    def seconds_left(self) -> float:
        return max(self.deadline - asyncio.get_running_loop().time(), 0.0)


@dataclass(frozen=True)
class _ScanRange:
    """Documents from `start` to `end`, matched to ledger rows up to `ledger_until`."""

    name: _TimeRangeName
    start: datetime
    end: datetime
    is_end_inclusive: bool
    ledger_until: datetime

    def narrowed_to(
        self, *, start: datetime | None = None, end: datetime | None = None, is_end_inclusive: bool | None = None
    ) -> _ScanRange:
        """Returns this range with the given bounds; a bound left as None is kept."""
        return _ScanRange(
            name=self.name,
            start=self.start if start is None else start,
            end=self.end if end is None else end,
            is_end_inclusive=self.is_end_inclusive if is_end_inclusive is None else is_end_inclusive,
            ledger_until=self.ledger_until,
        )


@dataclass(frozen=True)
class _RangeScan:
    """One time range being scanned in one run."""

    run: _Run
    doc_type: SpendDocType
    time_range: _ScanRange
    position: _ScanPosition
    totals: _RangeTotals


@dataclass
class _RangeTotals:
    """Counts and money sums of one time range, for its log line."""

    selected_count: int = 0
    status_counts: Counter[str] = field(default_factory=Counter)
    log_sums: dict[str, float] = field(default_factory=dict)
    log_maxima: dict[str, float] = field(default_factory=dict)
    conflict_count: int = 0
    item_error_counts: Counter[int] = field(default_factory=Counter)
    logged_item_errors: list[UpdateStatus] = field(default_factory=list)
    has_timed_out: bool = False

    def count_doc_updates(self, doc_updates: Sequence[DocUpdate]) -> None:
        for doc_update in doc_updates:
            status = str(doc_update.status)
            self.status_counts[status] += 1
            if doc_update.reason:
                self.status_counts[f"{status}_{doc_update.reason}"] += 1
            for name, value in doc_update.log_sums.items():
                self.log_sums[name] = self.log_sums.get(name, 0.0) + value
            for name, value in doc_update.log_maxima.items():
                self.log_maxima[name] = max(self.log_maxima.get(name, value), value)

    def count_update_statuses(self, statuses: Sequence[UpdateStatus]) -> None:
        for item in statuses:
            if item.status == HTTPStatus.CONFLICT:
                self.conflict_count += 1
            elif not _is_successful(item):
                self.item_error_counts[item.status] += 1
                if len(self.logged_item_errors) < _MAX_LOGGED_ITEM_ERRORS:
                    self.logged_item_errors.append(item)

    def format_as_log_fields(self) -> str:
        fields = [f"selected={self.selected_count}"]
        fields += [f"{name}={count}" for name, count in sorted(self.status_counts.items())]
        fields += [f"conflicts={self.conflict_count}", f"item_errors={self.item_error_counts.total()}"]
        fields += [f"item_errors_{status}={count}" for status, count in sorted(self.item_error_counts.items())]
        fields += [f"{name}={value:.6f}" for name, value in sorted(self.log_sums.items())]
        fields += [f"{name}={value:.6f}" for name, value in sorted(self.log_maxima.items())]
        return ", ".join(fields)


class _ScanProgress:
    """Where each document type's time ranges stopped, kept across runs so each run resumes there."""

    def __init__(self) -> None:
        self._positions: dict[tuple[str, _TimeRangeName], _ScanPosition] = {}
        self._history_complete_metric_names: set[str] = set()
        self._history_write_failures: dict[str, _WriteFailures] = {}

    def get_position(self, doc_type: SpendDocType, range_name: _TimeRangeName) -> _ScanPosition:
        return self._positions.setdefault((doc_type.metric_name, range_name), _ScanPosition())

    def is_history_complete(self, doc_type: SpendDocType) -> bool:
        return doc_type.metric_name in self._history_complete_metric_names

    def add_history_write_failures(self, doc_type: SpendDocType, totals: _RangeTotals) -> _WriteFailures:
        """Returns the failures of the history pass so far, which can span several runs."""
        failures = self._history_write_failures.setdefault(doc_type.metric_name, _WriteFailures())
        failures.item_errors += totals.item_error_counts.total()
        failures.conflicts += totals.conflict_count
        return failures

    def mark_history_complete(self, doc_type: SpendDocType) -> None:
        self._history_write_failures.pop(doc_type.metric_name, None)
        self._history_complete_metric_names.add(doc_type.metric_name)

    def restart_history_pass(self, doc_type: SpendDocType) -> None:
        self._history_write_failures.pop(doc_type.metric_name, None)
        self.get_position(doc_type, _TimeRangeName.HISTORY).rewind()


@dataclass
class _WriteFailures:
    item_errors: int = 0
    conflicts: int = 0


@dataclass
class _ScanPosition:
    """The last `@timestamp` read and the ids read at it, as `_shard_doc` sort values die with their point-in-time."""

    last_timestamp: datetime | None = None
    ids_at_last_timestamp: set[str] = field(default_factory=set)

    def rewind(self) -> None:
        self.last_timestamp = None
        self.ids_at_last_timestamp = set()

    def was_read(self, doc: MetricDoc) -> bool:
        return doc.timestamp == self.last_timestamp and doc.id in self.ids_at_last_timestamp

    def advance_past(self, docs: Sequence[MetricDoc]) -> None:
        for doc in docs:
            if doc.timestamp != self.last_timestamp:
                self.last_timestamp = doc.timestamp
                self.ids_at_last_timestamp = set()
            self.ids_at_last_timestamp.add(doc.id)
