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

"""The batched holder read, held to one property: it agrees with the single-holder read.

**That property is the whole safety argument for the batch existing**, so it is asserted directly
rather than inferred from the SQL. A batched read that quietly re-expressed a holder's predicate to
make the grouping cheaper would return a plausible figure for every holder and a *different* one
than the single-holder path for some - two figures for one holder, which is the defect the port was
built to remove. Every test here compares the two answers rather than checking one in isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql

from codemie.repository.spend_logs_repository import (
    SpendLogsRepository,
    may_hold_rows,
    predicate_for_holder,
    within,
)
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import SpendDimension, TimeRange
from codemie.service.spend.ledger import (
    HOLDER_BATCH_SIZE,
    PersonalHolder,
    PersonTotalHolder,
    ProjectHolder,
    ProjectMemberHolder,
    ReportFilter,
    UserTerm,
)

PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))
USER = "aaaaaaaa-1111-1111-1111-111111111111"


def sql(clause) -> str:
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).replace("\n", " ")


class _CapturingSession:
    """Records each statement and answers it with a row as wide as the statement's column list.

    Sized from the statement rather than from a fixed list, because the chunking test issues two
    statements of different widths and a fixed-width row would fail the `strict=True` zip for a
    reason that has nothing to do with the code under test.
    """

    def __init__(
        self, value: Decimal = Decimal("0"), values: list | None = None, identities: list | None = None
    ) -> None:
        self._value = value
        self._values = values
        self._identities = identities or []
        self.statements: list = []

    async def execute(self, statement):
        self.statements.append(statement)
        width = len(statement.selected_columns)
        row = tuple(self._values[:width]) if self._values is not None else tuple([self._value] * width)
        identities = self._identities

        class _Result:
            def one(self_inner):
                return row

            def all(self_inner):
                return identities

        return _Result()


class TestEveryHolderPredicateSurvivesTheBatch:
    """The batched statement must carry each holder's predicate verbatim, conjunct for conjunct."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "holder",
        [
            PersonalHolder(user_id=USER, category=BudgetCategory.PLATFORM, aliases=frozenset({"alice"})),
            ProjectHolder(project="atlas", category=BudgetCategory.PLATFORM),
            ProjectMemberHolder(project="atlas", category=BudgetCategory.PLATFORM, user_id=USER),
            PersonTotalHolder(user_id=USER, aliases=frozenset({"alice@example.com"})),
        ],
    )
    async def test_the_batched_filter_is_the_single_holder_predicate(self, holder) -> None:
        session = _CapturingSession(Decimal("1.5"))
        await SpendLogsRepository().sum_for_holders(session, [holder], PERIOD)

        rendered = sql(session.statements[0])
        # Compiled with literals inlined, so the whole predicate is comparable as text. A dropped
        # conjunct - `attr_form = 'principal'` is the dangerous one - shows up as a missing substring.
        assert sql(predicate_for_holder(holder)) in rendered
        assert "FILTER (WHERE" in rendered

    @pytest.mark.asyncio
    async def test_the_period_bound_is_the_same_half_open_range(self) -> None:
        session = _CapturingSession()
        await SpendLogsRepository().sum_for_holders(
            session, [ProjectHolder(project="atlas", category=BudgetCategory.PLATFORM)], PERIOD
        )
        assert sql(within(PERIOD)) in sql(session.statements[0])


class TestTheBatchIsOneQuery:
    @pytest.mark.asyncio
    async def test_many_holders_make_one_round_trip(self) -> None:
        holders = [ProjectHolder(project=f"p{index}", category=BudgetCategory.PLATFORM) for index in range(12)]
        session = _CapturingSession(values=[Decimal(index) for index in range(12)])

        totals = await SpendLogsRepository().sum_for_holders(session, holders, PERIOD)

        assert len(session.statements) == 1
        assert [totals[holder] for holder in holders] == [Decimal(index) for index in range(12)]

    @pytest.mark.asyncio
    async def test_a_holder_set_past_the_bound_is_split_and_not_truncated(self) -> None:
        """Splitting is the point of the bound; silently answering the first chunk is not."""
        holders = [
            ProjectHolder(project=f"p{index}", category=BudgetCategory.PLATFORM)
            for index in range(HOLDER_BATCH_SIZE + 3)
        ]
        session = _CapturingSession(Decimal("2"))

        totals = await SpendLogsRepository().sum_for_holders(session, holders, PERIOD)

        # Two chunks and no identity read: a project holder is priced whatever the identities.
        assert len(session.statements) == 2
        assert totals == dict.fromkeys(holders, Decimal("2"))

    @pytest.mark.asyncio
    async def test_no_holders_issues_no_query(self) -> None:
        """`or_()` over nothing is not a predicate, and an unbounded scan is what it would compile to."""
        session = _CapturingSession()
        assert await SpendLogsRepository().sum_for_holders(session, [], PERIOD) == {}
        assert session.statements == []

    @pytest.mark.asyncio
    async def test_one_holder_passed_twice_yields_one_column(self) -> None:
        holder = ProjectHolder(project="atlas", category=BudgetCategory.PLATFORM)
        session = _CapturingSession(Decimal("7"))

        totals = await SpendLogsRepository().sum_for_holders(session, [holder, holder], PERIOD)

        assert totals == {holder: Decimal("7")}


def _person(kind, index: int, aliases: frozenset[str] | None = None):
    user_id = f"user-{index}"
    if kind is PersonalHolder:
        return PersonalHolder(user_id=user_id, category=BudgetCategory.PLATFORM, aliases=aliases)
    return PersonTotalHolder(user_id=user_id, aliases=aliases)


class TestABatchPastOneChunkPricesOnlyHoldersThatCouldMatch:
    """A budget screen asks about every person with a budget; most spent nothing in the window.

    Each chunk's statement costs far more to build than one read of the identities the period's rows
    carry, so past one chunk a person-keyed holder whose user id and aliases are all absent is answered
    the zero its FILTER would have summed, and never priced.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", [PersonalHolder, PersonTotalHolder])
    async def test_only_the_holders_whose_identity_is_present_are_priced(self, kind) -> None:
        by_user_id = _person(kind, 3)
        by_alias = _person(kind, 5, aliases=frozenset({"alice", "alice@example.com"}))
        absent = [_person(kind, index) for index in range(10, 10 + HOLDER_BATCH_SIZE)]
        session = _CapturingSession(
            values=[Decimal("1.25"), Decimal("4.5")], identities=[("user-3", None), (None, "alice@example.com")]
        )

        totals = await SpendLogsRepository().sum_for_holders(session, [*absent, by_user_id, by_alias], PERIOD)

        identities, priced = session.statements
        assert "litellm_spend_logs.attr_principal" in sql(identities)
        assert len(priced.selected_columns) == 2
        assert totals[by_user_id] == Decimal("1.25")
        assert totals[by_alias] == Decimal("4.5")
        assert all(totals[holder] == Decimal(0) for holder in absent)
        assert len(totals) == HOLDER_BATCH_SIZE + 2

    @pytest.mark.asyncio
    async def test_with_the_projection_the_identities_of_whole_hours_come_from_it(self) -> None:
        """A raw identity read over a month would scan the very partitions the projection exists to spare."""
        holders = [_person(PersonalHolder, index) for index in range(HOLDER_BATCH_SIZE + 1)]
        session = _CapturingSession()

        await SpendLogsRepository(projection=True).sum_for_holders(session, holders, PERIOD)

        assert "FROM litellm_spend_hourly" in sql(session.statements[0])

    @pytest.mark.asyncio
    async def test_a_batch_whose_holders_all_spent_nothing_issues_only_the_identity_read(self) -> None:
        holders = [_person(PersonalHolder, index) for index in range(HOLDER_BATCH_SIZE + 1)]
        session = _CapturingSession(identities=[("someone-else", "someone-else@example.com")])

        totals = await SpendLogsRepository().sum_for_holders(session, holders, PERIOD)

        assert len(session.statements) == 1
        assert totals == dict.fromkeys(holders, Decimal(0))


class TestWhichHolderCouldMatchARow:
    """`may_hold_rows` must follow the person-keyed predicates: a holder it rules out wrongly loses real money."""

    @pytest.mark.parametrize("kind", [PersonalHolder, PersonTotalHolder])
    def test_a_person_matches_by_user_id_or_by_any_alias_and_by_nothing_else(self, kind) -> None:
        person = _person(kind, 1, aliases=frozenset({"bob", "bob@example.com"}))

        assert may_hold_rows(person, frozenset({"user-1"}), frozenset())
        assert may_hold_rows(person, frozenset(), frozenset({"bob@example.com"}))
        assert not may_hold_rows(person, frozenset({"user-2"}), frozenset({"carol", "user-1"}))

    @pytest.mark.parametrize("kind", [PersonalHolder, PersonTotalHolder])
    def test_an_unresolved_alias_set_leaves_the_user_id_alone(self, kind) -> None:
        person = _person(kind, 1, aliases=None)

        assert may_hold_rows(person, frozenset({"user-1"}), frozenset())
        assert not may_hold_rows(person, frozenset(), frozenset({"user-1", "bob"}))

    @pytest.mark.parametrize(
        "holder",
        [
            ProjectHolder(project="atlas", category=BudgetCategory.PLATFORM),
            ProjectMemberHolder(project="atlas", category=BudgetCategory.PLATFORM, user_id=USER),
        ],
    )
    def test_a_holder_keyed_on_anything_but_a_person_is_always_priced(self, holder) -> None:
        assert may_hold_rows(holder, frozenset(), frozenset())


class TestTheDefaultLedgerBatch:
    """The metric store's `filters` aggregation, held to the same agreement property."""

    @staticmethod
    def _ledger(response: dict):
        from codemie.service.spend.default_ledger import DefaultSpendLedger
        from codemie.service.spend.routing import LLMCallClass

        class _Repo:
            def __init__(self) -> None:
                self.bodies: list[dict] = []

            async def execute_aggregation_query(self, body):
                self.bodies.append(body)
                return response

        repo = _Repo()
        return DefaultSpendLedger([LLMCallClass.CHAT], repository=repo), repo

    @pytest.mark.asyncio
    async def test_each_person_gets_their_own_bucket_carrying_the_alias_union(self) -> None:
        ledger, repo = self._ledger({"aggregations": {"holders": {"buckets": {"holder_0": {"money": {"value": 3.5}}}}}})
        holder = PersonTotalHolder(user_id=USER, aliases=frozenset({"alice@example.com"}))

        results = await ledger.consumed_for_holders([holder], PERIOD)

        buckets = repo.bodies[0]["aggs"]["holders"]["filters"]["filters"]
        assert list(buckets) == ["holder_0"]
        # The union, not a terms bucket on one field: a person is matched by canonical id OR alias,
        # and a single-field bucket would read zero for everyone whose rows carry the email.
        rendered = repr(buckets["holder_0"])
        assert USER in rendered and "alice@example.com" in rendered
        assert results[holder].figure == Decimal("3.5")

    @pytest.mark.asyncio
    async def test_a_category_keyed_holder_gets_no_figure_and_costs_no_query(self) -> None:
        ledger, repo = self._ledger({})
        holder = PersonalHolder(user_id=USER, category=BudgetCategory.PLATFORM, aliases=frozenset())

        results = await ledger.consumed_for_holders([holder], PERIOD)

        assert results[holder].figure is None
        assert repo.bodies == []

    @pytest.mark.asyncio
    async def test_an_unresolvable_bridge_labels_the_figure_it_does_return(self) -> None:
        ledger, _ = self._ledger({"aggregations": {"holders": {"buckets": {"holder_0": {"money": {"value": 1.0}}}}}})
        holder = PersonTotalHolder(user_id=USER, aliases=None)

        result = (await ledger.consumed_for_holders([holder], PERIOD))[holder]

        assert result.figure == Decimal("1.0")
        portion = result.partial[SpendDimension.IDENTITY_BRIDGE]
        # Both unknown: an unresolvable alias set means the rows cannot be identified, so there is
        # no row set to sum or to count.
        assert portion.spend is None and portion.rows is None

    @pytest.mark.asyncio
    async def test_a_bucket_the_store_did_not_return_is_a_measured_zero(self) -> None:
        """No coverage floor here, so an empty bucket means nothing was recorded - not unknown."""
        ledger, _ = self._ledger({"aggregations": {"holders": {"buckets": {}}}})
        holder = PersonTotalHolder(user_id=USER, aliases=frozenset({"alice"}))

        assert (await ledger.consumed_for_holders([holder], PERIOD))[holder].figure == Decimal("0")

    @pytest.mark.asyncio
    async def test_the_dashboards_filter_narrows_the_outer_query_as_the_summary_cards_render_it(self) -> None:
        """The users- and projects-spending rows were narrowed by this filter, so every holder's bucket
        must be too: one set of clauses in the shared query, spelled by `ReportFilter` itself, and never
        copied into a bucket where one holder could be given it and another forgotten."""
        ledger, repo = self._ledger({"aggregations": {"holders": {"buckets": {}}}})
        holder = PersonTotalHolder(user_id=USER, aliases=frozenset({"alice"}))
        asked = ReportFilter(users=(UserTerm(user_id=USER, aliases=frozenset()),), projects=("proj-b",))

        await ledger.consumed_for_holders([holder], PERIOD, report_filter=asked)

        outer = repo.bodies[0]["query"]["bool"]["filter"]
        expected = asked.elasticsearch_filters(
            user_id_field="attributes.user_id.keyword", project_field="attributes.project.keyword"
        )
        assert expected and all(clause in outer for clause in expected)
        assert "proj-b" not in repr(repo.bodies[0]["aggs"]["holders"]), "the filter must not be copied into a bucket"

    @pytest.mark.asyncio
    async def test_a_budget_read_naming_no_filter_adds_no_clause(self) -> None:
        unfiltered, unfiltered_repo = self._ledger({"aggregations": {"holders": {"buckets": {}}}})
        empty, empty_repo = self._ledger({"aggregations": {"holders": {"buckets": {}}}})
        holder = PersonTotalHolder(user_id=USER, aliases=frozenset({"alice"}))

        await unfiltered.consumed_for_holders([holder], PERIOD)
        await empty.consumed_for_holders([holder], PERIOD, report_filter=ReportFilter())

        assert unfiltered_repo.bodies[0]["query"] == empty_repo.bodies[0]["query"]
