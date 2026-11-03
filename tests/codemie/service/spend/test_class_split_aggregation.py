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

"""The two class-split reads, held to the rules a wrong implementation satisfies silently.

Both grains live here on purpose: `aggregate_by_time_bucket` (per bucket per class) and
`aggregate_by_traffic_class` (per class over the whole window). They share four rules and one
invariant that only exists *between* them - a window total must equal its own buckets summed - and
that invariant cannot be pinned from either file alone.

Every failure pinned here draws a plausible chart. A dropped empty bucket draws a gap that reads as
*no data* where the truth is *no spend*; a floor applied per bucket restates one whole-range absence
as fifty-two per-week ones; a missing residue makes an incomplete week render exactly like a
complete one; and a bucket start aligned a step off the ledger's own grouping matches none of its
keys, so every figure reads zero while the money is quietly dropped.

The whole-range class split is pinned here too, beside each bucketed sibling, because it answers the
same question at a coarser grain and obeys the same four rules. Its own failure is one the bucketed
read cannot have: a window total that does **not** equal its own buckets summed, so a figure and the
chart printed next to it disagree and neither looks wrong.

**The three-hour grain adds a second between-things invariant**, and it lives here for the same reason
the first one does. Every width's buckets come from `date_bin`, which needs an *origin* the calendar
does not supply - so the walk that decides which buckets a chart has and the grouping that
fills them must tile from **one** origin. The two are read in different modules and cannot be compared
from either alone.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from codemie.enterprise.litellm.spend_ledger import SpendLogsLedger
from codemie.repository.spend_logs_repository import SpendLogsRepository
from codemie.service.spend.coverage import LedgerCoverage, Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.default_ledger import DefaultSpendLedger
from codemie.service.spend.identity_bridge import identity_bridge_portion
from codemie.service.spend.ledger import (
    BUCKET_ORIGIN,
    BUCKET_WIDTH_3_HOURS,
    AccessScope,
    ReportFilter,
    UserTerm,
    _align,
    bucket_origin,
    bucket_starts,
    bucket_step,
)
from codemie.service.spend.ledger_registry import _NoopSpendLedger
from codemie.service.spend.routing import LLMCallClass
from codemie.service.spend.spend_service import SpendService
from codemie.service.spend.traffic_class import UNION_CLASSES, TrafficClass

#: August 2026 starts on a Saturday, which is what makes the alignment rule visible: the first week
#: bucket is the *preceding* Monday, 27 July, because that is where a week bin from the epoch's first
#: Monday files these rows.
PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))
FIRST_WEEK = datetime(2026, 7, 27, tzinfo=timezone.utc)
SECOND_WEEK = datetime(2026, 8, 3, tzinfo=timezone.utc)
WEEKS_IN_PERIOD = 6

#: The split spend chart's own window, shaped like the `last_7_days` it is drawn over: a midnight
#: seven days back to *now*, so it spans seven whole days plus the elapsed part of today - 62
#: three-hour buckets, which is the ~64 that chart draws.
THREE_HOUR_PERIOD = TimeRange(
    datetime(2026, 8, 21, tzinfo=timezone.utc), datetime(2026, 8, 28, 15, 20, tzinfo=timezone.utc)
)
BUCKETS_IN_THREE_HOUR_PERIOD = 62

#: A start deliberately **off** every three-hour boundary. `date_bin` files 07:12 under 06:00, so this
#: is the window on which a walk that began at the requested instant matches none of the ledger's keys.
UNALIGNED_PERIOD = TimeRange(
    datetime(2026, 8, 21, 7, 12, tzinfo=timezone.utc), datetime(2026, 8, 23, tzinfo=timezone.utc)
)
FIRST_UNALIGNED_BUCKET = datetime(2026, 8, 21, 6, tzinfo=timezone.utc)

SERVICE = SpendService()


class _StubRepository:
    """Answers the two reads the bucketed path makes, and counts them."""

    def __init__(self, totals=None, residue=(Decimal("0"), 0)) -> None:
        self._totals = totals or {}
        self._residue = residue
        self.bucket_reads = 0
        self.class_reads = 0
        self.residue_reads = 0
        self.residue_ids: list[tuple[str, ...]] = []
        self.residue_narrowings: list[tuple] = []
        self.access_seen = "unset"
        self.filter_seen = "unset"

    async def sum_by_time_bucket(self, session, period, width, resolved_ids=None, access=None, report_filter=None):
        self.bucket_reads += 1
        self.widths_seen = width
        self.resolved_ids_seen = resolved_ids
        self.range_seen = period
        self.access_seen = access
        self.filter_seen = report_filter
        return dict(self._totals)

    async def sum_for_traffic_classes(self, session, period, resolved_ids=None):
        """Derived from the **same rows** the bucketed read answers from.

        That is what makes the drift test mean something: one table read at two grains, with only the
        ledger's own plumbing - floors, class fill, residues - in between. A stub holding two
        independent numbers would let the test pass while the real reads disagreed.

        Every class is present, as the real read's one row of filtered aggregates always is.
        """
        self.class_reads += 1
        self.resolved_ids_seen = resolved_ids
        self.range_seen = period
        return {
            cls: sum((spend for (_, keyed), spend in self._totals.items() if keyed is cls), Decimal("0"))
            for cls in TrafficClass
        }

    async def unclassified_residue(self, session, period, resolved_ids, access=None, report_filter=None):
        """The one residue a class split has: the rows no class claims, read once for the range."""
        self.residue_reads += 1
        self.residue_ids.append(resolved_ids)
        self.residue_narrowings.append((access, report_filter))
        return self._residue


class _StubSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def ledger(totals=None, residue=(Decimal("0"), 0), coverage: LedgerCoverage | None = None):
    repository = _StubRepository(totals=totals, residue=residue)
    covered = coverage or LedgerCoverage(coverage_floor=datetime(2026, 1, 1, tzinfo=timezone.utc), ready=True)
    return SpendLogsLedger(covered, session_factory=_StubSession, repository=repository), repository


class TestEveryBucketInTheRangeIsPresent:
    @pytest.mark.asyncio
    async def test_a_table_returning_nothing_still_yields_every_bucket_as_a_measured_zero(self) -> None:
        """The defect this pins: an empty week absent from the mapping draws a gap, not a zero."""
        led, _ = ledger(totals={})
        result = await led.aggregate_by_time_bucket(PERIOD, "week")

        assert len(result) == WEEKS_IN_PERIOD * len(TrafficClass)
        assert all(entry.figure == Decimal("0") for entry in result.values())
        assert all(entry.unavailable is None for entry in result.values())

    @pytest.mark.asyncio
    async def test_the_buckets_are_walked_from_the_range_and_not_inferred_from_the_rows(self) -> None:
        """One populated week must not shrink a six-week chart to one point."""
        led, _ = ledger(totals={(SECOND_WEEK, TrafficClass.CLI): Decimal("4.25")})
        result = await led.aggregate_by_time_bucket(PERIOD, "week")

        assert {bucket for bucket, _ in result} == {
            FIRST_WEEK,
            SECOND_WEEK,
            datetime(2026, 8, 10, tzinfo=timezone.utc),
            datetime(2026, 8, 17, tzinfo=timezone.utc),
            datetime(2026, 8, 24, tzinfo=timezone.utc),
            datetime(2026, 8, 31, tzinfo=timezone.utc),
        }
        assert result[(SECOND_WEEK, TrafficClass.CLI)].figure == Decimal("4.25")
        assert result[(FIRST_WEEK, TrafficClass.CLI)].figure == Decimal("0")

    @pytest.mark.asyncio
    async def test_the_first_bucket_is_the_monday_before_the_range_starts(self) -> None:
        """A week bin files a Saturday under its own Monday, so a walk from the requested instant
        would key every bucket a partial step off the ledger's keys and match none of them."""
        led, _ = ledger(totals={})
        result = await led.aggregate_by_time_bucket(PERIOD, "week")

        assert min(bucket for bucket, _ in result) == FIRST_WEEK
        assert PERIOD.start > FIRST_WEEK

    @pytest.mark.asyncio
    async def test_one_scan_answers_the_whole_chart(self) -> None:
        """Forty-eight round trips for one picture is what makes a surface acquire a cache, and a
        cache is a second source of spend truth."""
        led, repository = ledger(totals={})
        await led.aggregate_by_time_bucket(PERIOD, "week")

        assert repository.bucket_reads == 1


class TestEveryTrafficClassIsPresentOverTheWholeRange:
    @pytest.mark.asyncio
    async def test_a_table_returning_nothing_still_yields_every_class_as_a_measured_zero(self) -> None:
        """A class absent from the mapping reads downstream as a missing series, not an empty one."""
        led, _ = ledger(totals={})
        result = await led.aggregate_by_traffic_class(PERIOD)

        assert set(result) == set(TrafficClass)
        assert all(entry.figure == Decimal("0") for entry in result.values())
        assert all(entry.unavailable is None for entry in result.values())

    @pytest.mark.asyncio
    async def test_a_class_the_table_holds_nothing_for_is_a_zero_beside_a_class_that_has_money(self) -> None:
        """The mixed case, which a mapping built only from returned rows would get half right."""
        led, _ = ledger(totals={(SECOND_WEEK, TrafficClass.CLI): Decimal("4.25")})
        result = await led.aggregate_by_traffic_class(PERIOD)

        assert result[TrafficClass.CLI].figure == Decimal("4.25")
        assert result[TrafficClass.WORKFLOW].figure == Decimal("0")
        assert result[TrafficClass.DATASOURCE].figure == Decimal("0")

    @pytest.mark.asyncio
    async def test_one_scan_answers_the_whole_split(self) -> None:
        led, repository = ledger(totals={})
        await led.aggregate_by_traffic_class(PERIOD)

        assert repository.class_reads == 1
        assert repository.bucket_reads == 0, "a window total is its own read, not a sum of the chart's"


class TestTheFloorIsAppliedOnceForTheWholeRange:
    @pytest.mark.asyncio
    async def test_a_rejected_range_yields_no_figure_for_every_bucket(self) -> None:
        below_floor = LedgerCoverage(coverage_floor=datetime(2026, 10, 1, tzinfo=timezone.utc), ready=True)
        led, repository = ledger(totals={}, coverage=below_floor)
        result = await led.aggregate_by_time_bucket(PERIOD, "week")

        assert len(result) == WEEKS_IN_PERIOD * len(TrafficClass)
        assert all(entry.figure is None for entry in result.values())
        assert all(entry.unavailable == PERIOD for entry in result.values())
        assert repository.bucket_reads == 0, "a range no figure exists for must not be queried"

    @pytest.mark.asyncio
    async def test_an_unready_ledger_withholds_every_bucket_rather_than_reporting_zeroes(self) -> None:
        """A fabricated zero is a positive claim that nothing was spent."""
        led, _ = ledger(totals={}, coverage=LedgerCoverage(coverage_floor=PERIOD.start, ready=False))
        result = await led.aggregate_by_time_bucket(PERIOD, "week")

        assert all(entry.figure is None for entry in result.values())

    @pytest.mark.asyncio
    async def test_a_range_crossing_the_floor_flags_every_bucket_with_the_range_flag(self) -> None:
        """The whole-range flag, not a per-bucket one: the absence is one fact about one request."""
        crossing = LedgerCoverage(coverage_floor=datetime(2026, 8, 15, tzinfo=timezone.utc), ready=True)
        led, repository = ledger(totals={}, coverage=crossing)
        result = await led.aggregate_by_time_bucket(PERIOD, "week")

        flags = {entry.unavailable for entry in result.values()}
        assert flags == {TimeRange(PERIOD.start, datetime(2026, 8, 15, tzinfo=timezone.utc))}
        assert repository.range_seen == TimeRange(datetime(2026, 8, 15, tzinfo=timezone.utc), PERIOD.end)

    @pytest.mark.asyncio
    async def test_a_rejected_range_yields_no_figure_for_every_class(self) -> None:
        below_floor = LedgerCoverage(coverage_floor=datetime(2026, 10, 1, tzinfo=timezone.utc), ready=True)
        led, repository = ledger(totals={}, coverage=below_floor)
        result = await led.aggregate_by_traffic_class(PERIOD)

        assert set(result) == set(TrafficClass)
        assert all(entry.figure is None for entry in result.values())
        assert all(entry.unavailable == PERIOD for entry in result.values())
        assert repository.class_reads == 0, "a range no figure exists for must not be queried"

    @pytest.mark.asyncio
    async def test_a_sub_floor_range_carries_zeros_plus_the_whole_range_flag_on_every_class(self) -> None:
        """The flag is the whole reason the zero is honest: without it the zero reads as measured.

        Applied once for the range and never per class - a class is not a time interval, so a
        per-class flag would name an absence no floor describes.
        """
        crossing = LedgerCoverage(coverage_floor=datetime(2026, 8, 15, tzinfo=timezone.utc), ready=True)
        led, repository = ledger(totals={}, coverage=crossing)
        result = await led.aggregate_by_traffic_class(PERIOD)

        assert all(entry.figure == Decimal("0") for entry in result.values())
        assert {entry.unavailable for entry in result.values()} == {
            TimeRange(PERIOD.start, datetime(2026, 8, 15, tzinfo=timezone.utc))
        }
        assert repository.range_seen == TimeRange(datetime(2026, 8, 15, tzinfo=timezone.utc), PERIOD.end)


class TestTheResidueNamesTheRowsNoClassClaims:
    """The residue's definition, which is the one thing about it that can be silently wrong.

    Filing the assistant class's *unresolved* rows here is the reading its name most invites, and it
    would name every CLI, workflow and embedding row - about three quarters of a healthy chart - as
    money missing from one series. The rows no class claims belong to no series instead, so one figure
    is read for the range and every series carries it.
    """

    @pytest.mark.asyncio
    async def test_every_class_carries_the_one_unclassified_residue(self) -> None:
        led, _ = ledger(totals={}, residue=(Decimal("2.5"), 3))
        result = await led.aggregate_by_time_bucket(PERIOD, "week")

        for cls in TrafficClass:
            portion = result[(SECOND_WEEK, cls)].partial[SpendDimension.TRAFFIC_CLASS]
            assert portion.spend == Decimal("2.5")
            assert portion.rows == 3

    @pytest.mark.asyncio
    async def test_it_is_filed_under_the_traffic_class_dimension_and_not_under_a_class_own_one(self) -> None:
        """`SpendDimension.ASSISTANT` here would say *the assistant series is short by this much*,
        which is exactly what these rows are not."""
        led, _ = ledger(totals={}, residue=(Decimal("2.5"), 3))
        result = await led.aggregate_by_time_bucket(PERIOD, "week")

        assert set(result[(SECOND_WEEK, TrafficClass.ASSISTANT)].partial) == {SpendDimension.TRAFFIC_CLASS}

    @pytest.mark.asyncio
    async def test_a_window_with_no_unclassified_rows_is_not_labelled_partial(self) -> None:
        """Labelling a period that holds no hole reports a covered week as incomplete."""
        led, _ = ledger(totals={}, residue=(Decimal("0"), 0))
        result = await led.aggregate_by_time_bucket(PERIOD, "week")

        assert all(not entry.partial for entry in result.values())

    @pytest.mark.asyncio
    async def test_unclassified_rows_that_cost_nothing_leave_no_label(self) -> None:
        """Rows no class claims are a hole only when they carry money; a residue of failed calls at
        a spend of zero takes nothing away from any series."""
        led, _ = ledger(totals={}, residue=(Decimal("0"), 4))
        result = await led.aggregate_by_time_bucket(PERIOD, "week")

        assert all(not entry.partial for entry in result.values())

    @pytest.mark.asyncio
    async def test_it_is_read_once_for_the_range_and_not_once_per_bucket_or_per_class(self) -> None:
        led, repository = ledger(totals={})
        await led.aggregate_by_time_bucket(PERIOD, "week")

        assert repository.residue_reads == 1

    @pytest.mark.asyncio
    async def test_every_key_carries_the_one_equal_residue(self) -> None:
        """Value, not identity: the combiner dedupes holes by value, so what has to hold is that
        every key names the *same* hole; that it is read once is the sibling test's claim."""
        led, _ = ledger(totals={}, residue=(Decimal("2.5"), 3))
        result = await led.aggregate_by_time_bucket(PERIOD, "week")

        portions = {entry.partial[SpendDimension.TRAFFIC_CLASS] for entry in result.values()}
        assert portions == {Portion(spend=Decimal("2.5"), rows=3)}

    @pytest.mark.asyncio
    async def test_it_is_read_against_the_assistant_classes_own_id_set(self) -> None:
        """The only class whose rows the metric store resolves; a resolved row is not unclassified."""
        led, repository = ledger(totals={})
        await led.aggregate_by_time_bucket(PERIOD, "week", {TrafficClass.ASSISTANT: ("req-a",)})

        assert repository.residue_ids == [("req-a",)]
        assert repository.resolved_ids_seen == {TrafficClass.ASSISTANT: ("req-a",)}

    @pytest.mark.asyncio
    async def test_the_whole_range_split_carries_the_same_one_residue_on_every_class(self) -> None:
        """A summary must be able to render a partial label; the alternative is silently shorting."""
        led, repository = ledger(totals={}, residue=(Decimal("2.5"), 3))
        result = await led.aggregate_by_traffic_class(PERIOD)

        assert repository.residue_reads == 1
        for cls in TrafficClass:
            assert result[cls].partial[SpendDimension.TRAFFIC_CLASS].spend == Decimal("2.5")

    @pytest.mark.asyncio
    async def test_the_datasource_class_is_native_and_carries_no_id_set_at_all(self) -> None:
        """The inversion this class's history is: while it resolved through correlation ids its set was
        empty in every window - no datasource document emits one - so it was a permanent measured zero
        over real embedding spend. It selects on `call_type` now, so an id set cannot reach it."""
        led, repository = ledger(totals={(SECOND_WEEK, TrafficClass.DATASOURCE): Decimal("12.5")})
        result = await led.aggregate_by_traffic_class(PERIOD, {TrafficClass.DATASOURCE: ("ignored",)})

        assert TrafficClass.DATASOURCE not in UNION_CLASSES
        assert repository.residue_ids == [()], "the residue is read against the assistant set alone"
        assert result[TrafficClass.DATASOURCE].figure == Decimal("12.5")

    @pytest.mark.asyncio
    async def test_the_boundary_and_the_filter_narrow_the_scan_and_the_residue_alike(self) -> None:
        """Narrowing one and not the other either publishes the magnitude of spend a caller may not
        see, or labels their figure with a hole none of their rows is in."""
        access = AccessScope(unrestricted=False, user_id="alice", plain_projects=("A",))
        asked = ReportFilter(users=(UserTerm("alice", frozenset()),), projects=("A",))
        led, repository = ledger(totals={})

        await led.aggregate_by_time_bucket(PERIOD, "week", None, access, asked)

        assert (repository.access_seen, repository.filter_seen) == (access, asked)
        assert repository.residue_narrowings == [(access, asked)]

    @pytest.mark.asyncio
    async def test_the_whole_range_split_carries_no_boundary_because_it_has_no_caller(self) -> None:
        """`None` written out at the call site, rather than an `AccessScope.everything()` that would
        read as a caller entitled to everything."""
        led, repository = ledger(totals={})
        await led.aggregate_by_traffic_class(PERIOD)

        assert repository.residue_narrowings == [(None, None)]


class TestTheBridgeHoleAFilterCanLeave:
    """A dashboard filtered to a person the bridge could not resolve, and the label that says so.

    `report_filter_predicate` renders such a term as its explicit half alone - nobody found out which
    principal-form rows are that person's - so every bucket is short by those rows, by an amount
    nobody can compute either. Unlabelled, the chart renders exactly like a complete one.
    """

    @pytest.mark.asyncio
    async def test_every_key_carries_it_beside_the_class_split_residue(self) -> None:
        """Two absences over two row sets: one entry for both would claim the window was complete in
        whichever dimension it dropped."""
        led, _ = ledger(totals={}, residue=(Decimal("2.5"), 3))
        asked = ReportFilter(users=(UserTerm("u1", None),))

        result = await led.aggregate_by_time_bucket(PERIOD, "week", None, None, asked)

        for key, entry in result.items():
            assert entry.partial[SpendDimension.IDENTITY_BRIDGE] == identity_bridge_portion(), key
            assert entry.partial[SpendDimension.TRAFFIC_CLASS] == Portion(spend=Decimal("2.5"), rows=3), key

    @pytest.mark.asyncio
    async def test_a_resolved_alias_set_leaves_no_bridge_label(self) -> None:
        """An empty alias set is an answer - this person has no fallback identities - not a failure,
        and labelling it reports a complete chart as short."""
        led, _ = ledger(totals={}, residue=(Decimal("0"), 0))
        asked = ReportFilter(users=(UserTerm("u1", frozenset()),))

        result = await led.aggregate_by_time_bucket(PERIOD, "week", None, None, asked)

        assert all(not entry.partial for entry in result.values())


class TestTheBucketScanCarriesBothNarrowings:
    """The SQL half of the test above: two conjuncts in one `WHERE`, never one per `FILTER`.

    An aggregate's `FILTER` sees only the rows the `WHERE` admitted, so both narrowings reach all four
    class columns at once - and a scan that carried neither is the disclosure this port closed, a plain
    member's own chart footed with the whole platform's spend for the same week.
    """

    @pytest.mark.asyncio
    async def test_the_statement_carries_the_boundary_and_the_filter_beside_the_range(self) -> None:
        access = AccessScope(unrestricted=False, user_id="alice", plain_projects=("A",))
        asked = ReportFilter(users=(UserTerm("bob", frozenset({"bob@example.com"})),), projects=("B",))
        captured: dict = {}

        class _SqlSession:
            async def execute(self, statement):
                captured["sql"] = str(
                    statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
                ).replace("\n", " ")

                class _R:
                    def all(self_inner):
                        return []

                return _R()

        await SpendLogsRepository().sum_by_time_bucket(_SqlSession(), PERIOD, "week", None, access, asked)

        rendered = captured["sql"]
        where = rendered.split(" WHERE ")[1].split(" GROUP BY ")[0]
        assert "start_time >=" in where
        assert "attr_project IN ('A')" in where, "the caller's boundary"
        assert "attr_user_id = 'bob'" in where and "attr_project IN ('B')" in where, "what was asked for"
        assert "attr_principal IN ('bob@example.com')" in where, "a person is not one identifier"


class TestTheWindowTotalCannotDriftFromTheChartBesideIt:
    """The assertion that matters most: one window read two ways gives one answer.

    A total built by any rule other than the ledger's own - a caller summing buckets, a second
    filter list, a residue counted once per bucket - lands here as an inequality, which is the only
    place either number stops looking right.
    """

    @pytest.mark.asyncio
    async def test_each_class_figure_equals_that_class_buckets_summed_over_the_same_range(self) -> None:
        totals = {
            (FIRST_WEEK, TrafficClass.CLI): Decimal("1.50"),
            (SECOND_WEEK, TrafficClass.CLI): Decimal("2.25"),
            (SECOND_WEEK, TrafficClass.WORKFLOW): Decimal("0.75"),
            (datetime(2026, 8, 17, tzinfo=timezone.utc), TrafficClass.ASSISTANT): Decimal("4"),
        }
        led, _ = ledger(totals=totals)
        bucketed = await led.aggregate_by_time_bucket(PERIOD, "week")
        whole_range = await led.aggregate_by_traffic_class(PERIOD)

        for cls in TrafficClass:
            summed = sum(
                (entry.figure for (_, keyed), entry in bucketed.items() if keyed is cls),
                Decimal("0"),
            )
            assert whole_range[cls].figure == summed, f"{cls.value}: the window total drifted from its own chart"

        # Pinned as a literal too, so the equality above cannot be satisfied by both sides being zero.
        assert whole_range[TrafficClass.CLI].figure == Decimal("3.75")

    @pytest.mark.asyncio
    async def test_the_two_grains_are_read_over_the_same_admitted_range(self) -> None:
        """Both floors resolve to one interval, so neither grain sums a range the other excluded."""
        crossing = LedgerCoverage(coverage_floor=datetime(2026, 8, 15, tzinfo=timezone.utc), ready=True)
        led, repository = ledger(totals={}, coverage=crossing)

        await led.aggregate_by_time_bucket(PERIOD, "week")
        bucketed_range = repository.range_seen
        await led.aggregate_by_traffic_class(PERIOD)

        assert repository.range_seen == bucketed_range


class TestTheAdaptersThatCannotSplitByTrafficClass:
    @pytest.mark.asyncio
    async def test_the_default_ledger_reports_no_figure_without_issuing_a_query(self) -> None:
        """A metric-name grouping is the plausible substitute, and it answers a different question."""

        class _NeverAsked:
            async def execute_aggregation_query(self, body, **_kwargs):
                raise AssertionError("the metric store has no traffic-class vocabulary to be asked about")

        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_NeverAsked())
        result = await led.aggregate_by_time_bucket(PERIOD, "week")

        assert len(result) == WEEKS_IN_PERIOD * len(TrafficClass)
        assert all(entry.figure is None for entry in result.values())
        assert all(entry.unavailable == PERIOD for entry in result.values())

    @pytest.mark.asyncio
    async def test_the_noop_ledger_reports_no_figure_for_every_bucket(self) -> None:
        result = await _NoopSpendLedger().aggregate_by_time_bucket(PERIOD, "week")

        assert len(result) == WEEKS_IN_PERIOD * len(TrafficClass)
        assert all(entry.figure is None for entry in result.values())

    @pytest.mark.asyncio
    async def test_the_default_ledger_reports_no_figure_per_class_without_issuing_a_query(self) -> None:
        """Dropping the time grouping does not give this store a traffic-class vocabulary."""

        class _NeverAsked:
            async def execute_aggregation_query(self, body, **_kwargs):
                raise AssertionError("the metric store has no traffic-class vocabulary to be asked about")

        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_NeverAsked())
        result = await led.aggregate_by_traffic_class(PERIOD)

        assert set(result) == set(TrafficClass)
        assert all(entry.figure is None for entry in result.values())
        assert all(entry.unavailable == PERIOD for entry in result.values())

    @pytest.mark.asyncio
    async def test_the_noop_ledger_reports_no_figure_for_every_class(self) -> None:
        result = await _NoopSpendLedger().aggregate_by_traffic_class(PERIOD)

        assert set(result) == set(TrafficClass)
        assert all(entry.figure is None for entry in result.values())


class _StubLedger:
    def __init__(self, figure: Decimal) -> None:
        self.ledger_name = "stub"
        self._figure = figure
        self.calls = 0
        self.class_calls = 0
        self.resolved_ids_seen: list[dict] = []
        self.narrowings_seen: list[tuple] = []

    async def aggregate_by_time_bucket(self, period, width, resolved_ids=None, access=None, report_filter=None):
        self.calls += 1
        self.resolved_ids_seen.append(resolved_ids)
        self.narrowings_seen.append((access, report_filter))
        return {
            (bucket, traffic_class): SpendResult(figure=self._figure)
            for bucket in (FIRST_WEEK, SECOND_WEEK)
            for traffic_class in TrafficClass
        }

    async def aggregate_by_traffic_class(self, period, resolved_ids=None):
        self.class_calls += 1
        self.resolved_ids_seen.append(resolved_ids)
        return {traffic_class: SpendResult(figure=self._figure) for traffic_class in TrafficClass}


def registry(mapping):
    return patch("codemie.service.spend.spend_service.ledgers_by_call_class", return_value=mapping)


class TestTheFacadeCombinesPerKey:
    @pytest.mark.asyncio
    async def test_two_ledgers_answers_are_summed_key_by_key(self) -> None:
        chat = _StubLedger(Decimal("3"))
        embeddings = _StubLedger(Decimal("4"))
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            with patch.object(SpendService, "_metric_store_ids", return_value=()):
                result = await SERVICE.aggregate_by_time_bucket(PERIOD, "week", access=None)

        assert result[(SECOND_WEEK, TrafficClass.CLI)].figure == Decimal("7")
        # The closed grid the facade keys on - every walked bucket for every class - and not the two
        # buckets the stubs answered for: a bucket no ledger answered is still reported, as the
        # unavailable state `combine_results` produces from no answers, never by being absent.
        assert len(result) == WEEKS_IN_PERIOD * len(TrafficClass)

    @pytest.mark.asyncio
    async def test_one_ledger_serving_both_classes_is_asked_once(self) -> None:
        """6 rather than 3 would be the double count `_distinct_ledgers` exists to stop."""
        only = _StubLedger(Decimal("3"))
        with registry({LLMCallClass.CHAT: only, LLMCallClass.EMBEDDINGS: only}):
            with patch.object(SpendService, "_metric_store_ids", return_value=()):
                result = await SERVICE.aggregate_by_time_bucket(PERIOD, "week", access=None)

        assert only.calls == 1
        assert result[(FIRST_WEEK, TrafficClass.WORKFLOW)].figure == Decimal("3")

    @pytest.mark.asyncio
    async def test_the_id_set_is_fetched_once_per_union_class_and_shared_with_every_ledger(self) -> None:
        chat = _StubLedger(Decimal("1"))
        embeddings = _StubLedger(Decimal("1"))
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            with patch.object(SpendService, "_metric_store_ids", return_value=("req-1",)) as fetch:
                await SERVICE.aggregate_by_time_bucket(PERIOD, "week", access=None)

        assert fetch.call_count == len(UNION_CLASSES), "once for the whole range, not once per bucket"
        expected = {traffic_class: ("req-1",) for traffic_class in UNION_CLASSES}
        assert chat.resolved_ids_seen == [expected]
        assert embeddings.resolved_ids_seen == [expected]

    @pytest.mark.asyncio
    async def test_the_boundary_and_the_filter_reach_every_ledger_unchanged(self) -> None:
        """One boundary for the whole chart, because a boundary is a property of the caller and not of
        an adapter - a chart narrowed for one ledger's rows and not another's is a figure nobody can
        qualify."""
        access = AccessScope(unrestricted=False, user_id="alice", plain_projects=("A",))
        asked = ReportFilter(projects=("A",))
        chat = _StubLedger(Decimal("1"))
        embeddings = _StubLedger(Decimal("1"))
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            with patch.object(SpendService, "_metric_store_ids", return_value=()):
                await SERVICE.aggregate_by_time_bucket(PERIOD, "week", access=access, report_filter=asked)

        assert chat.narrowings_seen == [(access, asked)]
        assert embeddings.narrowings_seen == [(access, asked)]

    @pytest.mark.asyncio
    async def test_a_metric_store_failure_degrades_the_chart_and_never_fails_it(self) -> None:
        """The narrowed union's missing rows land in the residue, where the label names them."""
        led = _StubLedger(Decimal("2"))
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch(
                "codemie.service.spend.dimension_scope.resolve_metric_store_ids",
                side_effect=RuntimeError("elasticsearch is down"),
            ):
                result = await SERVICE.aggregate_by_time_bucket(PERIOD, "week", access=None)

        assert led.resolved_ids_seen == [{traffic_class: () for traffic_class in UNION_CLASSES}]
        assert result[(FIRST_WEEK, TrafficClass.ASSISTANT)].figure == Decimal("2")

    @pytest.mark.asyncio
    async def test_two_ledgers_class_figures_are_summed_class_by_class(self) -> None:
        chat = _StubLedger(Decimal("3"))
        embeddings = _StubLedger(Decimal("4"))
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            with patch.object(SpendService, "_metric_store_ids", return_value=()):
                result = await SERVICE.aggregate_by_traffic_class(PERIOD)

        assert result[TrafficClass.CLI].figure == Decimal("7")
        assert set(result) == set(TrafficClass)

    @pytest.mark.asyncio
    async def test_one_ledger_serving_both_classes_is_asked_once_for_the_class_split(self) -> None:
        """8 rather than 4 would be the double count `_distinct_ledgers` exists to stop."""
        only = _StubLedger(Decimal("3"))
        with registry({LLMCallClass.CHAT: only, LLMCallClass.EMBEDDINGS: only}):
            with patch.object(SpendService, "_metric_store_ids", return_value=()):
                result = await SERVICE.aggregate_by_traffic_class(PERIOD)

        assert only.class_calls == 1
        assert result[TrafficClass.WORKFLOW].figure == Decimal("3")

    @pytest.mark.asyncio
    async def test_the_id_set_is_fetched_here_once_per_union_class(self) -> None:
        led = _StubLedger(Decimal("1"))
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch.object(SpendService, "_metric_store_ids", return_value=("req-1",)) as fetch:
                await SERVICE.aggregate_by_traffic_class(PERIOD)

        assert fetch.call_count == len(UNION_CLASSES), "once for the whole range, not once per class"
        assert led.resolved_ids_seen == [{traffic_class: ("req-1",) for traffic_class in UNION_CLASSES}]

    def test_the_facade_admits_no_caller_supplied_id_set(self) -> None:
        """A consumer able to narrow a class's selector is how two surfaces come to answer one
        window differently - the freedom this port exists to remove, so it is pinned as a signature
        rather than left to review."""
        assert list(inspect.signature(SpendService.aggregate_by_traffic_class).parameters) == ["self", "period"]

    @pytest.mark.asyncio
    async def test_a_class_no_ledger_answered_for_is_reported_unavailable_rather_than_absent(self) -> None:
        """The key set is the closed enum, so a ledger dropping a class cannot drop it from the port."""

        class _DropsOneClass(_StubLedger):
            async def aggregate_by_traffic_class(self, period, resolved_ids=None):
                answers = await super().aggregate_by_traffic_class(period, resolved_ids)
                return {cls: entry for cls, entry in answers.items() if cls is not TrafficClass.DATASOURCE}

        led = _DropsOneClass(Decimal("5"))
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch.object(SpendService, "_metric_store_ids", return_value=()):
                result = await SERVICE.aggregate_by_traffic_class(PERIOD)

        assert set(result) == set(TrafficClass)
        assert result[TrafficClass.DATASOURCE].figure is None
        assert result[TrafficClass.DATASOURCE].unavailable == PERIOD


class TestTheThreeHourGrainTheSplitChartDraws:
    """The three-hour grain, and the walk that has to agree with `date_bin` exactly.

    Its failure is the same one the week grain's alignment test pins, reached by a different route: a
    grouping and a walk keyed from different origins share no bucket at all, so every figure reads as a
    measured zero and the chart draws a flat line through real money.
    """

    @pytest.mark.asyncio
    async def test_every_bucket_in_the_window_is_present_for_every_class(self) -> None:
        """62 buckets, not the ~56 seven days alone would give: the chart's window runs from a midnight
        to *now*, so it covers seven whole days plus the part of today that has elapsed."""
        led, _ = ledger(totals={})
        result = await led.aggregate_by_time_bucket(THREE_HOUR_PERIOD, BUCKET_WIDTH_3_HOURS)

        assert len(result) == BUCKETS_IN_THREE_HOUR_PERIOD * len(TrafficClass)
        assert all(entry.figure == Decimal("0") for entry in result.values())
        assert all(entry.unavailable is None for entry in result.values())

    @pytest.mark.asyncio
    async def test_the_first_bucket_is_the_one_date_bin_produces_for_an_unaligned_start(self) -> None:
        """A window starting at 07:12 is filed under 06:00, so a walk from the requested instant would
        key every bucket 72 minutes off the ledger's own keys and match none of them."""
        led, _ = ledger(totals={})
        result = await led.aggregate_by_time_bucket(UNALIGNED_PERIOD, BUCKET_WIDTH_3_HOURS)

        assert min(bucket for bucket, _ in result) == FIRST_UNALIGNED_BUCKET
        assert UNALIGNED_PERIOD.start > FIRST_UNALIGNED_BUCKET

    @pytest.mark.asyncio
    async def test_a_populated_bucket_sits_beside_empty_ones_that_are_measured_zeros(self) -> None:
        """The mixed case: one busy three-hour slot must not shrink a two-day chart to one point."""
        busy = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
        led, _ = ledger(totals={(busy, TrafficClass.CLI): Decimal("6.25")})
        result = await led.aggregate_by_time_bucket(UNALIGNED_PERIOD, BUCKET_WIDTH_3_HOURS)

        assert result[(busy, TrafficClass.CLI)].figure == Decimal("6.25")
        assert result[(FIRST_UNALIGNED_BUCKET, TrafficClass.CLI)].figure == Decimal("0")
        assert result[(busy, TrafficClass.WORKFLOW)].figure == Decimal("0")

    @pytest.mark.asyncio
    async def test_the_ledger_is_read_over_the_three_hour_width_it_was_asked_for(self) -> None:
        """The width travels unchanged: a chart re-grained on the way down draws a different picture
        from the one its caller asked for, and nothing in the response says so."""
        led, repository = ledger(totals={})
        await led.aggregate_by_time_bucket(THREE_HOUR_PERIOD, BUCKET_WIDTH_3_HOURS)

        assert repository.widths_seen == BUCKET_WIDTH_3_HOURS

    def test_a_calendar_width_is_still_refused_rather_than_approximated(self) -> None:
        """Adding a fixed three-hour step must not open the door to a month. No fixed step tiles
        months, so an approximated one misaligns every bucket after the first and the chart shows real
        money in the wrong period instead of failing."""
        for calendar_width in ("month", "quarter", "year"):
            with pytest.raises(ValueError, match="cannot step"):
                bucket_starts(THREE_HOUR_PERIOD, calendar_width)

    def test_the_three_hour_width_steps_three_hours_and_nothing_else(self) -> None:
        assert bucket_step(BUCKET_WIDTH_3_HOURS) == timedelta(hours=3)


async def bucket_grouping(width: str) -> tuple[str, dict]:
    """The statement the repository issues for one width: its SQL, and its bound values.

    Read from a real `sum_by_time_bucket` call rather than from `time_bucket` directly, so the pins
    below cover the expression as the read actually groups and orders on it.
    """
    captured: dict = {}

    class _SqlSession:
        async def execute(self, statement):
            dialect = postgresql.dialect()
            captured["sql"] = str(statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True})).replace(
                "\n", " "
            )
            captured["params"] = statement.compile(dialect=dialect).params

            class _R:
                def all(self_inner):
                    return []

            return _R()

    await SpendLogsRepository().sum_by_time_bucket(_SqlSession(), THREE_HOUR_PERIOD, width)
    return captured["sql"], captured["params"]


class TestTheGroupingAndTheWalkTileFromOneOrigin:
    """The trap `date_bin` brings that `date_trunc` did not, pinned so it cannot come back.

    `date_trunc`'s buckets were anchored by the calendar, so no origin existed to disagree about - and
    the session's `TimeZone` decided where that calendar started, which is why no width is grouped that
    way any more. `date_bin`'s buckets are anchored by an origin instead, and **a repository read and a
    bucket walk tiling from two different origins share no key at all** - so every figure reads as a
    measured zero while real money is dropped, and nothing raises. Every width is checked, because
    every width now carries an origin that can part company from the walk's.
    """

    WIDTHS = (BUCKET_WIDTH_3_HOURS, "hour", "day", "week")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("width", WIDTHS)
    async def test_the_grouping_bins_the_window_into_exactly_the_buckets_the_walk_emitted(self, width: str) -> None:
        """The invariant itself, and it is checked against the **statement's own** stride and origin
        rather than against the constants the walk reads - a test that read those from the same place
        the walk does could not tell the two apart in the first place.

        Set equality both ways is what makes it bite. Membership alone would pass a grouping strided at
        six hours (whose bins are all walked buckets too); an origin shifted by an hour fails the
        forward direction, and a coarser stride fails the reverse.
        """
        _, params = await bucket_grouping(width)
        stride, origin = params["date_bin_1"], params["date_bin_2"]

        moment, binned = THREE_HOUR_PERIOD.start, set()
        while moment < THREE_HOUR_PERIOD.end:
            binned.add(origin + (moment - origin) // stride * stride)
            moment += timedelta(minutes=15)

        assert binned == set(bucket_starts(THREE_HOUR_PERIOD, width))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("width", WIDTHS)
    async def test_the_statement_binds_the_ports_own_stride_and_origin_and_not_its_own(self, width: str) -> None:
        """The cheaper half of the same guarantee: a second origin spelled out here - as a literal, a
        module constant of the repository's own, the range's start - is the divergence itself."""
        _, params = await bucket_grouping(width)

        assert params["date_bin_1"] == bucket_step(width)
        assert params["date_bin_2"] == bucket_origin(width)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("width", WIDTHS)
    async def test_an_unaligned_window_is_grouped_from_the_origin_and_not_from_its_own_start(self, width: str) -> None:
        """The origin is a property of the design, not of the request. Derived from the range instead,
        it would differ between the walk - handed the *requested* range - and this read, handed the
        *admitted* one, exactly when a range crosses the coverage floor."""
        captured: dict = {}

        class _SqlSession:
            async def execute(self, statement):
                captured["params"] = statement.compile(dialect=postgresql.dialect()).params

                class _R:
                    def all(self_inner):
                        return []

                return _R()

        await SpendLogsRepository().sum_by_time_bucket(_SqlSession(), UNALIGNED_PERIOD, width)

        assert captured["params"]["date_bin_2"] == bucket_origin(width)
        assert captured["params"]["date_bin_2"] != UNALIGNED_PERIOD.start

    @pytest.mark.asyncio
    @pytest.mark.parametrize("width", WIDTHS)
    async def test_every_width_groups_on_date_bin_and_none_on_date_trunc(self, width: str) -> None:
        """One width left on `date_trunc` is one width a non-UTC session can re-anchor: `date_trunc`
        over a `timestamptz` truncates in the session's zone, while the walk is pure UTC arithmetic, so
        the two would share no key and every bucket would read as a measured zero over real money."""
        sql, _ = await bucket_grouping(width)

        assert "date_bin(" in sql, width
        assert "date_trunc" not in sql, width

    def test_the_week_origin_is_a_monday(self) -> None:
        """A week binned from the epoch itself would start on a Thursday - 1 January 1970 was one - and
        silently re-anchor every historical week bucket. The first Monday of the epoch is four days on,
        and it puts each bucket on the same ISO Monday the calendar names."""
        assert bucket_origin("week").weekday() == 0
        assert bucket_origin("week") == BUCKET_ORIGIN + timedelta(days=4)

    def test_align_is_unchanged_for_every_width(self) -> None:
        """The one formula must floor exactly where the four hand-written branches it replaced floored.
        Anything else re-keys history: the figures would be right and filed under the wrong instants."""
        moment = datetime(2026, 8, 19, 13, 47, tzinfo=timezone.utc)

        assert _align(moment, "hour") == datetime(2026, 8, 19, 13, tzinfo=timezone.utc)
        assert _align(moment, "day") == datetime(2026, 8, 19, tzinfo=timezone.utc)
        assert _align(moment, "week") == datetime(2026, 8, 17, tzinfo=timezone.utc)
        assert _align(moment, BUCKET_WIDTH_3_HOURS) == datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
