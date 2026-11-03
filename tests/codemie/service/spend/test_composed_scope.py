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

"""The composed scope's two reads, held to the rules a wrong implementation satisfies silently.

**The failure a conjunction has that no single-term read has is that a dropped term is invisible.**
Every wrong answer here is *larger* than the right one and looks entirely plausible: drop the class
term from *"this person's CLI spend"* and a person's whole spend is reported as their CLI spend;
drop the project term and their spend everywhere is reported as their spend in one project; read an
absent term as a filter, or a matches-nothing term as an absent one, and the figure is either the
whole table or empty. No figure-level assertion can tell any of those from the truth, so the terms
are pinned as **compiled SQL** in `tests/codemie/repository/test_spend_logs_repository.py`, beside
the holder predicates they are composed from, and this file pins what the two reads *do* with them.

The other failures pinned here are the four standing rules the two grouped reads already obey, and
one invariant that exists only **between** reads: **a composed scope carrying exactly one term must
answer exactly what that term's own read answers.** That is what makes this an extension of the port
rather than a second source of spend truth, and it is pinned three times - once per term that has a
single-term counterpart - because each counterpart is a different method with a different label.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from codemie.enterprise.litellm.spend_ledger import SpendLogsLedger
from codemie.repository.spend_logs_repository import SpendLogsRepository, composed_predicate
from codemie.service.spend.coverage import LedgerCoverage, Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.default_ledger import DefaultSpendLedger
from codemie.service.spend.ledger import ComposedScope, DimensionScope, PersonTotalHolder, UserTerm
from codemie.service.spend.ledger_registry import _NoopSpendLedger
from codemie.service.spend.routing import LLMCallClass
from codemie.service.spend.spend_service import SpendService
from codemie.service.spend.traffic_class import UNION_CLASSES, TrafficClass, traffic_class_predicate

PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))
MID_PERIOD = datetime(2026, 8, 15, tzinfo=timezone.utc)

USER_ID = "aaaaaaaa-1111-1111-1111-111111111111"
ALIASES = frozenset({"alice", "alice@example.com"})
USER = UserTerm(USER_ID, ALIASES)
UNBRIDGED_USER = UserTerm(USER_ID, None)

WORKFLOW_TERM = DimensionScope(SpendDimension.WORKFLOW, "wf-1")
ASSISTANT_TERM = DimensionScope(SpendDimension.ASSISTANT, "asst-1")

SERVICE = SpendService()


def sql(clause) -> str:
    """The clause as PostgreSQL would see it, with literals inlined so it can be asserted on."""
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).replace("\n", " ")


# ---------------------------------------------------------------------------
# The repository reads, against a session that records statements rather than a database
# ---------------------------------------------------------------------------


class _CapturingSession:
    """Records each statement and answers it with a row as wide as its own column list."""

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
                return row[0]

        return _Result()


class TestTheComposedTotalIsOneScanOverOneConjunction:
    @pytest.mark.asyncio
    async def test_the_whole_scope_is_summed_in_a_single_statement(self) -> None:
        """Two scoped sums added would double-count every row both terms claim - filter terms
        overlap by design, unlike the dimension union's two disjoint branches."""
        session = _CapturingSession(Decimal("4.25"))
        scope = ComposedScope(user=USER, project="acme", dimension=WORKFLOW_TERM)

        total = await SpendLogsRepository().sum_for_composed_scope(session, PERIOD, scope, dimension_ids=("req-1",))

        assert total == Decimal("4.25")
        assert len(session.statements) == 1
        rendered = sql(session.statements[0])
        assert rendered.count("sum(") == 1

    @pytest.mark.asyncio
    async def test_it_carries_the_half_open_period_bound(self) -> None:
        session = _CapturingSession()
        await SpendLogsRepository().sum_for_composed_scope(session, PERIOD, ComposedScope(user=USER))

        rendered = sql(session.statements[0])
        assert "start_time >= '2026-08-01 00:00:00+00:00'" in rendered
        assert "start_time < '2026-09-01 00:00:00+00:00'" in rendered
        assert "BETWEEN" not in rendered

    @pytest.mark.asyncio
    async def test_a_scope_that_can_match_nothing_is_a_measured_zero_and_costs_no_query(self) -> None:
        """The identical answer `sum_for_dimension_scope` gives for the same scope, and it must not
        become *unconstrained*: with no predicate at all this would sum every row in the range."""
        session = _CapturingSession(Decimal("999"))
        scope = ComposedScope(user=USER, dimension=ASSISTANT_TERM)

        assert await SpendLogsRepository().sum_for_composed_scope(session, PERIOD, scope) == Decimal("0")
        assert session.statements == []


class TestTheComposedClassSplitIsOneScanOverTheClosedFilterList:
    @pytest.mark.asyncio
    async def test_every_class_gets_its_own_filtered_aggregate_over_one_scan(self) -> None:
        session = _CapturingSession(values=[Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")])

        totals = await SpendLogsRepository().sum_for_composed_scope_by_traffic_class(
            session, PERIOD, ComposedScope(user=USER)
        )

        assert len(session.statements) == 1
        assert sql(session.statements[0]).count("FILTER (WHERE") == len(TrafficClass)
        assert totals == dict(zip(TrafficClass, [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")], strict=True))

    @pytest.mark.asyncio
    async def test_its_class_filters_are_the_platform_wide_splits_own_filters(self) -> None:
        """The SQL half of the drift guarantee for the class split, so it survives someone inlining
        `_traffic_class_aggregates`: a per-person split and the platform-wide split must not come to mean
        different things by "CLI"."""
        ids = {TrafficClass.ASSISTANT: ("req-1",), TrafficClass.DATASOURCE: ("req-2",)}

        scoped_session = _CapturingSession()
        await SpendLogsRepository().sum_for_composed_scope_by_traffic_class(
            scoped_session, PERIOD, ComposedScope(user=USER), ids_by_class=ids
        )
        platform_session = _CapturingSession()
        await SpendLogsRepository().sum_for_traffic_classes(platform_session, PERIOD, ids)

        scoped, platform = sql(scoped_session.statements[0]), sql(platform_session.statements[0])
        for traffic_class in TrafficClass:
            one_class = sql(traffic_class_predicate(traffic_class, ids.get(traffic_class, ())))
            assert f"FILTER (WHERE {one_class})" in scoped, traffic_class
            assert f"FILTER (WHERE {one_class})" in platform, traffic_class

    @pytest.mark.asyncio
    async def test_the_scope_narrows_the_scan_beside_the_period(self) -> None:
        session = _CapturingSession()
        await SpendLogsRepository().sum_for_composed_scope_by_traffic_class(
            session, PERIOD, ComposedScope(user=USER, project="acme")
        )

        rendered = sql(session.statements[0])
        assert f"attr_user_id = '{USER_ID}'" in rendered
        assert "attr_project = 'acme'" in rendered

    @pytest.mark.asyncio
    async def test_a_scope_that_can_match_nothing_is_four_measured_zeros_and_costs_no_query(self) -> None:
        session = _CapturingSession(Decimal("999"))
        scope = ComposedScope(user=USER, dimension=DimensionScope(SpendDimension.CONVERSATION, "conv-1"))

        totals = await SpendLogsRepository().sum_for_composed_scope_by_traffic_class(session, PERIOD, scope)

        assert totals == dict.fromkeys(TrafficClass, Decimal("0"))
        assert session.statements == []


# ---------------------------------------------------------------------------
# The ledger adapter
# ---------------------------------------------------------------------------


class _StubRepository:
    """Answers every composed and single-term read from **one** row set, and counts them.

    Deriving each grain from the same numbers is what makes the drift tests mean something: a stub
    holding independent figures per method would let them pass while the real reads disagreed.
    """

    def __init__(self, total=Decimal("0"), by_class=None, residue=None, project_residue=None) -> None:
        self._total = total
        self._by_class = by_class or {}
        self._residue = residue or (Decimal("0"), 0)
        self._project_residue = project_residue or (Decimal("0"), 0)
        self.composed_reads = 0
        self.class_reads = 0
        self.residue_calls: list[tuple[SpendDimension, tuple[str, ...]]] = []
        self.unclassified_calls: list[tuple[tuple[str, ...], object, object]] = []
        self.project_residue_calls = 0
        self.range_seen: TimeRange | None = None
        self.ids_seen: tuple[str, ...] | None = None

    async def sum_for_composed_scope(self, session, period, scope, *, dimension_ids=(), traffic_class_ids=()):
        self.composed_reads += 1
        self.range_seen = period
        self.ids_seen = dimension_ids
        return self._total

    async def sum_for_composed_scope_by_traffic_class(
        self, session, period, scope, *, dimension_ids=(), ids_by_class=None
    ):
        self.class_reads += 1
        self.range_seen = period
        return {cls: self._by_class.get(cls, Decimal("0")) for cls in TrafficClass}

    async def sum_for_holder(self, session, holder, period, access=None):
        """The person-total read, answering from the same number the composed read does."""
        self.range_seen = period
        return self._total

    async def sum_for_dimension_scope(self, session, period, scope, resolved_ids=()):
        self.range_seen = period
        return self._total

    async def sum_for_traffic_classes(self, session, period, resolved_ids=None):
        self.range_seen = period
        return {cls: self._by_class.get(cls, Decimal("0")) for cls in TrafficClass}

    async def dimension_residue(self, session, period, scope, resolved_ids, access=None, report_filter=None):
        self.residue_calls.append((scope.dimension, resolved_ids))
        return self._residue

    async def unclassified_residue(self, session, period, resolved_ids, access=None, report_filter=None):
        """The class split's one residue - the rows no class claims - from the same numbers."""
        self.unclassified_calls.append((resolved_ids, access, report_filter))
        return self._residue

    async def project_dimension_residue(self, session, sub_range, access=None):
        self.project_residue_calls += 1
        return self._project_residue


class _StubSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def ledger(coverage: LedgerCoverage | None = None, **repository_kwargs):
    repository = _StubRepository(**repository_kwargs)
    covered = coverage or LedgerCoverage(coverage_floor=datetime(2026, 1, 1, tzinfo=timezone.utc), ready=True)
    return SpendLogsLedger(covered, session_factory=_StubSession, repository=repository), repository


class TestTheFloorIsAppliedOnceForTheWholeRange:
    @pytest.mark.asyncio
    async def test_a_rejected_range_yields_no_figure_and_no_query(self) -> None:
        below_floor = LedgerCoverage(coverage_floor=datetime(2026, 10, 1, tzinfo=timezone.utc), ready=True)
        led, repository = ledger(coverage=below_floor)

        result = await led.aggregate_composed(PERIOD, ComposedScope(user=USER))

        assert result.figure is None
        assert result.unavailable == PERIOD
        assert repository.composed_reads == 0, "a range no figure exists for must not be queried"

    @pytest.mark.asyncio
    async def test_an_unready_ledger_withholds_the_figure_rather_than_reporting_zero(self) -> None:
        led, _ = ledger(coverage=LedgerCoverage(coverage_floor=PERIOD.start, ready=False))
        assert (await led.aggregate_composed(PERIOD, ComposedScope(user=USER))).figure is None

    @pytest.mark.asyncio
    async def test_a_crossing_range_is_read_over_the_admitted_portion_and_carries_the_flag(self) -> None:
        """The flag is the whole reason a small figure is honest: without it the figure reads as a
        complete one for the period that was asked about."""
        led, repository = ledger(coverage=LedgerCoverage(coverage_floor=MID_PERIOD, ready=True))

        result = await led.aggregate_composed(PERIOD, ComposedScope(user=USER))

        assert result.unavailable == TimeRange(PERIOD.start, MID_PERIOD)
        assert repository.range_seen == TimeRange(MID_PERIOD, PERIOD.end)

    @pytest.mark.asyncio
    async def test_a_rejected_range_yields_no_figure_for_every_class_and_never_a_zero(self) -> None:
        below_floor = LedgerCoverage(coverage_floor=datetime(2026, 10, 1, tzinfo=timezone.utc), ready=True)
        led, repository = ledger(coverage=below_floor)

        result = await led.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=USER))

        assert set(result) == set(TrafficClass)
        assert all(entry.figure is None for entry in result.values())
        assert all(entry.unavailable == PERIOD for entry in result.values())
        assert repository.class_reads == 0

    @pytest.mark.asyncio
    async def test_a_sub_floor_range_carries_zeros_plus_one_range_wide_flag_on_every_class(self) -> None:
        """Applied once for the range and never per class - a class is not a time interval, so a
        per-class flag would name an absence no floor describes."""
        led, _ = ledger(coverage=LedgerCoverage(coverage_floor=MID_PERIOD, ready=True))

        result = await led.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=USER))

        assert all(entry.figure == Decimal("0") for entry in result.values())
        assert {entry.unavailable for entry in result.values()} == {TimeRange(PERIOD.start, MID_PERIOD)}


class TestEveryClassIsPresent:
    @pytest.mark.asyncio
    async def test_a_class_the_scope_holds_nothing_for_is_a_measured_zero_beside_a_class_that_does(self) -> None:
        """A class silently absent reads downstream as a missing series rather than an empty one."""
        led, _ = ledger(by_class={TrafficClass.CLI: Decimal("3.75")})

        result = await led.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=USER))

        assert set(result) == set(TrafficClass)
        assert result[TrafficClass.CLI].figure == Decimal("3.75")
        assert result[TrafficClass.WORKFLOW].figure == Decimal("0")
        assert result[TrafficClass.WORKFLOW].unavailable is None, "a measured zero carries no absence"

    @pytest.mark.asyncio
    async def test_one_scan_answers_the_whole_split(self) -> None:
        led, repository = ledger()
        await led.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=USER))

        assert repository.class_reads == 1


class TestEveryTermsQualificationReachesTheCaller:
    """AD-33: a figure that exists but is qualified must arrive **with** its qualification.

    A composed figure can be short in three ways at once over three different row sets, and one slot
    for the first one found would render the most complete of the three.
    """

    @pytest.mark.asyncio
    async def test_a_dimension_term_carries_its_own_unresolved_residue(self) -> None:
        led, repository = ledger(residue=(Decimal("2.5"), 3))

        result = await led.aggregate_composed(PERIOD, ComposedScope(dimension=ASSISTANT_TERM), dimension_ids=("req-1",))

        assert result.partial[SpendDimension.ASSISTANT] == Portion(spend=Decimal("2.5"), rows=3)
        assert repository.residue_calls == [(SpendDimension.ASSISTANT, ("req-1",))]

    @pytest.mark.asyncio
    async def test_a_range_with_no_unresolved_rows_is_not_labelled_partial(self) -> None:
        led, _ = ledger(residue=(Decimal("0"), 0))
        result = await led.aggregate_composed(PERIOD, ComposedScope(dimension=ASSISTANT_TERM), dimension_ids=("r",))

        assert not result.partial

    @pytest.mark.asyncio
    async def test_a_project_term_carries_the_identity_floors_portion(self) -> None:
        """A project term makes the figure project-keyed, so `E` labels it for the same reason it
        labels the three project holders: below the floor a principal-form row carries no project
        at all, so it is simply absent from a project-filtered figure."""
        led, repository = ledger(project_residue=(Decimal("1.25"), 4))

        result = await led.aggregate_composed(PERIOD, ComposedScope(project="acme"))

        assert result.partial[SpendDimension.PROJECT].spend == Decimal("1.25")
        assert result.partial[SpendDimension.PROJECT].rows == 4
        assert repository.project_residue_calls == 1

    @pytest.mark.asyncio
    async def test_project_less_rows_that_cost_nothing_leave_no_project_portion(self) -> None:
        led, _ = ledger(project_residue=(Decimal("0"), 4))
        result = await led.aggregate_composed(PERIOD, ComposedScope(user=USER, project="acme"))

        assert SpendDimension.PROJECT not in result.partial

    @pytest.mark.asyncio
    async def test_no_project_term_reads_no_project_portion_at_all(self) -> None:
        led, repository = ledger(project_residue=(Decimal("1.25"), 4))
        result = await led.aggregate_composed(PERIOD, ComposedScope(user=USER))

        assert SpendDimension.PROJECT not in result.partial
        assert repository.project_residue_calls == 0

    @pytest.mark.asyncio
    async def test_an_unresolvable_alias_set_is_an_uncomputable_portion_and_never_a_zero(self) -> None:
        """`aliases is None` means the person's fallback rows cannot be *identified*, so there is no
        row set to sum or count - which is why this is the one portion whose members are unknown."""
        led, _ = ledger(total=Decimal("9"))

        result = await led.aggregate_composed(PERIOD, ComposedScope(user=UNBRIDGED_USER))

        assert result.figure == Decimal("9")
        assert result.partial[SpendDimension.IDENTITY_BRIDGE] == Portion(spend=None, rows=None)

    @pytest.mark.asyncio
    async def test_a_resolved_alias_set_leaves_no_bridge_portion(self) -> None:
        led, _ = ledger()
        assert (
            SpendDimension.IDENTITY_BRIDGE
            not in (await led.aggregate_composed(PERIOD, ComposedScope(user=USER))).partial
        )

    @pytest.mark.asyncio
    async def test_three_short_terms_produce_three_portions_rather_than_one(self) -> None:
        led, _ = ledger(residue=(Decimal("2"), 2), project_residue=(Decimal("1"), 1))
        scope = ComposedScope(user=UNBRIDGED_USER, project="acme", dimension=ASSISTANT_TERM)

        result = await led.aggregate_composed(PERIOD, scope, dimension_ids=("req-1",))

        assert set(result.partial) == {
            SpendDimension.ASSISTANT,
            SpendDimension.PROJECT,
            SpendDimension.IDENTITY_BRIDGE,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("traffic_class", list(TrafficClass))
    async def test_any_class_term_carries_the_splits_one_residue(self, traffic_class) -> None:
        """There is one class-split residue - the rows *no* class claims - and it is filed under
        `TRAFFIC_CLASS`, so a term naming any series carries it, a native class no less than the
        union one. Filing a union class's unresolved rows instead would name every CLI, workflow and
        embedding row as money missing from the assistant series."""
        led, repository = ledger(residue=(Decimal("0.5"), 1))

        result = await led.aggregate_composed(
            PERIOD, ComposedScope(traffic_class=traffic_class), traffic_class_ids=("req-1",)
        )

        assert result.partial[SpendDimension.TRAFFIC_CLASS] == Portion(spend=Decimal("0.5"), rows=1)
        assert repository.unclassified_calls == [(("req-1",), None, None)]
        assert repository.residue_calls == [], "a class term reads the split's residue, not a dimension's"

    @pytest.mark.asyncio
    async def test_a_range_with_no_unclassified_rows_leaves_a_class_term_unlabelled(self) -> None:
        """A covered window reported as partial is a warning nobody can act on."""
        led, _ = ledger(residue=(Decimal("0"), 0))

        result = await led.aggregate_composed(PERIOD, ComposedScope(traffic_class=TrafficClass.CLI))

        assert not result.partial


class TestTheClassSplitCarriesBothKindsOfLabel:
    @pytest.mark.asyncio
    async def test_the_scopes_own_label_rides_on_every_class(self) -> None:
        """It qualifies the row set every class is drawn from: the same unresolvable alias set
        applies whichever class a row landed in."""
        led, _ = ledger(project_residue=(Decimal("1"), 1))

        result = await led.aggregate_composed_by_traffic_class(
            PERIOD, ComposedScope(user=UNBRIDGED_USER, project="acme")
        )

        for traffic_class, entry in result.items():
            assert SpendDimension.IDENTITY_BRIDGE in entry.partial, traffic_class
            assert SpendDimension.PROJECT in entry.partial, traffic_class

    @pytest.mark.asyncio
    async def test_every_class_carries_the_one_residue_beside_the_scopes_own_label(self) -> None:
        """The two are different absences over different row sets. Merged into one entry, a residue
        would claim the scope was complete, or a scope label would claim the split was."""
        led, _ = ledger(residue=(Decimal("2"), 2), project_residue=(Decimal("1"), 1))

        result = await led.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(project="acme"))

        for traffic_class, entry in result.items():
            assert entry.partial[SpendDimension.TRAFFIC_CLASS] == Portion(spend=Decimal("2"), rows=2), traffic_class
            assert SpendDimension.PROJECT in entry.partial, traffic_class

    @pytest.mark.asyncio
    async def test_the_residue_is_read_once_for_the_whole_split_and_not_once_per_class(self) -> None:
        """The rows it names belong to no series, so there is nothing per class to read."""
        led, repository = ledger(residue=(Decimal("2"), 2))
        await led.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=USER))

        assert len(repository.unclassified_calls) == 1

    @pytest.mark.asyncio
    async def test_the_residue_is_read_against_the_assistant_classes_id_set(self) -> None:
        """The only class the metric store resolves ids for, and a resolved row is not unclassified -
        so a read that ignored the set would name rows the assistant series already claims."""
        led, repository = ledger(residue=(Decimal("1"), 1))

        await led.aggregate_composed_by_traffic_class(
            PERIOD, ComposedScope(user=USER), ids_by_class={TrafficClass.ASSISTANT: ("req-a",)}
        )

        assert repository.unclassified_calls == [(("req-a",), None, None)]

    @pytest.mark.asyncio
    async def test_a_class_absent_from_the_id_mapping_is_read_with_no_ids_rather_than_no_filter(self) -> None:
        led, repository = ledger(residue=(Decimal("1"), 1))
        await led.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=USER))

        assert repository.unclassified_calls == [((), None, None)]


class TestAComposedScopeWithOneTermCannotDriftFromThatTermsOwnRead:
    """The assertion that matters most: one question asked two ways gives one answer.

    A term re-expressed here rather than delegated - a bare `attr_user_id`, a hand-built project
    conjunct, a second definition of a class - lands here as an inequality, which is the only place
    either number stops looking right. Each counterpart is a different method with a different
    label, so the equality is pinned once per term rather than once.
    """

    @pytest.mark.asyncio
    async def test_a_user_only_scope_equals_the_person_total_holders_own_read(self) -> None:
        led, _ = ledger(total=Decimal("4.25"))
        composed = await led.aggregate_composed(PERIOD, ComposedScope(user=UNBRIDGED_USER))

        led, _ = ledger(total=Decimal("4.25"))
        holder = await led.consumed_for_holder(PersonTotalHolder(USER_ID, None), PERIOD)

        assert composed.figure == holder.figure
        assert composed.unavailable == holder.unavailable
        assert dict(composed.partial) == dict(holder.partial)
        # Pinned as literals too, so the equality cannot be satisfied by both sides being empty.
        assert composed.figure == Decimal("4.25")
        assert composed.partial[SpendDimension.IDENTITY_BRIDGE] == Portion(spend=None, rows=None)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("term", [WORKFLOW_TERM, ASSISTANT_TERM])
    async def test_a_dimension_only_scope_equals_aggregate_for_that_scope(self, term) -> None:
        led, _ = ledger(total=Decimal("4.25"), residue=(Decimal("1.5"), 2))
        composed = await led.aggregate_composed(PERIOD, ComposedScope(dimension=term), dimension_ids=("req-1",))

        led, _ = ledger(total=Decimal("4.25"), residue=(Decimal("1.5"), 2))
        single = await led.aggregate(PERIOD, term, ("req-1",))

        assert composed.figure == single.figure
        assert composed.unavailable == single.unavailable
        assert dict(composed.partial) == dict(single.partial)
        assert composed.figure == Decimal("4.25")
        assert composed.partial[term.dimension] == Portion(spend=Decimal("1.5"), rows=2)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("traffic_class", list(TrafficClass))
    async def test_a_class_only_scope_equals_the_platform_wide_split_for_that_class(self, traffic_class) -> None:
        by_class = {cls: Decimal("2") + index for index, cls in enumerate(TrafficClass)}
        ids = ("req-1",) if traffic_class in UNION_CLASSES else ()
        # The stub is told the two reads see the same rows for this class, so any difference below is
        # the two methods' own plumbing rather than the fixture's.
        same_rows = {"total": by_class[traffic_class], "by_class": by_class, "residue": (Decimal("1.5"), 2)}

        led, _ = ledger(**same_rows)
        composed = await led.aggregate_composed(
            PERIOD, ComposedScope(traffic_class=traffic_class), traffic_class_ids=ids
        )

        led, _ = ledger(**same_rows)
        split = (await led.aggregate_by_traffic_class(PERIOD, {traffic_class: ids}))[traffic_class]

        assert composed.figure == split.figure
        assert dict(composed.partial) == dict(split.partial)
        assert composed.unavailable == split.unavailable
        # Pinned as a literal too, so the equality cannot be satisfied by both sides being zero.
        assert composed.figure == by_class[traffic_class]

    @pytest.mark.asyncio
    async def test_both_grains_are_read_over_the_same_admitted_range(self) -> None:
        """Both floors resolve to one interval, so neither grain sums a range the other excluded."""
        led, repository = ledger(coverage=LedgerCoverage(coverage_floor=MID_PERIOD, ready=True))

        await led.aggregate_composed(PERIOD, ComposedScope(user=USER))
        total_range = repository.range_seen
        await led.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=USER))

        assert repository.range_seen == total_range == TimeRange(MID_PERIOD, PERIOD.end)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("term", [WORKFLOW_TERM, ASSISTANT_TERM])
    async def test_the_composed_filter_is_the_scope_reads_own_predicate(self, term) -> None:
        """The SQL half of the same guarantee, so it survives someone inlining `_union_predicate`.

        The ledger-level half above cannot catch the two statements diverging - one stub answers both
        - and this half cannot catch the plumbing distorting a grain. Neither pin alone is enough.
        """
        ids = ("req-1",)
        composed_session = _CapturingSession(Decimal("1"))
        await SpendLogsRepository().sum_for_composed_scope(
            composed_session, PERIOD, ComposedScope(dimension=term), dimension_ids=ids
        )

        scope_session = _CapturingSession(Decimal("1"))
        await SpendLogsRepository().sum_for_dimension_scope(scope_session, PERIOD, term, ids)

        predicate = sql(composed_predicate(ComposedScope(dimension=term), dimension_ids=ids))
        assert predicate in sql(composed_session.statements[0])
        assert predicate in sql(scope_session.statements[0])


# ---------------------------------------------------------------------------
# The adapters that answer some of a composed scope, or none of it
# ---------------------------------------------------------------------------


class _NeverAsked:
    async def execute_aggregation_query(self, body, **_kwargs):
        raise AssertionError("a term this store cannot express must cost no query")


class _RecordingStore:
    def __init__(self, value: float = 7.5) -> None:
        self.bodies: list[dict] = []
        self._value = value

    async def execute_aggregation_query(self, body, **_kwargs):
        self.bodies.append(body)
        return {"aggregations": {"money": {"value": self._value}}}


class TestTheDefaultLedgerAnswersOnlyAScopeItCanExpressWholly:
    """A conjunction answered with the subset of terms this store holds is a **wider** figure, not a
    narrower one, and nothing marks it. So a term it cannot express means no figure."""

    @pytest.mark.asyncio
    async def test_a_user_term_is_answered_by_the_person_total_filters(self) -> None:
        store = _RecordingStore()
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=store)

        result = await led.aggregate_composed(PERIOD, ComposedScope(user=USER))

        assert result.figure == Decimal("7.5")
        assert store.bodies[0]["query"]["bool"]["filter"][-1] == {
            "bool": {
                "should": [
                    {"term": {"attributes.user_id.keyword": USER_ID}},
                    {"terms": {"attributes.user_email.keyword": sorted(ALIASES)}},
                ],
                "minimum_should_match": 1,
            }
        }

    @pytest.mark.asyncio
    async def test_a_user_only_scope_equals_this_adapters_own_person_total_figure(self) -> None:
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_RecordingStore())
        composed = await led.aggregate_composed(PERIOD, ComposedScope(user=USER))
        holder = await led.consumed_for_holder(PersonTotalHolder(USER_ID, ALIASES), PERIOD)

        assert composed.figure == holder.figure

    @pytest.mark.asyncio
    async def test_a_dimension_term_with_an_attribute_is_expressed_on_it(self) -> None:
        store = _RecordingStore()
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=store)

        await led.aggregate_composed(PERIOD, ComposedScope(dimension=ASSISTANT_TERM))

        assert {"term": {"attributes.assistant_id.keyword": "asst-1"}} in store.bodies[0]["query"]["bool"]["filter"]

    @pytest.mark.asyncio
    async def test_a_grouped_dimension_term_narrows_by_exists_rather_than_by_nothing(self) -> None:
        """In a conjunction an omitted term widens the figure: *"this person's assistant spend"*
        answered without it is this person's whole spend."""
        store = _RecordingStore()
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=store)

        await led.aggregate_composed(
            PERIOD, ComposedScope(user=USER, dimension=DimensionScope(SpendDimension.ASSISTANT, None))
        )

        assert {"exists": {"field": "attributes.assistant_id.keyword"}} in store.bodies[0]["query"]["bool"]["filter"]

    @pytest.mark.asyncio
    async def test_an_unresolvable_alias_set_is_still_labelled_here(self) -> None:
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_RecordingStore())
        result = await led.aggregate_composed(PERIOD, ComposedScope(user=UNBRIDGED_USER))

        assert result.partial[SpendDimension.IDENTITY_BRIDGE] == Portion(spend=None, rows=None)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "scope",
        [
            ComposedScope(traffic_class=TrafficClass.CLI),
            ComposedScope(user=USER, traffic_class=TrafficClass.CLI),
            ComposedScope(project="acme"),
            ComposedScope(user=USER, project="acme"),
            ComposedScope(dimension=DimensionScope(SpendDimension.DATASOURCE, "ds-1")),
        ],
    )
    async def test_an_inexpressible_term_yields_no_figure_and_costs_no_query(self, scope) -> None:
        """Never a zero, and never a figure built from the remaining terms: dropping the class term
        from a CLI question reports the person's whole spend as their CLI spend."""
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_NeverAsked())
        result = await led.aggregate_composed(PERIOD, scope)

        assert result.figure is None
        assert result.unavailable == PERIOD

    @pytest.mark.asyncio
    async def test_the_class_split_reports_no_figure_for_every_class_without_a_query(self) -> None:
        """Narrowing the rows does not give this store a traffic-class vocabulary."""
        led = DefaultSpendLedger([LLMCallClass.CHAT], repository=_NeverAsked())
        result = await led.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=USER))

        assert set(result) == set(TrafficClass)
        assert all(entry.figure is None for entry in result.values())


class TestTheNoopLedgerReportsUnavailableRatherThanZero:
    @pytest.mark.asyncio
    async def test_the_total(self) -> None:
        assert (await _NoopSpendLedger().aggregate_composed(PERIOD, ComposedScope(user=USER))).figure is None

    @pytest.mark.asyncio
    async def test_every_class(self) -> None:
        result = await _NoopSpendLedger().aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=USER))

        assert set(result) == set(TrafficClass)
        assert all(entry.figure is None for entry in result.values())


# ---------------------------------------------------------------------------
# The facade
# ---------------------------------------------------------------------------


class _StubLedger:
    def __init__(self, figure: Decimal) -> None:
        self.ledger_name = "stub"
        self._figure = figure
        self.calls = 0
        self.dimension_ids_seen: list[tuple[str, ...]] = []
        self.class_ids_seen: list[tuple[str, ...]] = []
        self.ids_by_class_seen: list[dict] = []

    async def aggregate_composed(self, period, scope, *, dimension_ids=(), traffic_class_ids=()):
        self.calls += 1
        self.dimension_ids_seen.append(dimension_ids)
        self.class_ids_seen.append(traffic_class_ids)
        return SpendResult(figure=self._figure)

    async def aggregate_composed_by_traffic_class(self, period, scope, *, dimension_ids=(), ids_by_class=None):
        self.calls += 1
        self.dimension_ids_seen.append(dimension_ids)
        self.ids_by_class_seen.append(ids_by_class)
        return {cls: SpendResult(figure=self._figure) for cls in TrafficClass}


def registry(mapping):
    return patch("codemie.service.spend.spend_service.ledgers_by_call_class", return_value=mapping)


class TestTheFacadeResolvesTheIdSetsItselfAndCombinesOnce:
    def test_neither_composed_method_admits_a_caller_supplied_id_set(self) -> None:
        """A consumer able to narrow a term's selector is how two surfaces come to answer one window
        differently - the freedom this port exists to remove, so it is pinned as a signature."""
        assert list(inspect.signature(SpendService.aggregate_composed).parameters) == ["self", "period", "scope"]
        assert list(inspect.signature(SpendService.aggregate_composed_by_traffic_class).parameters) == [
            "self",
            "period",
            "scope",
        ]

    @pytest.mark.asyncio
    async def test_two_ledgers_answers_are_summed(self) -> None:
        chat, embeddings = _StubLedger(Decimal("3")), _StubLedger(Decimal("4"))
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            with patch.object(SpendService, "_metric_store_ids", return_value=()):
                result = await SERVICE.aggregate_composed(PERIOD, ComposedScope(user=USER))

        assert result.figure == Decimal("7")

    @pytest.mark.asyncio
    async def test_one_ledger_serving_both_classes_is_asked_once(self) -> None:
        """2 rather than 1 call would be the double count `_distinct_ledgers` exists to stop."""
        only = _StubLedger(Decimal("3"))
        with registry({LLMCallClass.CHAT: only, LLMCallClass.EMBEDDINGS: only}):
            with patch.object(SpendService, "_metric_store_ids", return_value=()):
                result = await SERVICE.aggregate_composed(PERIOD, ComposedScope(user=USER))

        assert only.calls == 1
        assert result.figure == Decimal("3")

    @pytest.mark.asyncio
    async def test_a_scope_with_neither_a_dimension_nor_a_class_term_resolves_no_ids_at_all(self) -> None:
        """The metric store is not consulted for terms that need nothing from it."""
        led = _StubLedger(Decimal("1"))
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch.object(SpendService, "_metric_store_ids") as resolve:
                await SERVICE.aggregate_composed(PERIOD, ComposedScope(user=USER))

        assert resolve.call_count == 0
        assert led.dimension_ids_seen == [()]
        assert led.class_ids_seen == [()]

    @pytest.mark.asyncio
    async def test_a_dimension_term_is_resolved_once_and_shared_with_every_ledger(self) -> None:
        chat, embeddings = _StubLedger(Decimal("1")), _StubLedger(Decimal("1"))
        with registry({LLMCallClass.CHAT: chat, LLMCallClass.EMBEDDINGS: embeddings}):
            with patch.object(SpendService, "_metric_store_ids", return_value=("req-1",)) as resolve:
                await SERVICE.aggregate_composed(PERIOD, ComposedScope(dimension=ASSISTANT_TERM))

        assert resolve.call_count == 1, "once for the scope, not once per ledger"
        assert chat.dimension_ids_seen == embeddings.dimension_ids_seen == [("req-1",)]

    @pytest.mark.asyncio
    async def test_a_union_class_term_is_resolved_as_the_whole_class_scope(self) -> None:
        """The class's ids answer *which rows belong to this class at all* - a different question
        from a dimension scope's ids, which is why the two travel separately."""
        led = _StubLedger(Decimal("1"))
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch.object(SpendService, "_metric_store_ids", return_value=("req-1",)) as resolve:
                await SERVICE.aggregate_composed(PERIOD, ComposedScope(traffic_class=TrafficClass.ASSISTANT))

        assert resolve.await_args.args[0] == DimensionScope(SpendDimension.ASSISTANT, None)
        assert led.class_ids_seen == [("req-1",)]

    @pytest.mark.asyncio
    async def test_the_class_split_resolves_every_union_class_once_for_the_range(self) -> None:
        led = _StubLedger(Decimal("2"))
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch.object(SpendService, "_metric_store_ids", return_value=("req-1",)) as resolve:
                result = await SERVICE.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=USER))

        assert resolve.call_count == len(UNION_CLASSES)
        assert led.ids_by_class_seen == [dict.fromkeys(UNION_CLASSES, ("req-1",))]
        assert set(result) == set(TrafficClass)

    @pytest.mark.asyncio
    async def test_a_class_no_ledger_answered_for_is_reported_unavailable_rather_than_absent(self) -> None:
        """The key set is the closed enum: a class no ledger answered for is still reported."""

        class _DropsOneClass(_StubLedger):
            async def aggregate_composed_by_traffic_class(self, period, scope, *, dimension_ids=(), ids_by_class=None):
                answers = await super().aggregate_composed_by_traffic_class(
                    period, scope, dimension_ids=dimension_ids, ids_by_class=ids_by_class
                )
                return {cls: entry for cls, entry in answers.items() if cls is not TrafficClass.DATASOURCE}

        led = _DropsOneClass(Decimal("5"))
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch.object(SpendService, "_metric_store_ids", return_value=()):
                result = await SERVICE.aggregate_composed_by_traffic_class(PERIOD, ComposedScope(user=USER))

        assert set(result) == set(TrafficClass)
        assert result[TrafficClass.DATASOURCE].figure is None
        assert result[TrafficClass.DATASOURCE].unavailable == PERIOD

    @pytest.mark.asyncio
    async def test_a_metric_store_failure_degrades_the_figure_and_never_fails_it(self) -> None:
        """The narrowed union's missing rows land in the residue, where the label names them."""
        led = _StubLedger(Decimal("2"))
        with registry({LLMCallClass.CHAT: led, LLMCallClass.EMBEDDINGS: led}):
            with patch(
                "codemie.service.spend.dimension_scope.resolve_metric_store_ids",
                side_effect=RuntimeError("elasticsearch is down"),
            ):
                result = await SERVICE.aggregate_composed(PERIOD, ComposedScope(dimension=ASSISTANT_TERM))

        assert led.dimension_ids_seen == [()]
        assert result.figure == Decimal("2")
