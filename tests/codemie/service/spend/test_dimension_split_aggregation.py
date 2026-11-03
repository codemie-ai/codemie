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

"""The dimension-keyed read, held to the rules a wrong implementation satisfies silently.

**The defect this file exists for has exactly one detector, and it is here.** Exactly one of the five
dimensions has a ledger column, so the obvious implementation - `GROUP BY <the dimension column>` -
is *correct* for `workflow` and returns **nothing whatsoever** for assistant, conversation and
datasource, whose rows are identified only by the correlation-id set the metric store resolved. Both
halves are pinned below as compiled SQL, because a figure of zero for every assistant is exactly what
an empty table looks like and no test built only from workflow rows can tell the two apart.

The other failures pinned here each draw a plausible table: a value with no rows dropped from the
mapping makes its row *vanish* rather than read zero; a floor applied per value relabels one
whole-range absence as a thousand per-row ones; a residue read per value either names the same money
a thousand times or claims to know which bucket it belonged in; and a value absent from the id
mapping read as *no filter* rather than *no ids* silently gives it the whole table's money.

The last class is the one invariant that only exists **between** two reads: a grouped figure for one
value must equal `aggregate`'s figure for that same scope. It is pinned twice, at the ledger level
where one stub answers both grains from one row set, and at the SQL level where the two statements'
predicates are compared as compiled text - so the guarantee survives someone inlining the shared
predicate builder.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from codemie.enterprise.litellm.spend_ledger import SpendLogsLedger
from codemie.repository.spend_logs_repository import SpendLogsRepository, _union_predicate
from codemie.service.spend.coverage import LedgerCoverage, Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.default_ledger import DefaultSpendLedger
from codemie.service.spend.identity_bridge import identity_bridge_portion
from codemie.service.spend.ledger import HOLDER_BATCH_SIZE, AccessScope, DimensionScope, ReportFilter, UserTerm
from codemie.service.spend.ledger_registry import _NoopSpendLedger
from codemie.service.spend.routing import LLMCallClass
from codemie.service.spend.spend_service import SpendService

PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))
MID_PERIOD = datetime(2026, 8, 15, tzinfo=timezone.utc)

#: The two cases the union splits on, and the whole reason this file is not one test:
#: `workflow` carries a ledger column, `assistant` carries none at all.
WITH_A_COLUMN = SpendDimension.WORKFLOW
WITHOUT_A_COLUMN = SpendDimension.ASSISTANT

SERVICE = SpendService()

#: Distinguishes "the argument never arrived" from "the argument arrived as `None`". The second is a
#: read with no caller and narrows nothing on purpose; the first is a read that forgot its boundary.
_UNSET = object()


def sql(clause) -> str:
    """The clause as PostgreSQL would see it, with literals inlined so it can be asserted on."""
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).replace("\n", " ")


# ---------------------------------------------------------------------------
# The repository read, against a session that records statements rather than a database
# ---------------------------------------------------------------------------


class _CapturingSession:
    """Records each statement and answers it with a row as wide as its own column list.

    Sized from the statement rather than from a fixed list, because the chunking test issues two
    statements of different widths and a fixed-width row would fail the `strict=True` zip for a
    reason that has nothing to do with the code under test.
    """

    def __init__(self, value: Decimal = Decimal("0"), values: list | None = None) -> None:
        self._value = value
        self._values = values
        self.statements: list = []

    async def execute(self, statement):
        self.statements.append(statement)
        width = len(statement.selected_columns)
        row = tuple(self._values[:width]) if self._values is not None else tuple([self._value] * width)

        class _Result:
            def one(self_inner):
                return row

            def scalar_one(self_inner):
                """The single-scope read's own accessor, so both statements can be captured here."""
                return row[0]

        return _Result()


class TestTheGroupedPredicateIsRightForBothKindsOfDimension:
    """The F-1 trap, pinned as compiled SQL because a figure cannot show it.

    A `GROUP BY` implementation emits correct SQL for the dimension that has a column and selects no
    rows at all for the three that do not. Both predicates are asserted here, side by side.
    """

    @pytest.mark.asyncio
    async def test_a_dimension_with_a_column_selects_on_it_and_on_the_id_set(self) -> None:
        session = _CapturingSession(Decimal("3"))
        await SpendLogsRepository().sum_for_dimension_values(session, PERIOD, WITH_A_COLUMN, {"wf-1": ("req-1",)})

        rendered = sql(session.statements[0])
        assert "codemie_workflow_id IS NOT NULL AND litellm_spend_logs.codemie_workflow_id = 'wf-1'" in rendered
        # The NULL-era half: precedence gives the populated column priority, so the id set takes only
        # the rows written before the column existed.
        assert "codemie_workflow_id IS NULL AND litellm_spend_logs.codemie_request_id IN ('req-1')" in rendered

    @pytest.mark.asyncio
    async def test_a_dimension_without_a_column_carries_no_workflow_conjunct_at_all(self) -> None:
        """The half a `GROUP BY` silently fails.

        With `codemie_workflow_id IS NULL` forced on, an assistant-grouped table loses every assistant
        invocation made inside a workflow: real money, in scope, in no bucket and in no residue.
        """
        session = _CapturingSession(Decimal("3"))
        await SpendLogsRepository().sum_for_dimension_values(session, PERIOD, WITHOUT_A_COLUMN, {"asst-1": ("req-1",)})

        rendered = sql(session.statements[0])
        assert "codemie_workflow_id" not in rendered
        assert "codemie_request_id IN ('req-1')" in rendered

    @pytest.mark.asyncio
    async def test_each_value_gets_its_own_filtered_aggregate_over_one_scan(self) -> None:
        session = _CapturingSession(values=[Decimal("1"), Decimal("2")])
        totals = await SpendLogsRepository().sum_for_dimension_values(
            session, PERIOD, WITHOUT_A_COLUMN, {"asst-1": ("req-1",), "asst-2": ("req-2",)}
        )

        assert len(session.statements) == 1
        assert totals == {"asst-1": Decimal("1"), "asst-2": Decimal("2")}
        assert sql(session.statements[0]).count("FILTER (WHERE") == 2

    @pytest.mark.asyncio
    async def test_the_period_bound_is_carried_and_narrows_the_scan_to_the_chunk(self) -> None:
        """The disjunction cannot move a figure - every FILTER predicate implies it - but without it
        the statement scans rows no value in the chunk could claim."""
        session = _CapturingSession()
        await SpendLogsRepository().sum_for_dimension_values(session, PERIOD, WITHOUT_A_COLUMN, {"asst-1": ("req-1",)})

        rendered = sql(session.statements[0])
        assert "start_time >= '2026-08-01 00:00:00+00:00'" in rendered
        assert "start_time < '2026-09-01 00:00:00+00:00'" in rendered
        assert "BETWEEN" not in rendered

    @pytest.mark.asyncio
    async def test_a_value_with_no_ids_and_no_column_needs_no_column_and_is_a_measured_zero(self) -> None:
        """Identical to what `sum_for_dimension_scope` answers for the same scope without querying:
        neither branch exists, so no row can match, so the zero is measured rather than fabricated."""
        session = _CapturingSession()
        totals = await SpendLogsRepository().sum_for_dimension_values(session, PERIOD, WITHOUT_A_COLUMN, {"asst-1": ()})

        assert totals == {"asst-1": Decimal("0")}
        assert session.statements == [], "a value that can match nothing is not worth a column"

    @pytest.mark.asyncio
    async def test_a_value_with_no_ids_but_a_column_is_still_selected_on_the_column(self) -> None:
        """The asymmetry between the two kinds, from the other side: an empty id set does not make a
        workflow value unanswerable, because its own column still identifies it."""
        session = _CapturingSession(Decimal("5"))
        totals = await SpendLogsRepository().sum_for_dimension_values(session, PERIOD, WITH_A_COLUMN, {"wf-1": ()})

        assert totals == {"wf-1": Decimal("5")}
        assert "codemie_workflow_id = 'wf-1'" in sql(session.statements[0])

    @pytest.mark.asyncio
    async def test_a_value_set_past_the_batch_bound_is_split_and_not_truncated(self) -> None:
        """Splitting is the point of the bound; silently answering the first chunk is not."""
        ids_by_value = {f"wf-{index}": () for index in range(HOLDER_BATCH_SIZE + 3)}
        session = _CapturingSession(Decimal("2"))

        totals = await SpendLogsRepository().sum_for_dimension_values(session, PERIOD, WITH_A_COLUMN, ids_by_value)

        assert len(session.statements) == 2
        assert len(totals) == HOLDER_BATCH_SIZE + 3
        assert all(total == Decimal("2") for total in totals.values())

    @pytest.mark.asyncio
    async def test_no_values_issues_no_query(self) -> None:
        """`or_()` over nothing is not a predicate, and an unbounded scan is what it would compile to."""
        session = _CapturingSession()
        assert await SpendLogsRepository().sum_for_dimension_values(session, PERIOD, WITHOUT_A_COLUMN, {}) == {}
        assert session.statements == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dimension", [SpendDimension.PROJECT, SpendDimension.IDENTITY_BRIDGE])
    async def test_the_two_dimensions_not_resolved_this_way_are_refused_loudly(self, dimension) -> None:
        session = _CapturingSession()
        with pytest.raises(ValueError):
            await SpendLogsRepository().sum_for_dimension_values(session, PERIOD, dimension, {"x": ()})


# ---------------------------------------------------------------------------
# The ledger adapter
# ---------------------------------------------------------------------------


class _StubRepository:
    """Answers the two dimension reads from **one** row set, and counts them.

    Deriving both grains from the same mapping is what makes the drift test mean something: a stub
    holding two independent numbers would let it pass while the real reads disagreed.
    """

    def __init__(self, totals=None, residue=None) -> None:
        self._totals = totals or {}
        self._residue = residue or (Decimal("0"), 0)
        self.grouped_reads = 0
        self.scope_reads = 0
        self.residue_calls: list[tuple[SpendDimension, tuple[str, ...]]] = []
        self.range_seen: TimeRange | None = None
        self.ids_seen: dict[str, tuple[str, ...]] | None = None
        #: What boundary each read was narrowed by. `_UNSET` rather than `None`, because `None` is a
        #: real value the port passes to mean "no caller" and a stub that could not tell the two apart
        #: would pass a test that never threaded the argument at all.
        self.access_seen: object = _UNSET
        self.residue_access_seen: object = _UNSET
        #: What dashboard filter each read was narrowed by, `_UNSET` for the same reason.
        self.filter_seen: object = _UNSET
        self.residue_filter_seen: object = _UNSET

    async def sum_for_dimension_values(self, session, period, dimension, ids_by_value, access=None, report_filter=None):
        self.grouped_reads += 1
        self.range_seen = period
        self.ids_seen = dict(ids_by_value)
        self.access_seen = access
        self.filter_seen = report_filter
        return {value: self._totals.get(value, Decimal("0")) for value in ids_by_value}

    async def sum_for_dimension_scope(self, session, period, scope, resolved_ids=()):
        self.scope_reads += 1
        self.range_seen = period
        return self._totals.get(scope.value, Decimal("0"))

    async def dimension_residue(self, session, period, scope, resolved_ids, access=None, report_filter=None):
        self.residue_calls.append((scope.dimension, resolved_ids))
        self.residue_access_seen = access
        self.residue_filter_seen = report_filter
        return self._residue


class _StubSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def ledger(totals=None, residue=None, coverage: LedgerCoverage | None = None):
    repository = _StubRepository(totals=totals, residue=residue)
    covered = coverage or LedgerCoverage(coverage_floor=datetime(2026, 1, 1, tzinfo=timezone.utc), ready=True)
    return SpendLogsLedger(covered, session_factory=_StubSession, repository=repository), repository


class TestEveryRequestedValueIsPresent:
    @pytest.mark.asyncio
    async def test_both_kinds_of_dimension_return_their_figures(self) -> None:
        """The F-1 trap at the adapter level: a `GROUP BY` build returns the first and not the second."""
        led, _ = ledger(totals={"wf-1": Decimal("4.25")})
        by_workflow = await led.aggregate_by_dimension(PERIOD, WITH_A_COLUMN, ["wf-1"])

        led, _ = ledger(totals={"asst-1": Decimal("4.25")})
        by_assistant = await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1"])

        assert by_workflow["wf-1"].figure == Decimal("4.25")
        assert by_assistant["asst-1"].figure == Decimal("4.25")

    @pytest.mark.asyncio
    async def test_a_value_the_table_holds_nothing_for_is_a_measured_zero_and_not_absent(self) -> None:
        """A caller's table row must not vanish because its figure is zero."""
        led, _ = ledger(totals={"asst-1": Decimal("4.25")})
        result = await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1", "asst-2"])

        assert set(result) == {"asst-1", "asst-2"}
        assert result["asst-2"].figure == Decimal("0")
        assert result["asst-2"].unavailable is None, "a measured zero carries no absence"

    @pytest.mark.asyncio
    async def test_a_value_repeated_by_the_caller_yields_one_entry(self) -> None:
        led, repository = ledger(totals={"asst-1": Decimal("7")})
        result = await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1", "asst-1"])

        assert result == {"asst-1": result["asst-1"]}
        assert list(repository.ids_seen) == ["asst-1"]

    @pytest.mark.asyncio
    async def test_one_scan_answers_the_whole_table(self) -> None:
        """A thousand round trips for one table is what makes a surface acquire a cache, and a cache
        is a second source of spend truth."""
        led, repository = ledger()
        await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, [f"asst-{n}" for n in range(50)])

        assert repository.grouped_reads == 1
        assert repository.scope_reads == 0

    @pytest.mark.asyncio
    async def test_an_empty_value_set_issues_no_query_at_all(self) -> None:
        led, repository = ledger()
        result = await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, [])

        assert result == {}
        assert repository.grouped_reads == 0
        assert repository.residue_calls == [], "nothing is asked, so not even the residue is read"


class TestTheFloorIsAppliedOnceForTheWholeRange:
    @pytest.mark.asyncio
    async def test_a_rejected_range_yields_no_figure_for_every_value(self) -> None:
        below_floor = LedgerCoverage(coverage_floor=datetime(2026, 10, 1, tzinfo=timezone.utc), ready=True)
        led, repository = ledger(coverage=below_floor)
        result = await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1", "asst-2"])

        assert set(result) == {"asst-1", "asst-2"}
        assert all(entry.figure is None for entry in result.values())
        assert all(entry.unavailable == PERIOD for entry in result.values())
        assert repository.grouped_reads == 0, "a range no figure exists for must not be queried"

    @pytest.mark.asyncio
    async def test_an_unready_ledger_withholds_every_value_rather_than_reporting_zeroes(self) -> None:
        """A fabricated zero is a positive claim that this row cost nothing."""
        led, _ = ledger(coverage=LedgerCoverage(coverage_floor=PERIOD.start, ready=False))
        result = await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1"])

        assert result["asst-1"].figure is None

    @pytest.mark.asyncio
    async def test_a_sub_floor_range_carries_zeros_plus_the_whole_range_flag_on_every_value(self) -> None:
        """The flag is the whole reason the zero is honest: without it the zero reads as measured.

        Applied once for the range and never per value - a value is not a time interval, so a
        per-value flag would name an absence no floor describes.
        """
        crossing = LedgerCoverage(coverage_floor=MID_PERIOD, ready=True)
        led, repository = ledger(coverage=crossing)
        result = await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1", "asst-2"])

        assert all(entry.figure == Decimal("0") for entry in result.values())
        assert {entry.unavailable for entry in result.values()} == {TimeRange(PERIOD.start, MID_PERIOD)}
        assert repository.range_seen == TimeRange(MID_PERIOD, PERIOD.end)


class TestTheResidueIsOneFactAboutTheWholeRead:
    @pytest.mark.asyncio
    async def test_it_reaches_every_value_partial_and_is_read_once(self) -> None:
        """A property of the range and the dimension, not of one value: the unresolved rows are
        missing from *every* bucket, and any of them might belong to any value."""
        led, repository = ledger(residue=(Decimal("2.5"), 3))
        result = await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1", "asst-2", "asst-3"])

        for value, entry in result.items():
            portion = entry.partial[SpendDimension.ASSISTANT]
            assert portion.spend == Decimal("2.5"), f"{value} lost the residue"
            assert portion.rows == 3
        assert len(repository.residue_calls) == 1, "one residue for the read, not one per value"

    @pytest.mark.asyncio
    async def test_it_is_read_against_the_union_of_every_value_ids(self) -> None:
        """Reading it against one value's ids would report every other value's resolved rows as
        unresolved, so a fully covered table would render as almost entirely partial."""
        led, repository = ledger(residue=(Decimal("1"), 1))
        await led.aggregate_by_dimension(
            PERIOD,
            WITHOUT_A_COLUMN,
            ["asst-1", "asst-2"],
            {"asst-1": ("req-1", "req-2"), "asst-2": ("req-2", "req-3")},
        )

        dimension, ids = repository.residue_calls[0]
        assert dimension is WITHOUT_A_COLUMN
        # Deduped: an id resolved for two values is one row, and repeating it only bloats the NOT IN.
        assert ids == ("req-1", "req-2", "req-3")

    @pytest.mark.asyncio
    async def test_unbucketable_rows_that_cost_nothing_leave_no_label(self) -> None:
        """A failed call writes a row with no model and a spend of zero: the figure beside it is short
        by nothing, and *partial, missing $0.00* would tell a reader to distrust a whole number."""
        led, _ = ledger(residue=(Decimal("0"), 3))
        result = await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1"])

        assert not result["asst-1"].partial

    @pytest.mark.asyncio
    async def test_a_range_with_no_unresolved_rows_is_not_labelled_partial(self) -> None:
        """Labelling a range that holds no hole reports a covered table as incomplete."""
        led, _ = ledger(residue=(Decimal("0"), 0))
        result = await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1"])

        assert not result["asst-1"].partial


class TestAValueAbsentFromTheIdMappingMatchesNothingRatherThanEverything:
    @pytest.mark.asyncio
    async def test_it_is_passed_to_the_repository_as_an_empty_id_set(self) -> None:
        """The inversion this plumbing must not reintroduce: an absent entry is *no ids* - a measured
        zero whose rows the residue names - never "select everything for this value"."""
        led, repository = ledger()
        await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1", "asst-2"], {"asst-1": ("req-1",)})

        assert repository.ids_seen == {"asst-1": ("req-1",), "asst-2": ()}

    @pytest.mark.asyncio
    async def test_no_mapping_at_all_is_every_value_with_no_ids(self) -> None:
        led, repository = ledger()
        await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1", "asst-2"])

        assert repository.ids_seen == {"asst-1": (), "asst-2": ()}

    @pytest.mark.asyncio
    async def test_an_absent_columnless_value_reads_zero_and_never_the_ranges_whole_money(self) -> None:
        """Asserted through the real repository, because the inversion is a SQL-level one: with no
        predicate at all a filtered aggregate would sum every row in the range."""
        session = _CapturingSession(Decimal("999"))
        totals = await SpendLogsRepository().sum_for_dimension_values(session, PERIOD, WITHOUT_A_COLUMN, {"asst-1": ()})

        assert totals == {"asst-1": Decimal("0")}


class TestTheGroupedFigureCannotDriftFromTheSingleScopeFigure:
    """The assertion that matters most: one value read two ways gives one answer.

    A grouped figure built by any rule other than the scope read's own - a re-expressed predicate, a
    `GROUP BY` key, a residue counted per value - lands here as an inequality, which is the only
    place either number stops looking right.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dimension", [WITH_A_COLUMN, WITHOUT_A_COLUMN])
    async def test_a_single_value_grouped_read_equals_aggregate_for_that_scope(self, dimension) -> None:
        totals = {"key-1": Decimal("4.25")}
        led, _ = ledger(totals=totals, residue=(Decimal("1.5"), 2))
        grouped = (await led.aggregate_by_dimension(PERIOD, dimension, ["key-1"]))["key-1"]

        led, _ = ledger(totals=totals, residue=(Decimal("1.5"), 2))
        single = await led.aggregate(PERIOD, DimensionScope(dimension, "key-1"))

        assert grouped.figure == single.figure
        assert grouped.unavailable == single.unavailable
        assert dict(grouped.partial) == dict(single.partial)
        # Pinned as literals too, so the equality cannot be satisfied by both sides being empty.
        assert grouped.figure == Decimal("4.25")
        assert grouped.partial[dimension] == Portion(spend=Decimal("1.5"), rows=2)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dimension", [WITH_A_COLUMN, WITHOUT_A_COLUMN])
    async def test_a_crossing_range_is_admitted_identically_by_both_reads(self, dimension) -> None:
        """Both floors resolve to one interval, so neither grain sums a range the other excluded."""
        crossing = LedgerCoverage(coverage_floor=MID_PERIOD, ready=True)
        led, repository = ledger(coverage=crossing)

        await led.aggregate_by_dimension(PERIOD, dimension, ["key-1"])
        grouped_range = repository.range_seen
        await led.aggregate(PERIOD, DimensionScope(dimension, "key-1"))

        assert repository.range_seen == grouped_range == TimeRange(MID_PERIOD, PERIOD.end)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("dimension", [WITH_A_COLUMN, WITHOUT_A_COLUMN])
    async def test_the_grouped_filter_is_the_scope_reads_own_predicate(self, dimension) -> None:
        """The SQL half of the same guarantee, so it survives someone inlining `_union_predicate`.

        The ledger-level half above cannot catch the two statements diverging - one stub answers both
        - and this half cannot catch the plumbing distorting a grain. Neither pin alone is enough.
        """
        ids = ("req-1",)
        grouped_session = _CapturingSession(Decimal("1"))
        await SpendLogsRepository().sum_for_dimension_values(grouped_session, PERIOD, dimension, {"key-1": ids})

        scope_session = _CapturingSession(Decimal("1"))
        await SpendLogsRepository().sum_for_dimension_scope(
            scope_session, PERIOD, DimensionScope(dimension, "key-1"), ids
        )

        predicate = sql(_union_predicate(DimensionScope(dimension, "key-1"), ids))
        assert predicate in sql(grouped_session.statements[0])
        assert predicate in sql(scope_session.statements[0])


class TestTheAdaptersThatDoNotAnswerAGroupedRead:
    @pytest.mark.asyncio
    async def test_the_default_ledger_reports_no_figure_for_a_dimension_it_cannot_express(self) -> None:
        """Not a zero: a zero would claim each of these values cost nothing.

        `datasource` is the case, and it is a fact about the metric store rather than a missing read -
        no emission carries a datasource id, so no bucket can be built for one. The four dimensions
        that store *does* carry an attribute for are answered from it; that read is pinned in
        `test_default_ledger.py`, beside the money rule it shares with `aggregate`.
        """

        class _NeverAsked:
            async def execute_aggregation_query(self, body, **_kwargs):
                raise AssertionError("a dimension with no metric-store attribute must cost no query")

        led = DefaultSpendLedger([LLMCallClass.EMBEDDINGS], repository=_NeverAsked())
        result = await led.aggregate_by_dimension(PERIOD, SpendDimension.DATASOURCE, ["ds-1", "ds-2"])

        assert set(result) == {"ds-1", "ds-2"}
        assert all(entry.figure is None for entry in result.values())
        assert all(entry.unavailable == PERIOD for entry in result.values())

    @pytest.mark.asyncio
    async def test_the_default_ledger_answers_an_empty_request_with_an_empty_mapping(self) -> None:
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=object())
        assert await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, []) == {}

    @pytest.mark.asyncio
    async def test_the_noop_ledger_reports_no_figure_for_every_value(self) -> None:
        result = await _NoopSpendLedger().aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1", "asst-2"])

        assert set(result) == {"asst-1", "asst-2"}
        assert all(entry.figure is None for entry in result.values())


# ---------------------------------------------------------------------------
# The facade
# ---------------------------------------------------------------------------


class _StubLedger:
    def __init__(self, figure: Decimal) -> None:
        self.ledger_name = "stub"
        self._figure = figure
        self.calls = 0
        self.ids_seen: list[dict] = []
        self.access_seen: list = []
        self.filter_seen: list = []

    async def aggregate_by_dimension(
        self, period, dimension, values, ids_by_value=None, access=None, report_filter=None
    ):
        self.calls += 1
        self.ids_seen.append(ids_by_value)
        self.access_seen.append(access)
        self.filter_seen.append(report_filter)
        return {value: SpendResult(figure=self._figure) for value in values}


def registry(mapping):
    return patch("codemie.service.spend.spend_service.ledgers_by_call_class", return_value=mapping)


class TestTheFacadeCombinesPerValue:
    @pytest.mark.asyncio
    async def test_two_ledgers_answers_are_summed_value_by_value(self) -> None:
        chat = _StubLedger(Decimal("3"))
        embeddings = _StubLedger(Decimal("4"))
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            with patch.object(SpendService, "_metric_store_ids_by_value", return_value={}):
                result = await SERVICE.aggregate_by_dimension(
                    PERIOD, WITHOUT_A_COLUMN, ["asst-1", "asst-2"], access=None
                )

        assert result["asst-1"].figure == Decimal("7")
        assert set(result) == {"asst-1", "asst-2"}

    @pytest.mark.asyncio
    async def test_one_ledger_serving_both_classes_is_asked_once(self) -> None:
        """2 rather than 1 call would be the double count `_distinct_ledgers` exists to stop."""
        only = _StubLedger(Decimal("3"))
        with registry({LLMCallClass.CHAT: only, LLMCallClass.EMBEDDINGS: only}):
            with patch.object(SpendService, "_metric_store_ids_by_value", return_value={}):
                result = await SERVICE.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1"], access=None)

        assert only.calls == 1
        assert result["asst-1"].figure == Decimal("3")

    @pytest.mark.asyncio
    async def test_the_id_mapping_is_resolved_once_and_shared_with_every_ledger(self) -> None:
        chat = _StubLedger(Decimal("1"))
        embeddings = _StubLedger(Decimal("1"))
        resolved = {"asst-1": ("req-1",)}
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            with patch.object(SpendService, "_metric_store_ids_by_value", return_value=resolved) as fetch:
                await SERVICE.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1", "asst-2"], access=None)

        assert fetch.call_count == 1, "once for the whole value set, not once per value"
        assert chat.ids_seen == [resolved]
        assert embeddings.ids_seen == [resolved]

    @pytest.mark.asyncio
    async def test_a_metric_store_failure_degrades_the_table_and_never_fails_it(self) -> None:
        """The narrowed union's missing rows land in the residue, where the label names them."""
        led = _StubLedger(Decimal("2"))
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch(
                "codemie.service.spend.dimension_scope.resolve_metric_store_ids_by_value",
                side_effect=RuntimeError("elasticsearch is down"),
            ):
                result = await SERVICE.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1"], access=None)

        assert led.ids_seen == [{}]
        assert result["asst-1"].figure == Decimal("2")

    @pytest.mark.asyncio
    async def test_an_empty_value_set_resolves_nothing_and_disturbs_no_ledger(self) -> None:
        led = _StubLedger(Decimal("2"))
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch.object(SpendService, "_metric_store_ids_by_value") as fetch:
                assert await SERVICE.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, [], access=None) == {}

        assert fetch.call_count == 0
        assert led.calls == 0

    def test_the_facade_admits_no_caller_supplied_id_mapping(self) -> None:
        """A consumer able to narrow a value's selector is how two surfaces come to answer one window
        differently - the freedom this port exists to remove, so it is pinned as a signature rather
        than left to review."""
        assert list(inspect.signature(SpendService.aggregate_by_dimension).parameters) == [
            "self",
            "period",
            "dimension",
            "values",
            # The access boundary is the one argument a consumer *must* supply - see
            # `TestTheAccessBoundaryCannotBeForgotten` below for why it carries no default.
            "access",
            # What the dashboard asked about. It narrows a value's rows and never names them, so it
            # is no id mapping; `None` is an unfiltered table.
            "report_filter",
        ]

    @pytest.mark.asyncio
    async def test_a_value_no_ledger_answered_for_is_reported_unavailable_rather_than_absent(self) -> None:
        """The key set is the **requested** set: the caller named these rows, so a ledger dropping one
        cannot drop it from the table."""

        class _DropsOneValue(_StubLedger):
            async def aggregate_by_dimension(
                self, period, dimension, values, ids_by_value=None, access=None, report_filter=None
            ):
                answers = await super().aggregate_by_dimension(
                    period, dimension, values, ids_by_value, access, report_filter
                )
                return {value: entry for value, entry in answers.items() if value != "asst-2"}

        led = _DropsOneValue(Decimal("5"))
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch.object(SpendService, "_metric_store_ids_by_value", return_value={}):
                result = await SERVICE.aggregate_by_dimension(
                    PERIOD, WITHOUT_A_COLUMN, ["asst-1", "asst-2"], access=None
                )

        assert set(result) == {"asst-1", "asst-2"}
        assert result["asst-2"].figure is None
        assert result["asst-2"].unavailable == PERIOD


class TestTheDashboardFilterNarrowsTheTable:
    """RD-D1: what a dashboard asked about reaches a dimension table's figures and its residue.

    A value's ids - and a workflow's or a model's own column - select its rows in every project and for
    every person, so a table filtered to one project and read without the filter footed its rows with the
    whole window's spend: live, a table filtered to a $13.48 personal project read "(at least; $23.31
    unattributed)".
    """

    ACCESS = AccessScope(unrestricted=False, user_id="alice", plain_projects=("A",))
    ASKED = ReportFilter(users=(UserTerm("bob", frozenset({"bob@example.com"})),), projects=("B",))

    @pytest.mark.asyncio
    async def test_the_grouped_statement_carries_the_boundary_and_the_filter_beside_the_range(self) -> None:
        session = _CapturingSession()
        await SpendLogsRepository().sum_for_dimension_values(
            session, PERIOD, WITHOUT_A_COLUMN, {"asst-1": ("req-1",)}, self.ACCESS, self.ASKED
        )

        where = sql(session.statements[0]).split(" WHERE ", 1)[1]
        assert "attr_project IN ('A')" in where, "the caller's boundary"
        assert "attr_user_id = 'bob'" in where and "attr_project IN ('B')" in where, "what was asked for"
        assert "attr_principal IN ('bob@example.com')" in where, "a person is not one identifier"

    @pytest.mark.asyncio
    async def test_the_residue_statement_carries_them_too(self) -> None:
        """Narrowing the figure and not the residue labels a filtered table with every other
        project's and person's unbucketable money."""
        session = _CapturingSession(values=[Decimal("0"), 0])
        await SpendLogsRepository().dimension_residue(
            session, PERIOD, DimensionScope(WITHOUT_A_COLUMN), ("req-1",), self.ACCESS, self.ASKED
        )

        where = sql(session.statements[0]).split(" WHERE ", 1)[1]
        assert "attr_project IN ('A')" in where
        assert "attr_user_id = 'bob'" in where and "attr_project IN ('B')" in where

    @pytest.mark.asyncio
    async def test_no_filter_leaves_the_grouped_statement_as_it_was(self) -> None:
        """An unfiltered table is the whole window, and its statement must not change shape for it."""
        unfiltered, absent = _CapturingSession(), _CapturingSession()
        await SpendLogsRepository().sum_for_dimension_values(unfiltered, PERIOD, WITHOUT_A_COLUMN, {"a": ("r",)})
        await SpendLogsRepository().sum_for_dimension_values(
            absent, PERIOD, WITHOUT_A_COLUMN, {"a": ("r",)}, None, ReportFilter()
        )

        assert sql(unfiltered.statements[0]) == sql(absent.statements[0])

    @pytest.mark.asyncio
    async def test_the_ledger_hands_both_reads_the_filter_beside_the_boundary(self) -> None:
        led, repository = ledger(residue=(Decimal("1"), 1))

        await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1"], None, self.ACCESS, self.ASKED)

        assert (repository.access_seen, repository.filter_seen) == (self.ACCESS, self.ASKED)
        assert (repository.residue_access_seen, repository.residue_filter_seen) == (self.ACCESS, self.ASKED)

    @pytest.mark.asyncio
    async def test_a_filter_on_an_unbridged_person_labels_every_value_beside_the_residue(self) -> None:
        """Two absences over two row sets, as on the time-bucket chart: one entry for both would claim
        the table complete in whichever dimension it dropped."""
        led, _ = ledger(residue=(Decimal("2.5"), 3))
        asked = ReportFilter(users=(UserTerm("u1", None),))

        result = await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1", "asst-2"], None, None, asked)

        for value, entry in result.items():
            assert entry.partial[SpendDimension.IDENTITY_BRIDGE] == identity_bridge_portion(), value
            assert entry.partial[WITHOUT_A_COLUMN] == Portion(spend=Decimal("2.5"), rows=3), value

    @pytest.mark.asyncio
    async def test_a_resolved_filter_leaves_no_bridge_label(self) -> None:
        led, _ = ledger(residue=(Decimal("0"), 0))

        result = await led.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1"], None, None, self.ASKED)

        assert not result["asst-1"].partial

    @pytest.mark.asyncio
    async def test_the_facade_hands_every_ledger_the_filter(self) -> None:
        chat = _StubLedger(Decimal("1"))
        embeddings = _StubLedger(Decimal("1"))
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            with patch.object(SpendService, "_metric_store_ids_by_value", return_value={}):
                await SERVICE.aggregate_by_dimension(
                    PERIOD, WITHOUT_A_COLUMN, ["asst-1"], access=None, report_filter=self.ASKED
                )

        assert chat.filter_seen == [self.ASKED] and embeddings.filter_seen == [self.ASKED]

    @pytest.mark.asyncio
    async def test_the_facade_hands_no_filter_as_none_rather_than_omitting_it(self) -> None:
        only = _StubLedger(Decimal("1"))
        with registry({LLMCallClass.CHAT: only}):
            with patch.object(SpendService, "_metric_store_ids_by_value", return_value={}):
                await SERVICE.aggregate_by_dimension(PERIOD, WITHOUT_A_COLUMN, ["asst-1"], access=None)

        assert only.filter_seen == [None]
