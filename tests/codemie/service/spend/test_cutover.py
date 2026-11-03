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

"""Which engine answers a read, and what period each one is handed - the two facts the cutover owns.

Both are asserted on the **recorded period** rather than on the figure, because a figure agrees with a
wrong implementation far too often. A legacy engine handed the whole crossing range returns a number
that foots against nothing; a ledger handed a sub-`F` instant returns a figure *and* an `unavailable`
flag naming time the legacy half already served. Neither raises, and only one of the two - the range
each side was asked about - separates them. So every fake ledger here records the `TimeRange` it was
called with, and that recording is the subject of most of these assertions.

**The property with no error message is the one worth writing down: the two halves must tile.**
`[start, F)` beside `[F, end)` claims the row logged at exactly `F` once and only once. A closed legacy
upper bound - `[start, F]` beside `[F, end)` - claims it twice, and its mirror `[start, F)` beside
`(F, end)` drops it from both. All three produce a plausible total and none of them fails, so adjacency
and non-overlap are asserted here as their own property instead of being inferred from a sum that
happened to come out right.

The fakes are hand-written classes rather than mocks on purpose: a mock records the call, but the thing
under test is the *value* of one argument on that call, and a hand-written recorder puts that value in
the assertion where a reader can see it.

**One class here asserts a defect rather than a guarantee**, which is why it says so in its own name
and in every assertion message: `TestTheTwoHalvesMeasureTimeOnTwoDifferentClocks` pins what the cutover
currently *does* with money whose call started on one side of `F` and whose metric document was emitted
on the other. That behaviour is a known, accepted limitation with no fix available inside this unit -
see `cutover`'s module docstring - and it is written down as a test for the one reason a limitation ever
should be: so that changing it requires deleting an assertion that explains itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from operator import attrgetter

import pytest

from codemie.service.spend.coverage import (
    DEFAULT_EMISSION_LAG,
    LedgerCoverage,
    SpendDimension,
    SpendResult,
    TimeRange,
)
from codemie.service.spend.cutover import CutoverSpendLedger
from codemie.service.spend.ledger import (
    AccessScope,
    PersonTotalHolder,
    ReportFilter,
    RequestCost,
    UserTerm,
    bucket_starts,
)
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.traffic_class import TrafficClass
from tests.codemie.service.spend.periods import PERIOD

#: The rollout instant, chosen to fall **inside** the shared period so `PERIOD` is the crossing case.
FLOOR = datetime(2026, 8, 15, tzinfo=timezone.utc)

WHOLLY_BELOW = TimeRange(datetime(2026, 7, 1, tzinfo=timezone.utc), datetime(2026, 8, 1, tzinfo=timezone.utc))
#: Ends *at* `F`. Half-open, so it reaches no instant the ledger covers and is still wholly legacy.
ENDS_AT_FLOOR = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), FLOOR)
STARTS_AT_FLOOR = TimeRange(FLOOR, datetime(2026, 9, 1, tzinfo=timezone.utc))
WHOLLY_ABOVE = TimeRange(datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc))

ALICE = PersonTotalHolder(user_id="aaaaaaaa-1111-1111-1111-111111111111", aliases=frozenset({"alice"}))
BOB = PersonTotalHolder(user_id="bbbbbbbb-2222-2222-2222-222222222222", aliases=frozenset({"bob"}))
CAROL = PersonTotalHolder(user_id="cccccccc-3333-3333-3333-333333333333", aliases=frozenset({"carol"}))


#: Distinguishes "the argument never arrived" from "the argument arrived as `None`". The second is a
#: read with no caller and no filter, and narrows nothing on purpose; the first is a half that was
#: asked without one at all - a different defect, and one a `None` assertion would hide.
_UNSET = object()

ALICE_SCOPE = AccessScope(unrestricted=False, user_id="alice", plain_projects=("A",))
DASHBOARD_FILTER = ReportFilter(users=(UserTerm(user_id="alice", aliases=frozenset({"alice@example.com"})),))


def _money(amount: str) -> SpendResult:
    return SpendResult(figure=Decimal(amount))


def _utc(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=timezone.utc)


class _RecordingLedger:
    """A `SpendLedger` that answers whatever it was configured with and remembers what it was asked.

    **`periods` is the assertion target, not a debugging aid.** Every ranged method appends the range it
    received, so a test can state the rule the cutover actually has to obey - *this engine was asked
    about exactly this interval* - instead of the rule a figure can only hint at. `cost_calls` is
    separate because the id-keyed read carries no period at all, and asserting "never called" on an
    empty period list would pass for a method that was called with nothing to record.
    """

    def __init__(
        self,
        name: str,
        result: SpendResult | None = None,
        *,
        by_key: dict | None = None,
        buckets: dict | None = None,
        count: int = 0,
        cost: RequestCost | None = None,
    ) -> None:
        self.ledger_name = name
        self._result = result if result is not None else _money("0")
        self._by_key = dict(by_key or {})
        self._buckets = dict(buckets or {})
        self._count = count
        self._cost = cost or RequestCost.unavailable()
        self.periods: list[TimeRange] = []
        self.cost_calls = 0
        #: What the whole-window reads and the holder batch were handed. `_UNSET` distinguishes an argument that never
        #: arrived from one that arrived as `None`, which is a real value meaning *no caller*.
        self.access_seen: list = []
        self.filter_seen: list = []

    @property
    def period(self) -> TimeRange:
        """The single range this engine was asked about, or a failure naming how many it really got."""
        assert len(self.periods) == 1, f"{self.ledger_name} was asked {len(self.periods)} times, expected once"
        return self.periods[0]

    async def consumed_for_holder(self, holder, period, access=None):
        self.periods.append(period)
        return self._result

    async def consumed_for_holders(self, holders, period, access=_UNSET, *, report_filter=_UNSET):
        self.periods.append(period)
        self.access_seen.append(access)
        self.filter_seen.append(report_filter)
        return dict(self._by_key)

    async def aggregate(self, period, scope=None, resolved_ids=()):
        self.periods.append(period)
        return self._result

    async def aggregate_by_time_bucket(self, period, width, resolved_ids=None, access=_UNSET, report_filter=_UNSET):
        self.periods.append(period)
        self.access_seen.append(access)
        self.filter_seen.append(report_filter)
        return dict(self._buckets)

    async def aggregate_by_traffic_class(self, period, resolved_ids=None):
        self.periods.append(period)
        return dict(self._by_key)

    async def aggregate_by_spend_group(self, period, *, access=_UNSET, report_filter=_UNSET):
        self.periods.append(period)
        self.access_seen.append(access)
        self.filter_seen.append(report_filter)
        return dict(self._by_key)

    async def aggregate_by_dimension(
        self, period, dimension, values, ids_by_value=None, access=_UNSET, report_filter=_UNSET
    ):
        self.periods.append(period)
        self.access_seen.append(access)
        self.filter_seen.append(report_filter)
        return dict(self._by_key)

    async def request_count(self, period, scope=None, resolved_ids=()):
        self.periods.append(period)
        return self._count

    async def cost_for_request(self, *, run_id=None, request_id=None):
        self.cost_calls += 1
        return self._cost


def _pair(legacy_result: SpendResult | None = None, current_result: SpendResult | None = None, **kwargs):
    legacy = _RecordingLedger("legacy", legacy_result, **kwargs)
    current = _RecordingLedger("current", current_result, **kwargs)
    return legacy, current, CutoverSpendLedger(legacy, current, FLOOR)


class TestWhichEngineAnswers:
    """A period lying wholly on one side is one delegation, and the *other* engine is never touched.

    Asking both and letting the empty side contribute nothing would return the same figure, which is
    exactly why the untouched side is asserted: on the sub-`F` side the ledger would answer
    `unavailable` and the flag would survive the combine onto a total the legacy engine had covered in
    full - a complete figure presented as incomplete, from a call that never needed to be made.
    """

    @pytest.mark.parametrize("period", [WHOLLY_BELOW, ENDS_AT_FLOOR], ids=["below", "ending-at-the-floor"])
    def test_a_period_below_the_floor_is_wholly_the_legacy_engines(self, period):
        """`ENDS_AT_FLOOR` is the boundary twin: half-open, so ending *at* `F` reaches nothing above it."""
        legacy, current, cutover = _pair(_money("12"))

        result = asyncio.run(cutover.aggregate(period))

        assert legacy.period == period, "the legacy engine must be asked about the whole period, unclipped"
        assert current.periods == [], "the ledger holds nothing below F and asking it can only raise a flag"
        assert result.figure == Decimal("12")

    def test_a_period_above_the_floor_is_wholly_the_ledgers(self):
        legacy, current, cutover = _pair(current_result=_money("30"))

        result = asyncio.run(cutover.aggregate(WHOLLY_ABOVE))

        assert current.period == WHOLLY_ABOVE
        assert legacy.periods == [], "the legacy engine must not be consulted about a period the ledger covers"
        assert result.figure == Decimal("30")

    def test_the_floor_instant_itself_belongs_to_the_current_ledger(self):
        """**`F` is the first instant the ledger table covers**, so `start == F` is not a crossing at all.

        The plausible mistake is a `>` where this needs `>=`: the period would then be treated as
        crossing, the legacy engine would be handed the empty-to-inverted `[F, F)` - which `TimeRange`
        refuses outright - and a boundary read would raise instead of answering.
        """
        legacy, current, cutover = _pair(current_result=_money("21"))

        result = asyncio.run(cutover.aggregate(STARTS_AT_FLOOR))

        assert current.period == STARTS_AT_FLOOR
        assert legacy.periods == [], "a period starting exactly at F reaches no legacy time"
        assert result.figure == Decimal("21")


class TestTheSplitTilesTheRequestedPeriodExactly:
    """The no-double-count property, asserted as itself rather than through a total that came out right.

    Adjacency and non-overlap are one equality - `legacy.end == current.start` - and every way of
    getting it wrong still returns a number. So it is stated here, on the recorded ranges, beside the
    two outer bounds that make the pair cover the request and nothing more.
    """

    def test_a_crossing_period_splits_into_two_adjacent_non_overlapping_halves(self):
        legacy, current, cutover = _pair(_money("4"), _money("6"))

        asyncio.run(cutover.aggregate(PERIOD))

        assert legacy.period == TimeRange(PERIOD.start, FLOOR)
        assert current.period == TimeRange(FLOOR, PERIOD.end)
        assert legacy.period.end == current.period.start, "adjacent: no instant of the request falls in neither half"
        overlaps = legacy.period.start < current.period.end and current.period.start < legacy.period.end
        assert not overlaps, "non-overlapping: an instant claimed by both halves is money counted twice"
        assert legacy.period.start == PERIOD.start and current.period.end == PERIOD.end, "the pair covers the request"

    @pytest.mark.parametrize(
        "period", [PERIOD, STARTS_AT_FLOOR, WHOLLY_ABOVE], ids=["crossing", "starting-at-the-floor", "above"]
    )
    def test_the_current_ledger_never_receives_a_period_starting_below_the_floor(self, period):
        """**This clip is why nothing downstream changed**, so it is asserted on the call, not inferred.

        `LedgerCoverage.unavailable_flag` returns None exactly when `requested.start >= F`. Hand the
        ledger a range starting one instant earlier and it correctly raises a flag about time the legacy
        half already answered for - and `coverage.py`, `spend_service.py` and every adapter would then
        need a rule for subtracting it back off.
        """
        _, current, cutover = _pair(current_result=_money("1"))

        asyncio.run(cutover.aggregate(period))

        assert current.period.start >= FLOOR
        coverage = LedgerCoverage(coverage_floor=FLOOR, ready=True)
        assert coverage.unavailable_flag(current.period) is None, "the clipped half must raise no floor flag"


@dataclass(frozen=True)
class _Call:
    """One unit of work carrying **both** timestamps the two stores record it under.

    Two fields rather than one, because the difference between them is the entire subject of the class
    below. Nothing in production holds both on one record - `litellm_spend_logs` has `start_time` and a
    metric document has `@timestamp` - and that is exactly why neither engine can notice what the other
    one did or did not claim, and why the loss asserted below is silent.
    """

    started_at: datetime
    emitted_at: datetime
    amount: Decimal


#: Which of a call's two timestamps a store files it under. Elasticsearch has only the emission instant
#: to put in its `@timestamp` range clause; `litellm_spend_logs` has only `start_time`. Named as values
#: so the two engines below differ in nothing but this.
_EMISSION_CLOCK: Callable[[_Call], datetime] = attrgetter("emitted_at")
_START_CLOCK: Callable[[_Call], datetime] = attrgetter("started_at")


class _ClockedLedger:
    """An engine that admits calls by **one** of the two clocks, with the same half-open comparison.

    `_RecordingLedger` answers with whatever it was handed, which is right for asserting *which range*
    each engine was given and useless for asserting what each engine then finds inside it. Here the
    figure is computed - `half.start <= clock(call) < half.end`, the shape of the Elasticsearch
    `@timestamp` range in `default_ledger._query` and of the `start_time` predicate in
    `spend_logs_repository` alike - so a call landing in neither half, or in both, is produced by
    arithmetic rather than asserted into place by a fixture.

    **A half matching no call sums to a measured zero, deliberately, because both real engines do.** An
    Elasticsearch scope holding no document sums to zero by `_sum_money`'s stated rule, and a ledger
    period at or above `F` holding no row reports zero rather than absent - there is no floor left to
    raise a flag about. Answering `no_figure` here instead would hide the finding behind an unavailable
    flag that production would never raise.
    """

    def __init__(self, name: str, calls: list[_Call], *, clock: Callable[[_Call], datetime]) -> None:
        self.ledger_name = name
        self._calls = tuple(calls)
        self._clock = clock
        self.periods: list[TimeRange] = []

    async def aggregate(self, period, scope=None, resolved_ids=()):
        self.periods.append(period)
        admitted = [call.amount for call in self._calls if period.start <= self._clock(call) < period.end]
        return SpendResult(figure=sum(admitted, Decimal("0")))


class TestTheTwoHalvesMeasureTimeOnTwoDifferentClocks:
    """**Known, accepted limitation. These tests assert what the cutover does, not what it should do.**

    The class above proves the halves tile the requested period as instants. They do not tile the
    *rows*: the legacy half filters Elasticsearch on `@timestamp` - metric **emission** time - and the
    ledger half filters `litellm_spend_logs` on `start_time` - when the call **started**. A call whose
    two timestamps fall on opposite sides of `F` is therefore claimed by neither half or by both, and
    the combined figure carries no flag and no portion either way: it is short, or doubled, and exact.

    **The band is the emission lag `L` wide** - `SPEND_LEDGER_EMISSION_LAG_HOURS`, 24 hours by default
    - not one row, so the parametrisation below runs a call ten minutes below `F` and one nearly a full
    `L` below it and expects the same loss from both.

    No fix belongs in `CutoverSpendLedger`: a metric document has no `start_time`, so no query against
    the metric store can partition on the ledger's clock, and any other boundary merely moves the band.
    The mitigations are the owner's to choose and are named in `cutover`'s module docstring. What these
    tests are for is that the exposure cannot shrink, grow or be quietly declared solved without an
    assertion changing here.
    """

    def _crossing_read(self, call: _Call) -> tuple[_ClockedLedger, _ClockedLedger, SpendResult]:
        """`PERIOD` crosses `FLOOR`, so both halves run - each over the one call, each on its own clock."""
        legacy = _ClockedLedger("legacy", [call], clock=_EMISSION_CLOCK)
        current = _ClockedLedger("current", [call], clock=_START_CLOCK)
        cutover = CutoverSpendLedger(legacy, current, FLOOR)
        return legacy, current, asyncio.run(cutover.aggregate(PERIOD))

    @pytest.mark.parametrize(
        "call",
        [
            _Call(FLOOR - timedelta(minutes=10), FLOOR + timedelta(minutes=5), Decimal("0.50")),
            _Call(FLOOR - timedelta(hours=23), FLOOR + timedelta(minutes=30), Decimal("0.50")),
        ],
        ids=["ten-minutes-below-the-floor", "almost-a-whole-emission-lag-below-the-floor"],
    )
    def test_a_call_starting_before_the_floor_but_emitted_after_it_is_dropped_by_both_halves(self, call):
        """The production case, on this file's floor: started below `F`, its metric document landed above.

        This is the module docstring's worked example - `F = 1 Oct`, started `30 Sep 23:50`, emitted
        `1 Oct 00:05`, `$0.50` - rewritten against `FLOOR`. **The assertions below are the defect, not
        the requirement.** Both are asserted together on purpose: the missing figure alone could be
        argued to be a rounding of little consequence, and `is_exact` is what makes it serious - there
        is no flag, no portion and no rendering difference between this and a period in which the money
        was never spent.
        """
        assert FLOOR - DEFAULT_EMISSION_LAG <= call.started_at < FLOOR, "inside the band L bounds, by construction"

        legacy, current, result = self._crossing_read(call)

        assert legacy.periods == [TimeRange(PERIOD.start, FLOOR)], "the halves are cut correctly; the clocks are not"
        assert current.periods == [TimeRange(FLOOR, PERIOD.end)]
        assert result.figure == Decimal("0"), "KNOWN LIMITATION: 0.50 was spent inside PERIOD and neither half has it"
        assert result.is_exact, "KNOWN LIMITATION: the shortfall raises no flag and labels no dimension"

    def test_a_call_starting_after_the_floor_but_emitted_before_it_is_counted_by_both_halves(self):
        """The mirror, which needs the two writers' clocks to disagree - and says nothing when they do.

        A metric document emitted *before* the row's own `start_time` is not physically possible on one
        clock; it is what two hosts produce when the one stamping the metric runs behind the one
        stamping the ledger row, and neither store can detect it. The band is then bounded by that skew
        rather than by `L`, which makes this the rarer direction and not the safer one: the drop
        understates a total and this overstates it, both by real money, both exactly.
        """
        skewed = _Call(FLOOR + timedelta(minutes=5), FLOOR - timedelta(minutes=10), Decimal("0.50"))

        _, _, result = self._crossing_read(skewed)

        assert result.figure == Decimal("1.00"), "KNOWN LIMITATION: 0.50 spent once, claimed by both halves"
        assert result.is_exact, "KNOWN LIMITATION: a doubled figure presents exactly as a correct one"


class TestACrossingReadIsOneSeamlessTotal:
    """The headline: a period spanning the rollout reads as one figure, with nothing said about a seam.

    A caller looking at a week that contains `F` is not told that its money came from two engines, and
    the result must not carry the artefacts of having done so - neither a doubled figure nor a flag
    about a range both halves between them covered completely.
    """

    def test_both_halves_figures_are_summed_into_one_result(self):
        legacy, current, cutover = _pair(_money("4.25"), _money("6.75"))

        result = asyncio.run(cutover.aggregate(PERIOD))

        assert result.figure == Decimal("11.00")

    def test_a_fully_answered_crossing_read_carries_no_unavailable_flag(self):
        """Both halves answered, so the total is whole - a flag here would report a hole that is not there."""
        _, _, cutover = _pair(_money("4"), _money("6"))

        result = asyncio.run(cutover.aggregate(PERIOD))

        assert result.unavailable is None
        assert result.is_exact, "a summed, unflagged, unqualified figure is exact and must present as one"

    def test_a_holder_read_crosses_the_same_way(self):
        """`consumed_for_holder` is a budget read, so the same seam decides whether a limit is breached."""
        legacy, current, cutover = _pair(_money("7"), _money("3"))

        result = asyncio.run(cutover.consumed_for_holder(ALICE, PERIOD))

        assert result.figure == Decimal("10")
        assert legacy.period.end == FLOOR and current.period.start == FLOOR

    def test_request_count_sums_both_halves(self):
        """Two integers add; there is no flag or portion for a count to carry and none is invented."""
        legacy = _RecordingLedger("legacy", count=4)
        current = _RecordingLedger("current", count=6)
        cutover = CutoverSpendLedger(legacy, current, FLOOR)

        assert asyncio.run(cutover.request_count(PERIOD)) == 10
        assert legacy.period.end == FLOOR and current.period.start == FLOOR

    def test_a_count_below_the_floor_comes_from_the_legacy_engine_alone(self):
        legacy = _RecordingLedger("legacy", count=9)
        current = _RecordingLedger("current", count=99)
        cutover = CutoverSpendLedger(legacy, current, FLOOR)

        assert asyncio.run(cutover.request_count(WHOLLY_BELOW)) == 9
        assert current.periods == [], "99 would mean the ledger was counted for time it holds nothing about"


#: One shared key both engines report, plus one key each engine alone reports. The keys differ in type
#: per method - a dimension value, a traffic class, a holder - because the merge must not care.
_MAPPING_READS = [
    pytest.param(
        lambda cutover, period: cutover.aggregate_by_dimension(period, SpendDimension.PROJECT, ["shared"]),
        ("shared", "legacy-only", "current-only"),
        id="aggregate_by_dimension",
    ),
    pytest.param(
        lambda cutover, period: cutover.aggregate_by_traffic_class(period),
        (TrafficClass.CLI, TrafficClass.WORKFLOW, TrafficClass.ASSISTANT),
        id="aggregate_by_traffic_class",
    ),
    pytest.param(
        lambda cutover, period: cutover.consumed_for_holders([ALICE, BOB, CAROL], period),
        (ALICE, BOB, CAROL),
        id="consumed_for_holders",
    ),
    pytest.param(
        lambda cutover, period: cutover.aggregate_by_spend_group(period, access=None),
        (SpendGroup.TOTAL, SpendGroup.PLATFORM, SpendGroup.EMBEDDING),
        id="aggregate_by_spend_group",
    ),
]


class TestMappingReadsCombinePerKeyOverTheUnion:
    """The union rather than the intersection, and the one-sided key is the case that proves which.

    A holder who spent only before the rollout, an assistant retired the week `F` landed, a traffic
    class one engine records nothing for: intersecting would drop the row altogether and the caller's
    table would come back a row short. That is a fabricated silence arriving through the merge - the
    same defect as an absent holder key, and just as invisible, because a table with one fewer row
    still adds up.
    """

    @pytest.mark.parametrize(("read", "keys"), _MAPPING_READS)
    def test_a_shared_key_sums_and_a_one_sided_key_survives_with_its_own_figure(self, read, keys):
        shared, legacy_only, current_only = keys
        legacy = _RecordingLedger("legacy", by_key={shared: _money("2"), legacy_only: _money("7")})
        current = _RecordingLedger("current", by_key={shared: _money("3"), current_only: _money("9")})

        result = asyncio.run(read(CutoverSpendLedger(legacy, current, FLOOR), PERIOD))

        assert result[shared].figure == Decimal("5"), "a key both engines answered is one summed row"
        assert result[legacy_only].figure == Decimal("7"), "a pre-rollout-only key must not be dropped"
        assert result[current_only].figure == Decimal("9"), "a post-rollout-only key must not be dropped"
        assert set(result) == {shared, legacy_only, current_only}

    @pytest.mark.parametrize(("read", "keys"), _MAPPING_READS)
    def test_each_half_is_asked_about_its_own_range_only(self, read, keys):
        legacy = _RecordingLedger("legacy", by_key={keys[0]: _money("1")})
        current = _RecordingLedger("current", by_key={keys[0]: _money("1")})

        asyncio.run(read(CutoverSpendLedger(legacy, current, FLOOR), PERIOD))

        assert legacy.period == TimeRange(PERIOD.start, FLOOR)
        assert current.period == TimeRange(FLOOR, PERIOD.end)


class TestOneGridForABucketedRead:
    """A bucket straddling `F` must draw **one** bar, and the failure it guards is a drawing bug.

    Each half walks the range it was handed, so their bucket starts are their own. Here the legacy half
    reports the straddling bucket on the absolute day grid and the current half reports it anchored to
    its own start at midday - the shape a range-aligned width would produce - and a plain per-key merge
    would file them under two keys. The chart then draws two half-height bars where one belongs while
    every total printed beside it still foots, so nothing anywhere raises.
    """

    WIDTH = "day"
    STRADDLING_PERIOD = TimeRange(_utc(1), _utc(4))
    MIDDAY_FLOOR = _utc(2, 12)

    def _bucketed(self):
        legacy = _RecordingLedger(
            "legacy",
            buckets={(_utc(1), TrafficClass.CLI): _money("1"), (_utc(2), TrafficClass.CLI): _money("2")},
        )
        current = _RecordingLedger(
            "current",
            buckets={(_utc(2, 12), TrafficClass.CLI): _money("3"), (_utc(3, 12), TrafficClass.CLI): _money("5")},
        )
        cutover = CutoverSpendLedger(legacy, current, self.MIDDAY_FLOOR)
        return asyncio.run(cutover.aggregate_by_time_bucket(self.STRADDLING_PERIOD, self.WIDTH))

    def test_the_straddling_bucket_yields_one_row_carrying_both_halves_money(self):
        result = self._bucketed()

        assert result[(_utc(2), TrafficClass.CLI)].figure == Decimal("5"), "2 + 3 in the one bucket they belong to"
        assert (_utc(2, 12), TrafficClass.CLI) not in result, "the half's own off-grid key must not survive"

    def test_the_keys_are_the_canonical_grid_of_the_original_period(self):
        """The keys an uncut ledger would have returned, which is the standard the whole cutover is held to."""
        result = self._bucketed()

        grid = bucket_starts(self.STRADDLING_PERIOD, self.WIDTH)
        assert grid == (_utc(1), _utc(2), _utc(3))
        assert set(result) == {(start, traffic_class) for start in grid for traffic_class in TrafficClass}

    def test_buckets_only_one_half_answered_keep_that_halfs_figure(self):
        result = self._bucketed()

        assert result[(_utc(1), TrafficClass.CLI)].figure == Decimal("1")
        assert result[(_utc(3), TrafficClass.CLI)].figure == Decimal("5")

    def test_a_bucket_neither_half_answered_is_unavailable_rather_than_absent(self):
        """An absent key renders as a blank, which reads as *no data* where the state is what it is."""
        result = self._bucketed()

        empty = result[(_utc(1), TrafficClass.WORKFLOW)]
        assert empty.figure is None
        assert empty.unavailable == self.STRADDLING_PERIOD, "the flag names the period the caller asked about"


class TestAnIdLookupHasNoPeriodToRouteOn:
    """`cost_for_request` is the one read with nothing to split on, so it does not split.

    Inventing a bound - the request's own day is the obvious one - drops a retry that landed on the
    other side of it. And the legacy engine has nothing to add: its own `cost_for_request` returns
    `unavailable` by design, because on that path the cost was computed locally and is already on the
    record, so asking it only produces a second `unavailable` to reconcile against a settled figure.
    """

    def test_the_current_ledger_answers_alone(self):
        legacy = _RecordingLedger("legacy", cost=RequestCost.settled(Decimal("99")))
        current = _RecordingLedger("current", cost=RequestCost.settled(Decimal("0.42")))
        cutover = CutoverSpendLedger(legacy, current, FLOOR)

        cost = asyncio.run(cutover.cost_for_request(request_id="q-1"))

        assert cost.is_settled and cost.figure == Decimal("0.42")
        assert current.cost_calls == 1
        assert legacy.cost_calls == 0, "the legacy engine's answer is unavailable by design and adds nothing"
        assert legacy.periods == [], "no period may be invented to route an id-keyed read on"


class TestTheFloorIsRequiredAndResolved:
    """A wiring defect raises here rather than degrading, because the degradation is the silent kind.

    Delegating everything to one side flips either every pre-rollout figure to `unavailable` or every
    post-rollout figure to the legacy engine's, and both read as a data problem rather than as the
    construction bug they are. A naive floor would raise deep inside a comparison, far from the wiring
    that caused it; an offset one does something worse than raise, silently shifting the boundary by
    that offset and serving a whole day's rows from the wrong engine.
    """

    @pytest.mark.parametrize(
        "floor",
        [None, datetime(2026, 8, 15), datetime(2026, 8, 15, tzinfo=timezone(timedelta(hours=2)))],
        ids=["none", "naive", "offset"],
    )
    def test_a_floor_that_is_not_a_resolved_utc_instant_is_refused(self, floor):
        with pytest.raises(ValueError):
            CutoverSpendLedger(_RecordingLedger("legacy"), _RecordingLedger("current"), floor)


class TestTheBoundaryAndTheFilterReachBothHalves:
    """A boundary honoured by one half is not a boundary, and the failure is a disclosure.

    The two whole-window reads carry the caller's permission scope *and* the dashboard's own users and
    projects filter. Both are properties of the request rather than of a period, so a half asked
    without either answers wider than the table it feeds - and because the halves are then summed, the
    widened one lands inside a figure the narrowed one made look correct. Nothing raises, and the
    total is plausible whichever half was skipped, so the pass-through is asserted on the call.

    **`_UNSET` rather than `None` is what makes these assertions worth anything.** `None` is a real
    value on both arguments - a read with no caller, a dashboard with no filter - so a half that was
    never handed the argument at all would satisfy a `is None` assertion for the wrong reason.
    """

    def _crossing_pair(self):
        legacy = _RecordingLedger("legacy", by_key={SpendGroup.TOTAL: _money("1")}, buckets={})
        current = _RecordingLedger("current", by_key={SpendGroup.TOTAL: _money("1")}, buckets={})
        return legacy, current, CutoverSpendLedger(legacy, current, FLOOR)

    def test_a_bucketed_read_hands_both_to_both_halves(self):
        legacy, current, cutover = self._crossing_pair()

        asyncio.run(cutover.aggregate_by_time_bucket(PERIOD, "day", None, ALICE_SCOPE, DASHBOARD_FILTER))

        assert legacy.access_seen == [ALICE_SCOPE] and current.access_seen == [ALICE_SCOPE]
        assert legacy.filter_seen == [DASHBOARD_FILTER] and current.filter_seen == [DASHBOARD_FILTER]

    def test_a_spend_group_read_hands_both_to_both_halves(self):
        legacy, current, cutover = self._crossing_pair()

        asyncio.run(cutover.aggregate_by_spend_group(PERIOD, access=ALICE_SCOPE, report_filter=DASHBOARD_FILTER))

        assert legacy.access_seen == [ALICE_SCOPE] and current.access_seen == [ALICE_SCOPE]
        assert legacy.filter_seen == [DASHBOARD_FILTER] and current.filter_seen == [DASHBOARD_FILTER]

    def test_no_caller_and_no_filter_travel_as_none_rather_than_being_omitted(self):
        legacy, current, cutover = self._crossing_pair()

        asyncio.run(cutover.aggregate_by_spend_group(PERIOD))

        assert legacy.access_seen == [None] and current.access_seen == [None]
        assert legacy.filter_seen == [None] and current.filter_seen == [None]

    def test_a_wholly_delegated_read_carries_them_too(self):
        """The steady state a month after the rollout is one delegation, so it is the path that runs."""
        legacy, current, cutover = self._crossing_pair()

        asyncio.run(cutover.aggregate_by_spend_group(WHOLLY_ABOVE, access=ALICE_SCOPE, report_filter=DASHBOARD_FILTER))

        assert legacy.access_seen == [], "the legacy engine must not be consulted above the floor"
        assert current.access_seen == [ALICE_SCOPE]
        assert current.filter_seen == [DASHBOARD_FILTER]

    def test_a_holder_batch_hands_both_to_both_halves(self):
        """The users- and projects-spending tables read their rows' money through this batch, so a half
        asked without the filter puts a person's spend in every project into a one-project row."""
        legacy, current, cutover = self._crossing_pair()

        asyncio.run(cutover.consumed_for_holders([ALICE], PERIOD, ALICE_SCOPE, report_filter=DASHBOARD_FILTER))

        assert legacy.access_seen == [ALICE_SCOPE] and current.access_seen == [ALICE_SCOPE]
        assert legacy.filter_seen == [DASHBOARD_FILTER] and current.filter_seen == [DASHBOARD_FILTER]

    def test_a_budget_holder_batch_hands_both_halves_no_filter(self):
        legacy, current, cutover = self._crossing_pair()

        asyncio.run(cutover.consumed_for_holders([ALICE], PERIOD))

        assert legacy.filter_seen == [None] and current.filter_seen == [None]

    def test_a_dimension_table_hands_both_to_both_halves(self):
        """The assistant, workflow and model tables read their money through this, so a half asked
        without the filter puts a value's spend in every project into a filtered row (RD-D1)."""
        legacy, current, cutover = self._crossing_pair()

        asyncio.run(
            cutover.aggregate_by_dimension(
                PERIOD, SpendDimension.ASSISTANT, ["asst-1"], None, ALICE_SCOPE, DASHBOARD_FILTER
            )
        )

        assert legacy.access_seen == [ALICE_SCOPE] and current.access_seen == [ALICE_SCOPE]
        assert legacy.filter_seen == [DASHBOARD_FILTER] and current.filter_seen == [DASHBOARD_FILTER]
