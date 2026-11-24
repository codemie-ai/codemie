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

from __future__ import annotations

import asyncio
import re
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError

from codemie.repository.spend_backfill_metrics_repository import (
    MetricDoc,
    MetricsIndexUnavailableError,
    UnsettledDocsQuery,
    UpdateStatus,
)
from codemie.service.spend.coverage import LedgerCoverage, TimeRange
from codemie.service.spend_backfill import spend_backfill_service as service_module
from codemie.service.spend_backfill.spend_backfill_service import (
    _CLOCK_SKEW,
    SpendBackfillService,
    SpendBackfillSettings,
    _TimeRangeName,
)
from codemie.service.spend_backfill.spend_doc_type import DocUpdate, SpendDocType

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
UNTIL = NOW - timedelta(minutes=1)
FLUSHED = NOW - timedelta(minutes=5)
RECENT_START = NOW - timedelta(hours=48)
FLOOR = datetime(2026, 11, 1, tzinfo=UTC)
LAG = timedelta(hours=24)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
METRIC = "fake_metric"
OTHER_METRIC = "other_fake_metric"


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def millis(moment: datetime) -> int:
    return (moment - EPOCH) // timedelta(milliseconds=1)


async def wait_forever() -> None:
    await asyncio.Event().wait()


class FakeRepository:
    """Answers the service's page requests from an in-memory store, honouring the metric, time range and version."""

    def __init__(self, store: dict[str, dict]):
        self.store = store
        self.searches: list[dict] = []
        self.writes: list[list] = []
        self.write_statuses: list[list] = []
        self.pits = 0
        self.scan_timeouts: list[float] = []
        self.mapping_calls = 0
        self.mapping_errors: list[Exception] = []
        self.before_search = None
        self.fail_when = None

    async def apply_ledger_mapping(self) -> None:
        self.mapping_calls += 1
        if self.mapping_errors:
            raise self.mapping_errors.pop(0)

    def open_point_in_time(self, *, timeout: float):
        self.scan_timeouts.append(timeout)
        self.pits += 1
        pit_id = f"pit-{self.pits}"

        @asynccontextmanager
        async def pit():
            yield pit_id

        return pit()

    async def find_unsettled_docs(self, query: UnsettledDocsQuery, *, pit_id: str, timeout: float) -> list[MetricDoc]:
        arguments = {**asdict(query), "pit_id": pit_id, "timeout": timeout}
        self.searches.append(arguments)
        if self.before_search:
            await self.before_search()
        if self.fail_when and self.fail_when(arguments):
            raise MetricsIndexUnavailableError("search failed: boom")
        start, end = arguments["start"], arguments["end"]
        candidates = []
        for doc_id, doc in self.store.items():
            if doc["metric"] != arguments["metric_name"]:
                continue
            moment = doc["ts"]
            if moment < start or moment > end or (moment == end and not arguments["is_end_inclusive"]):
                continue
            if doc["attributes"].get("ledger_settlement_version", 0) >= arguments["settlement_version"]:
                continue
            candidates.append((millis(moment), doc_id))
        candidates.sort()
        if arguments["search_after"] is not None:
            candidates = [c for c in candidates if c > tuple(arguments["search_after"])]
        return [self._doc(doc_id, key) for key, doc_id in candidates[: arguments["size"]]]

    async def update_docs(self, updates, timeout):
        self.writes.append(list(updates))
        statuses = self.write_statuses.pop(0) if self.write_statuses else [200] * len(updates)
        items = [status if isinstance(status, UpdateStatus) else UpdateStatus(status) for status in statuses]
        for update, item in zip(updates, items, strict=True):
            if item.status == 200:
                self.store[update.id]["attributes"].update(update.attributes)
        return items

    def _doc(self, doc_id: str, key: int) -> MetricDoc:
        return MetricDoc(
            index="codemie_metrics_logs-2026-q4",
            id=doc_id,
            seq_no=5,
            primary_term=1,
            timestamp=EPOCH + timedelta(milliseconds=key),
            sort_values=[key, doc_id],
            attributes=dict(self.store[doc_id]["attributes"]),
        )


class FakeDocType(SpendDocType):
    metric_name = METRIC
    settlement_version = 1
    source_fields = ("attributes.kind", "attributes.cost")

    def __init__(self):
        self.calls: list[tuple[list[MetricDoc], TimeRange, float]] = []
        self.fail_when = None
        self.error: Exception = OperationalError("SELECT 1", {}, Exception("canceling statement due to timeout"))

    async def build_updates(self, docs, ledger_period, now, timeout):
        if self.fail_when and self.fail_when(docs, timeout):
            raise self.error
        self.calls.append((list(docs), ledger_period, timeout))
        return [self._build_update(doc) for doc in docs]

    @staticmethod
    def _build_update(doc: MetricDoc) -> DocUpdate:
        if doc.attributes["kind"] == "pending":
            return DocUpdate(status="pending", reason="no_rows", attributes=None)
        return DocUpdate(
            status="settled",
            reason=None,
            attributes={"ledger_settlement": "settled", "ledger_settlement_version": 1},
            log_sums={"original_cost_before": 1.0, "original_cost_after": 2.0},
            log_maxima={"max_relative_change": 0.25},
        )

    def updated_ids(self) -> list[str]:
        return [doc.id for docs, _, _ in self.calls for doc in docs]


class OtherFakeDocType(FakeDocType):
    metric_name = OTHER_METRIC


def doc(ts: datetime, kind: str = "settled", metric: str = METRIC) -> dict:
    return {"ts": ts, "metric": metric, "attributes": {"kind": kind}}


def settings(**overrides) -> SpendBackfillSettings:
    values = {
        "dry_run": True,
        "recent_window": timedelta(hours=48),
        "batch_size": 2,
        "timeout_seconds": 60.0,
        "history_start": None,
        "projects": (),
    }
    values.update(overrides)
    return SpendBackfillSettings(**values)


def coverage(floor: datetime | None = FLOOR) -> LedgerCoverage:
    return LedgerCoverage(coverage_floor=floor, emission_lag=LAG, ready=True)


class Harness:
    def __init__(self, store, **setting_overrides):
        self.store = store
        self.repository = FakeRepository(store)
        self.doc_type = FakeDocType()
        self.doc_types: tuple[SpendDocType, ...] = (self.doc_type,)
        self.service = SpendBackfillService()
        self.coverage = coverage()
        self.settings = settings(**setting_overrides)

    @property
    def progress(self):
        return self.service._scan_progress

    def position(self, range_name: _TimeRangeName):
        return self.progress.get_position(self.doc_type, range_name)

    async def run(self, docs_until: datetime = FLUSHED, feed_until: datetime = UNTIL) -> None:
        with (
            patch.object(service_module, "SpendBackfillMetricsRepository", return_value=self.repository),
            patch.object(service_module, "ledger_coverage", return_value=self.coverage),
            patch.object(service_module.SpendBackfillSettings, "from_config", return_value=self.settings),
            patch.object(service_module, "DOC_TYPES", self.doc_types),
        ):
            await self.service.backfill(feed_until, end_margin=feed_until - docs_until - _CLOCK_SKEW)


@pytest.fixture(autouse=True)
def frozen_now():
    with patch.object(service_module, "_get_utc_now", return_value=NOW):
        yield


@pytest.fixture
def log():
    with patch.object(service_module, "logger") as logger:
        yield logger


def info_lines(logger) -> list[str]:
    return [call.args[0] for call in logger.info.call_args_list]


def history_incomplete_lines(logger) -> list[str]:
    lines = [call.args[0] for call in logger.warning.call_args_list]
    return [line for line in lines if line.startswith("Spend backfill history pass incomplete:")]


def window_lines(logger, window: str) -> list[str]:
    return [line for line in info_lines(logger) if line.startswith(f"Spend backfill window finished: window={window},")]


def time_range(search: dict) -> dict:
    return {"gte": iso(search["start"]), "lte" if search["is_end_inclusive"] else "lt": iso(search["end"])}


def fields(line: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=([^,\s]+)", line))


@pytest.mark.asyncio
async def test_recent_range_searches_the_doc_type_metric_time_range_projects_and_version(log):
    harness = Harness({"a": doc(NOW - timedelta(hours=1))}, projects=("alpha", "beta"))

    await harness.run()

    search = harness.repository.searches[0]
    assert search["metric_name"] == METRIC
    assert time_range(search) == {"gte": iso(RECENT_START), "lte": iso(FLUSHED)}
    assert search["projects"] == ("alpha", "beta")
    assert search["settlement_version"] == 1
    assert search["source_fields"] == FakeDocType.source_fields
    assert search["size"] == 2
    assert search["search_after"] is None


@pytest.mark.asyncio
async def test_each_doc_type_in_doc_types_gets_its_own_pass(log):
    store = {
        "a": doc(NOW - timedelta(hours=2)),
        "x": doc(NOW - timedelta(hours=1), metric=OTHER_METRIC),
        "h": doc(RECENT_START - timedelta(hours=5), metric=OTHER_METRIC),
    }
    harness = Harness(store, history_start=FLOOR)
    other = OtherFakeDocType()
    harness.doc_types = (harness.doc_type, other)

    await harness.run()

    assert [search["metric_name"] for search in harness.repository.searches] == [
        METRIC,
        METRIC,
        OTHER_METRIC,
        OTHER_METRIC,
    ]
    assert harness.doc_type.updated_ids() == ["a"]
    assert other.updated_ids() == ["x", "h"]
    assert len(window_lines(log, "recent")) == 2
    assert len(window_lines(log, "history")) == 2
    assert harness.progress.is_history_complete(harness.doc_type)
    assert harness.progress.is_history_complete(other)


@pytest.mark.asyncio
async def test_history_range_ends_exclusively_at_the_recent_start_and_is_clipped_to_the_floor(log):
    harness = Harness({}, history_start=datetime(2026, 10, 1, tzinfo=UTC))

    await harness.run()

    [recent, history] = [time_range(search) for search in harness.repository.searches]
    assert recent == {"gte": iso(RECENT_START), "lte": iso(FLUSHED)}
    assert history == {"gte": iso(FLOOR), "lt": iso(RECENT_START)}
    assert harness.repository.pits == 2


@pytest.mark.asyncio
async def test_ledger_period_starts_at_oldest_doc_minus_emission_lag(log):
    old = RECENT_START - timedelta(hours=3)
    harness = Harness(
        {"a": doc(NOW - timedelta(hours=2)), "h": doc(old)}, history_start=datetime(2026, 11, 2, tzinfo=UTC)
    )

    await harness.run()

    [(recent_docs, recent_period, _), (history_docs, history_period, _)] = harness.doc_type.calls
    assert [doc.id for doc in recent_docs] == ["a"]
    assert recent_period == TimeRange(NOW - timedelta(hours=2) - LAG, UNTIL)
    assert [doc.id for doc in history_docs] == ["h"]
    assert history_period == TimeRange(old - LAG, RECENT_START + _CLOCK_SKEW)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("docs_until", "floor"),
    [
        (RECENT_START - timedelta(minutes=1), FLOOR),
        (RECENT_START, FLOOR),
        (NOW - timedelta(hours=2), NOW - timedelta(hours=1)),
    ],
    ids=["inverted", "empty", "below_floor"],
)
async def test_empty_recent_range_is_skipped_with_a_log_line(log, docs_until, floor):
    harness = Harness({"a": doc(NOW - timedelta(hours=3))})
    harness.coverage = coverage(floor)

    await harness.run(docs_until=docs_until)

    assert harness.repository.searches == []
    assert harness.repository.pits == 0
    assert harness.doc_type.calls == []
    assert info_lines(log) == ["Spend backfill window skipped: window=recent, reason=empty_window"]


@pytest.mark.asyncio
async def test_empty_history_range_is_skipped_with_a_log_line(log):
    harness = Harness({}, history_start=RECENT_START)

    await harness.run()

    assert "Spend backfill window skipped: window=history, reason=empty_window" in info_lines(log)
    assert harness.repository.pits == 1


@pytest.mark.asyncio
async def test_skips_with_one_line_when_ledger_not_activated(log):
    harness = Harness({"a": doc(NOW - timedelta(hours=1))}, history_start=FLOOR)
    harness.coverage = LedgerCoverage()

    await harness.run()

    assert harness.repository.searches == []
    assert harness.doc_type.calls == []
    assert info_lines(log) == ["Spend backfill skipped: ledger not activated"]


@pytest.mark.asyncio
async def test_resume_skips_seen_ids_at_same_timestamp(log):
    t1 = NOW - timedelta(hours=3)
    t2 = NOW - timedelta(hours=2)
    t3 = NOW - timedelta(hours=1)
    store = {"a": doc(t1), "b": doc(t2), "c": doc(t2)}
    harness = Harness(store, batch_size=3)

    await harness.run()
    assert harness.doc_type.updated_ids() == ["a", "b", "c"]

    store["d"] = doc(t2)
    store["e"] = doc(t3)
    harness.doc_type.calls.clear()
    first_search = len(harness.repository.searches)
    await harness.run()

    assert harness.doc_type.updated_ids() == ["d", "e"]
    resumed = harness.repository.searches[first_search]
    assert time_range(resumed) == {"gte": iso(t2), "lte": iso(FLUSHED)}
    assert resumed["search_after"] is None
    assert resumed["pit_id"] != harness.repository.searches[0]["pit_id"]


@pytest.mark.asyncio
async def test_each_service_keeps_its_own_progress(log):
    store = {"a": doc(NOW - timedelta(hours=2))}
    first = Harness(store)
    second = Harness(store)

    await first.run()
    await second.run()
    await first.run()

    assert second.doc_type.updated_ids() == ["a"]
    assert first.doc_type.updated_ids() == ["a"]


@pytest.mark.asyncio
async def test_search_after_stays_inside_one_point_in_time(log):
    store = {name: doc(NOW - timedelta(hours=hours)) for name, hours in (("a", 4), ("b", 3), ("c", 2))}
    harness = Harness(store)

    await harness.run()

    [first, second] = harness.repository.searches
    assert first["search_after"] is None
    assert second["search_after"] == [millis(NOW - timedelta(hours=3)), "b"]
    assert first["pit_id"] == second["pit_id"]
    assert harness.doc_type.updated_ids() == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_timeout_stops_the_run_and_logs_the_skipped_range(log):
    store = {name: doc(NOW - timedelta(hours=hours)) for name, hours in (("a", 5), ("b", 4), ("c", 3), ("d", 2))}
    harness = Harness(store, timeout_seconds=0.2, history_start=FLOOR)

    async def take_time_then_hang_on_second_search():
        if len(harness.repository.searches) == 1:
            await asyncio.sleep(0.05)
        else:
            await wait_forever()

    harness.repository.before_search = take_time_then_hang_on_second_search

    await harness.run()

    [first, second] = harness.repository.searches
    assert first["timeout"] == pytest.approx(0.2, abs=0.02)
    assert 0 < second["timeout"] < first["timeout"] - 0.03
    assert harness.doc_type.updated_ids() == ["a", "b"]
    [line] = window_lines(log, "recent")
    assert fields(line)["timed_out"] == "true"
    assert fields(line)["selected"] == "2"
    assert window_lines(log, "history") == []
    assert "Spend backfill window skipped: window=history, reason=deadline" in info_lines(log)
    log.error.assert_not_called()


@pytest.mark.asyncio
async def test_page_cut_by_the_timeout_is_not_marked_read_and_is_repeated_next_run(log):
    store = {name: doc(NOW - timedelta(hours=hours)) for name, hours in (("a", 3), ("b", 2))}
    harness = Harness(store, timeout_seconds=0.2, dry_run=False)

    async def hang_on_write(updates, timeout):
        await wait_forever()

    with patch.object(harness.repository, "update_docs", hang_on_write):
        await harness.run()

    assert harness.position(_TimeRangeName.RECENT).last_timestamp is None
    assert fields(window_lines(log, "recent")[0])["timed_out"] == "true"

    await harness.run()

    assert harness.doc_type.updated_ids() == ["a", "b", "a", "b"]
    assert [doc.id for doc in harness.repository.writes[0]] == ["a", "b"]


@pytest.mark.asyncio
async def test_conflicts_and_item_errors_counted_apart_and_run_continues(log):
    store = {name: doc(NOW - timedelta(hours=hours)) for name, hours in (("a", 5), ("b", 4), ("c", 3), ("d", 2))}
    harness = Harness(store, dry_run=False, batch_size=3)
    harness.repository.write_statuses = [[200, 409, 503], [200]]

    await harness.run()

    assert [len(write) for write in harness.repository.writes] == [3, 1]
    [line] = window_lines(log, "recent")
    values = fields(line)
    assert values["conflicts"] == "1"
    assert values["item_errors"] == "1"
    assert values["settled"] == "2"


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_status", [429, 409])
async def test_doc_whose_write_failed_is_neither_counted_nor_summed(log, failed_status):
    store = {
        "a": doc(NOW - timedelta(hours=3)),
        "p": doc(NOW - timedelta(hours=2), kind="pending"),
        "b": doc(NOW - timedelta(hours=1)),
    }
    harness = Harness(store, dry_run=False, batch_size=3)
    harness.repository.write_statuses = [[failed_status, 200]]

    await harness.run()

    [line] = window_lines(log, "recent")
    values = fields(line)
    assert (values["selected"], values["settled"], values["pending_no_rows"]) == ("3", "1", "1")
    assert float(values["original_cost_before"]) == pytest.approx(1.0)
    assert float(values["original_cost_after"]) == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_dry_run_counts_and_sums_every_doc(log):
    store = {"a": doc(NOW - timedelta(hours=2)), "b": doc(NOW - timedelta(hours=1))}
    harness = Harness(store, dry_run=True)

    await harness.run()

    [line] = window_lines(log, "recent")
    values = fields(line)
    assert values["settled"] == "2"
    assert float(values["original_cost_before"]) == pytest.approx(2.0)
    assert harness.repository.writes == []


@pytest.mark.asyncio
async def test_item_errors_counted_by_status_and_first_reasons_logged(log):
    store = {f"d{n}": doc(NOW - timedelta(hours=6 - n)) for n in range(5)}
    harness = Harness(store, dry_run=False, batch_size=5)
    harness.repository.write_statuses = [
        [
            UpdateStatus(400, "mapper_parsing_exception", "failed to parse field [attributes.ledger_rows]"),
            UpdateStatus(400, "mapper_parsing_exception", "failed to parse field [attributes.ledger_spend_usd]"),
            UpdateStatus(403, "cluster_block_exception", "index read-only"),
            UpdateStatus(503, "unavailable_shards_exception", "primary shard is not active"),
            UpdateStatus(200),
        ]
    ]

    await harness.run()

    [line] = window_lines(log, "recent")
    values = fields(line)
    assert values["item_errors"] == "4"
    assert values["item_errors_400"] == "2"
    assert values["item_errors_403"] == "1"
    assert values["item_errors_503"] == "1"
    reasons = [call.args[0] for call in log.warning.call_args_list if "bulk item failed" in call.args[0]]
    assert len(reasons) == 3
    assert "status=400" in reasons[0]
    assert "type=mapper_parsing_exception" in reasons[0]
    assert "failed to parse field [attributes.ledger_rows]" in reasons[0]
    assert all("window=recent" in reason for reason in reasons)


@pytest.mark.asyncio
async def test_write_addresses_each_doc(log):
    harness = Harness({"a": doc(NOW - timedelta(hours=1))}, dry_run=False)

    await harness.run()

    [[update]] = harness.repository.writes
    assert (update.index, update.id, update.seq_no, update.primary_term) == ("codemie_metrics_logs-2026-q4", "a", 5, 1)
    assert update.attributes == {"ledger_settlement": "settled", "ledger_settlement_version": 1}


@pytest.mark.asyncio
async def test_write_mode_wraps_at_most_once_per_run(log):
    times = {name: NOW - timedelta(hours=hours) for name, hours in (("p1", 5), ("p2", 4), ("p3", 3), ("p4", 2))}
    store = {name: doc(moment, "pending") for name, moment in times.items()}
    harness = Harness(store, dry_run=False)
    position = harness.position(_TimeRangeName.RECENT)
    position.last_timestamp = times["p2"]
    position.ids_at_last_timestamp = {"p2"}

    await harness.run()

    assert harness.doc_type.updated_ids() == ["p3", "p4", "p1"]
    wrapped = [time_range(search) for search in harness.repository.searches][-1]
    assert wrapped == {"gte": iso(RECENT_START), "lt": iso(times["p2"])}
    assert len({search["pit_id"] for search in harness.repository.searches}) == 1


@pytest.mark.asyncio
async def test_stale_position_before_range_start_is_reset(log):
    inside = NOW - timedelta(hours=1)
    store = {"old": doc(RECENT_START - timedelta(hours=1)), "a": doc(inside)}
    harness = Harness(store)
    position = harness.position(_TimeRangeName.RECENT)
    position.last_timestamp = RECENT_START - timedelta(hours=2)
    position.ids_at_last_timestamp = {"gone"}

    await harness.run()

    assert time_range(harness.repository.searches[0])["gte"] == iso(RECENT_START)
    assert harness.doc_type.updated_ids() == ["a"]
    assert (position.last_timestamp, position.ids_at_last_timestamp) == (inside, {"a"})


@pytest.mark.asyncio
async def test_write_mode_restarts_from_range_start_after_reaching_end(log):
    store = {"p1": doc(NOW - timedelta(hours=3), "pending")}
    harness = Harness(store, dry_run=False)

    await harness.run()
    await harness.run()

    assert harness.doc_type.updated_ids() == ["p1", "p1"]
    assert time_range(harness.repository.searches[-1]) == {"gte": iso(RECENT_START), "lte": iso(FLUSHED)}


@pytest.mark.asyncio
async def test_dry_run_does_not_wrap_or_write(log):
    times = {name: NOW - timedelta(hours=hours) for name, hours in (("p1", 5), ("p2", 4), ("p3", 3), ("p4", 2))}
    store = {name: doc(moment) for name, moment in times.items()}
    harness = Harness(store, dry_run=True)
    position = harness.position(_TimeRangeName.RECENT)
    position.last_timestamp = times["p2"]
    position.ids_at_last_timestamp = {"p2"}

    await harness.run()

    assert harness.doc_type.updated_ids() == ["p3", "p4"]
    assert harness.repository.writes == []


@pytest.mark.asyncio
async def test_dry_run_counts_each_document_once_across_runs(log):
    store = {"a": doc(NOW - timedelta(hours=3)), "b": doc(NOW - timedelta(hours=2))}
    harness = Harness(store, dry_run=True)

    await harness.run()
    store["c"] = doc(NOW - timedelta(hours=1))
    await harness.run()

    first, second = window_lines(log, "recent")
    assert (fields(first)["selected"], fields(first)["settled"]) == ("2", "2")
    assert (fields(second)["selected"], fields(second)["settled"]) == ("1", "1")
    total_before = float(fields(first)["original_cost_before"]) + float(fields(second)["original_cost_before"])
    assert total_before == pytest.approx(3.0)
    assert harness.doc_type.updated_ids() == ["a", "b", "c"]
    assert harness.repository.writes == []


@pytest.mark.asyncio
async def test_history_pass_complete_logged_once_and_not_rescanned(log):
    store = {"h": doc(RECENT_START - timedelta(hours=5))}
    harness = Harness(store, history_start=FLOOR)

    await harness.run()
    await harness.run()

    history_searches = [search for search in harness.repository.searches if not search["is_end_inclusive"]]
    assert len(history_searches) == 1
    assert info_lines(log).count("Spend backfill history pass complete") == 1
    assert harness.doc_type.updated_ids() == ["h"]


@pytest.mark.asyncio
async def test_clean_write_history_pass_completes_once(log):
    harness = Harness({"h": doc(RECENT_START - timedelta(hours=5))}, history_start=FLOOR, dry_run=False)

    await harness.run()
    await harness.run()

    assert harness.progress.is_history_complete(harness.doc_type)
    assert info_lines(log).count("Spend backfill history pass complete") == 1
    assert history_incomplete_lines(log) == []
    assert harness.doc_type.updated_ids() == ["h"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("failed_status", "item_errors", "conflicts"), [(429, 1, 0), (409, 0, 1)])
async def test_history_pass_with_failed_items_is_not_complete_and_rescans_from_its_start(
    log, failed_status, item_errors, conflicts
):
    store = {"h1": doc(RECENT_START - timedelta(hours=6)), "h2": doc(RECENT_START - timedelta(hours=5))}
    harness = Harness(store, history_start=FLOOR, dry_run=False)
    harness.repository.write_statuses = [[failed_status, 200]]

    await harness.run()

    assert not harness.progress.is_history_complete(harness.doc_type)
    assert "Spend backfill history pass complete" not in info_lines(log)
    assert history_incomplete_lines(log) == [
        f"Spend backfill history pass incomplete: item_errors={item_errors}, conflicts={conflicts}, retrying next run"
    ]
    assert harness.position(_TimeRangeName.HISTORY).last_timestamp is None

    await harness.run()

    assert harness.progress.is_history_complete(harness.doc_type)
    assert info_lines(log).count("Spend backfill history pass complete") == 1
    assert harness.doc_type.updated_ids() == ["h1", "h2", "h1"]
    assert len(history_incomplete_lines(log)) == 1


@pytest.mark.asyncio
async def test_history_failures_of_a_run_cut_short_still_restart_the_pass(log):
    store = {"h1": doc(RECENT_START - timedelta(hours=6)), "h2": doc(RECENT_START - timedelta(hours=5))}
    harness = Harness(store, history_start=FLOOR, batch_size=1, dry_run=False)
    harness.repository.write_statuses = [[429]]
    harness.repository.fail_when = lambda search: not search["is_end_inclusive"] and search["search_after"]

    await harness.run()

    assert harness.doc_type.updated_ids() == ["h1"]
    assert history_incomplete_lines(log) == []

    harness.repository.fail_when = None
    await harness.run()

    assert not harness.progress.is_history_complete(harness.doc_type)
    assert history_incomplete_lines(log) == [
        "Spend backfill history pass incomplete: item_errors=1, conflicts=0, retrying next run"
    ]
    assert harness.position(_TimeRangeName.HISTORY).last_timestamp is None


@pytest.mark.asyncio
async def test_history_pass_cut_short_is_not_marked_complete_and_resumes(log):
    store = {"h1": doc(RECENT_START - timedelta(hours=6)), "h2": doc(RECENT_START - timedelta(hours=5))}
    harness = Harness(store, history_start=FLOOR, batch_size=1)
    history_searches: list[dict] = []

    def fail_on_second_history_page(search):
        if search["is_end_inclusive"]:
            return False
        history_searches.append(search)
        return len(history_searches) == 2

    harness.repository.fail_when = fail_on_second_history_page

    await harness.run()

    assert not harness.progress.is_history_complete(harness.doc_type)
    assert "Spend backfill history pass complete" not in info_lines(log)
    assert harness.doc_type.updated_ids() == ["h1"]

    harness.repository.fail_when = None
    await harness.run()

    assert harness.progress.is_history_complete(harness.doc_type)
    assert harness.doc_type.updated_ids() == ["h1", "h2"]


@pytest.mark.asyncio
async def test_second_write_run_writes_nothing(log):
    store = {"a": doc(NOW - timedelta(hours=3)), "b": doc(NOW - timedelta(hours=2))}
    harness = Harness(store, dry_run=False)

    await harness.run()
    written = sum(len(write) for write in harness.repository.writes)
    await harness.run()

    assert written == 2
    assert sum(len(write) for write in harness.repository.writes) == 2
    assert fields(window_lines(log, "recent")[-1])["selected"] == "0"


@pytest.mark.asyncio
async def test_range_summary_reports_counts_and_money_totals_but_no_settings(log):
    store = {"a": doc(NOW - timedelta(hours=3)), "b": doc(NOW - timedelta(hours=2), "pending")}
    harness = Harness(store, projects=("secret-project",))

    await harness.run()

    [line] = window_lines(log, "recent")
    values = fields(line)
    assert values["selected"] == "2"
    assert values["settled"] == "1"
    assert values["pending"] == "1"
    assert values["pending_no_rows"] == "1"
    assert values["conflicts"] == "0"
    assert values["item_errors"] == "0"
    assert float(values["original_cost_before"]) == pytest.approx(1.0)
    assert float(values["original_cost_after"]) == pytest.approx(2.0)
    assert float(values["max_relative_change"]) == pytest.approx(0.25)
    assert values["timed_out"] == "false"
    assert values["elapsed"].endswith("s")
    for absent in ("pod", "target", "dry_run", "cursor_reset"):
        assert absent not in values
    assert "secret-project" not in line


@pytest.mark.asyncio
async def test_elasticsearch_failure_is_logged_once_as_an_error_and_next_range_runs(log):
    harness = Harness({"h": doc(RECENT_START - timedelta(hours=5))}, history_start=FLOOR)
    harness.repository.fail_when = lambda search: search["is_end_inclusive"]

    await harness.run()

    assert harness.doc_type.updated_ids() == ["h"]
    [error] = log.error.call_args_list
    assert error.args[0] == "Spend backfill window failed: window=recent"
    assert error.kwargs["exc_info"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        OperationalError("SELECT 1", {}, Exception("connection reset")),
        ConnectionRefusedError(111, "Connect call failed"),
        socket.gaierror(11001, "getaddrinfo failed"),
    ],
    ids=["sqlalchemy", "connection_refused", "unknown_host"],
)
async def test_ledger_error_is_logged_as_an_error_and_next_range_runs(log, error):
    store = {"a": doc(NOW - timedelta(hours=1)), "h": doc(RECENT_START - timedelta(hours=5))}
    harness = Harness(store, history_start=FLOOR)
    harness.doc_type.error = error
    harness.doc_type.fail_when = lambda docs, timeout: docs[0].id == "a"

    await harness.run()

    [line] = window_lines(log, "recent")
    assert fields(line)["selected"] == "0"
    assert fields(line)["timed_out"] == "false"
    [error] = log.error.call_args_list
    assert error.args[0] == "Spend backfill ledger read failed: window=recent"
    assert error.kwargs["exc_info"] is True
    assert harness.doc_type.updated_ids() == ["h"]


@pytest.mark.asyncio
async def test_ledger_statement_timeout_counts_as_timed_out_without_an_error(log):
    harness = Harness({"a": doc(NOW - timedelta(hours=1))}, timeout_seconds=0.1)

    def cancelled_at_the_deadline(docs, timeout):
        time.sleep(timeout + 0.05)
        return True

    harness.doc_type.fail_when = cancelled_at_the_deadline

    await harness.run()

    [line] = window_lines(log, "recent")
    assert fields(line)["timed_out"] == "true"
    log.error.assert_not_called()


@pytest.mark.asyncio
async def test_timeouts_come_from_the_time_left(log):
    harness = Harness({"a": doc(NOW - timedelta(hours=1))}, timeout_seconds=30.0)

    await harness.run()

    assert 0 < harness.repository.scan_timeouts[0] <= 30.0
    assert 0 < harness.repository.searches[0]["timeout"] <= 30.0
    assert 0 < harness.doc_type.calls[0][2] <= 30.0


@pytest.mark.asyncio
async def test_unexpected_failure_reaches_the_caller(log):
    harness = Harness({"a": doc(NOW - timedelta(hours=1))})

    async def fail(query, **arguments):
        raise RuntimeError("boom")

    harness.repository.find_unsettled_docs = fail

    with pytest.raises(RuntimeError, match="boom"):
        await harness.run()

    log.error.assert_not_called()


@pytest.mark.asyncio
async def test_applies_mapping_once_across_runs(log):
    harness = Harness({"a": doc(NOW - timedelta(hours=1))}, dry_run=False)

    await harness.run()
    await harness.run()

    assert harness.repository.mapping_calls == 1
    assert len(window_lines(log, "recent")) == 2


@pytest.mark.asyncio
async def test_dry_run_never_applies_the_mapping_and_still_scans(log):
    harness = Harness({"a": doc(NOW - timedelta(hours=1))}, dry_run=True)

    await harness.run()
    await harness.run()

    assert harness.repository.mapping_calls == 0
    assert harness.doc_type.updated_ids() == ["a"]
    assert harness.repository.writes == []


@pytest.mark.asyncio
async def test_mapping_failure_is_logged_once_skips_the_run_and_the_next_run_retries(log):
    harness = Harness({"a": doc(NOW - timedelta(hours=1))}, dry_run=False)
    harness.repository.mapping_errors = [MetricsIndexUnavailableError("es down")]

    await harness.run()

    [error] = log.error.call_args_list
    assert error.args[0].startswith("Spend backfill skipped: could not apply the ledger mapping")
    assert error.kwargs["exc_info"] is True
    assert harness.repository.searches == []

    await harness.run()

    assert harness.repository.mapping_calls == 2
    assert harness.doc_type.updated_ids() == ["a"]


def test_settings_from_config(monkeypatch):
    from codemie.configs.config import config

    monkeypatch.setattr(config, "SPEND_BACKFILL_DRY_RUN", True)
    monkeypatch.setattr(config, "SPEND_BACKFILL_MAX_AGE_HOURS", 12)
    monkeypatch.setattr(config, "SPEND_BACKFILL_BATCH", 500)
    monkeypatch.setattr(config, "SPEND_BACKFILL_TIMEOUT_SECONDS", 45)
    monkeypatch.setattr(config, "SPEND_BACKFILL_HISTORY_START", datetime(2026, 11, 19, 2, tzinfo=UTC))
    monkeypatch.setattr(config, "SPEND_BACKFILL_PROJECTS", "alpha, beta ,")

    assert SpendBackfillSettings.from_config() == SpendBackfillSettings(
        dry_run=True,
        recent_window=timedelta(hours=12),
        batch_size=500,
        timeout_seconds=45.0,
        history_start=datetime(2026, 11, 19, 2, tzinfo=UTC),
        projects=("alpha", "beta"),
    )
