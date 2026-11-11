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

"""The summary cards' read, and the three ways four figures on one screen stop adding up.

Each failure pinned here prints a plausible screen. `<> 'cli'` instead of `IS DISTINCT FROM` drops
every unattributed row from the platform card while the total beside it still counts them, so two
cards on one screen disagree by exactly the money nobody attributed. A platform figure computed as
*total less CLI* subtracts two independently floored numbers, each with its own flag, and the
difference is qualified by nobody. And a scan that carries the caller's boundary but not the
dashboard's filter shows a filtered screen an unfiltered total.

The card selectors are the traffic classes' own, pinned as compiled SQL rather than trusted to the
shared table, so a card and the chart under it cannot come to mean different things by "CLI".
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql

from codemie.enterprise.litellm.spend_ledger import SpendLogsLedger
from codemie.repository.spend_group_batch import SpendGroupBatch
from codemie.repository.spend_logs_repository import SpendLogsRepository, report_filter_predicate
from codemie.service.spend.coverage import LedgerCoverage, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.identity_bridge import identity_bridge_portion
from codemie.service.spend.ledger import AccessScope, ReportFilter, UserTerm
from codemie.service.spend.spend_group import SpendGroup, spend_group_predicate
from codemie.service.spend.traffic_class import TrafficClass, traffic_class_predicate

PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))

ALICE_SCOPE = AccessScope(unrestricted=False, user_id="alice", plain_projects=("A",))
ASKED = ReportFilter(users=(UserTerm("bob", frozenset({"bob@example.com"})),), projects=("B",))


def sql(clause) -> str:
    """The clause as PostgreSQL would see it, with literals inlined so it can be asserted on."""
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).replace("\n", " ")


class _SqlSession:
    """Records the SQL of every statement a read issues, with no database behind it."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(sql(statement))
        return self

    def one(self):
        return [0] * len(SpendGroup)

    def all(self):
        return []


async def rendered(access=None, report_filter=None) -> str:
    """The SQL the group read issues."""
    session = _SqlSession()
    await SpendLogsRepository().sum_for_spend_groups(session, PERIOD, access, report_filter)
    assert len(session.statements) == 1, "the four cards must foot against each other, so they share one scan"
    return session.statements[0]


def group_labels(rendered_sql: str) -> list[str]:
    """The four column labels, in the order the statement lists them.

    Split on the table rather than on `FROM`: the platform column's own `IS DISTINCT FROM` carries
    one, and a select list truncated there would pin only the total.
    """
    return re.findall(r"AS (group_\d+)", rendered_sql.split(" FROM litellm_spend_logs")[0])


class TestTheFourCardsComeFromOneScan:
    @pytest.mark.asyncio
    async def test_the_total_is_a_bare_aggregate_and_the_other_three_are_filtered(self) -> None:
        """ "Every row" is the absence of a selector. Spelled as an always-true `FILTER` instead, a
        later edit could narrow the total without looking like a narrowing."""
        statement = await rendered()

        assert statement.count("FILTER (WHERE") == len(SpendGroup) - 1
        assert "coalesce(sum(litellm_spend_logs.spend), 0)" in statement

    @pytest.mark.asyncio
    async def test_the_columns_are_in_enum_order_so_the_row_maps_back_to_the_groups(self) -> None:
        """One row of positional figures: a column order that drifts from the enum pairs each figure
        with the next group's card - every number plausible and every one wrong."""
        statement = await rendered()

        assert group_labels(statement) == [f"group_{index}" for index in range(len(SpendGroup))]
        assert list(SpendGroup) == [SpendGroup.TOTAL, SpendGroup.PLATFORM, SpendGroup.CLI, SpendGroup.EMBEDDING]

        # Each selector sits in its own group's column, so a figure cannot be read off the next card.
        positions = [
            statement.index(marker) for marker in ("IS DISTINCT FROM", "attr_category = 'cli'", "call_type IN")
        ]
        assert positions == sorted(positions)
        assert [statement.index(f"AS group_{index}") for index in (1, 2, 3)] == sorted(
            statement.index(f"AS group_{index}") for index in (1, 2, 3)
        )

    @pytest.mark.asyncio
    async def test_the_figures_come_back_keyed_on_the_group_that_selected_them(self) -> None:
        class _Session:
            async def execute(self, statement):
                class _R:
                    def one(self_inner):
                        return [Decimal("10"), Decimal("7"), Decimal("3"), Decimal("2")]

                return _R()

        totals = await SpendLogsRepository().sum_for_spend_groups(_Session(), PERIOD)

        assert totals == {
            SpendGroup.TOTAL: Decimal("10"),
            SpendGroup.PLATFORM: Decimal("7"),
            SpendGroup.CLI: Decimal("3"),
            SpendGroup.EMBEDDING: Decimal("2"),
        }
        assert totals[SpendGroup.TOTAL] == totals[SpendGroup.PLATFORM] + totals[SpendGroup.CLI]


class TestThePlatformCardAdmitsTheUnattributedRow:
    @pytest.mark.asyncio
    async def test_it_is_is_distinct_from_and_never_a_bare_inequality(self) -> None:
        """`NULL <> 'cli'` is *unknown*, an aggregate `FILTER` admits no unknown, and the platform card
        would quietly omit exactly the money the total beside it still counts."""
        statement = await rendered()

        assert "attr_category IS DISTINCT FROM 'cli'" in statement
        assert "attr_category != 'cli'" not in statement
        assert "attr_category <> 'cli'" not in statement

    def test_the_predicate_itself_says_so_too(self) -> None:
        assert "IS DISTINCT FROM" in sql(spend_group_predicate(SpendGroup.PLATFORM))

    def test_the_total_selects_on_nothing_at_all(self) -> None:
        assert spend_group_predicate(SpendGroup.TOTAL) is None


class TestTheCardsAndTheChartCannotMeanDifferentThings:
    """The two selectors a card shares with a traffic class, compared as compiled SQL.

    A second spelling of "CLI" is how a card and the chart printed under it come to disagree about
    one window, with both numbers looking right on their own.
    """

    def test_the_cli_card_is_the_cli_traffic_class(self) -> None:
        assert sql(spend_group_predicate(SpendGroup.CLI)) == sql(traffic_class_predicate(TrafficClass.CLI))

    def test_the_embedding_card_is_the_datasource_traffic_class(self) -> None:
        assert sql(spend_group_predicate(SpendGroup.EMBEDDING)) == sql(traffic_class_predicate(TrafficClass.DATASOURCE))

    @pytest.mark.asyncio
    async def test_the_statement_carries_those_two_selectors_verbatim(self) -> None:
        statement = await rendered()

        assert sql(traffic_class_predicate(TrafficClass.CLI)) in statement
        assert "call_type IN ('embedding', 'aembedding')" in statement


class TestTheScanIsNarrowedByBothAndBoundByTime:
    @pytest.mark.asyncio
    async def test_the_range_predicate_is_the_half_open_one(self) -> None:
        statement = await rendered()

        assert "start_time >=" in statement and "start_time <" in statement
        assert "BETWEEN" not in statement.upper()

    @pytest.mark.asyncio
    async def test_the_boundary_and_the_filter_are_conjuncts_of_the_one_where(self) -> None:
        """One conjunct each in the `WHERE`, never one per `FILTER`: an aggregate's `FILTER` sees only
        the rows the `WHERE` admitted, so both narrowings reach all four cards at once."""
        where = (await rendered(ALICE_SCOPE, ASKED)).split(" WHERE ")[1]

        assert "attr_project IN ('A')" in where, "the caller's boundary"
        assert "attr_user_id = 'bob'" in where and "attr_project IN ('B')" in where, "what was asked for"
        assert where.count("FILTER (WHERE") == 0


class TestWhatTheDashboardAskedFor:
    """`report_filter_predicate`: a filter, and emphatically not a boundary.

    Their empty states mean opposite things - a restricted boundary with no project matches nothing,
    a filter with no term matches everything - so an implementation that merged them would have to
    pick one meaning and would be wrong on one of the two.
    """

    def test_no_filter_and_an_empty_one_are_both_the_whole_window(self) -> None:
        assert report_filter_predicate(None) is None
        assert report_filter_predicate(ReportFilter()) is None

    def test_a_user_term_matches_the_canonical_id_and_every_alias(self) -> None:
        """A person is not one identifier on the rows the parser attributed: some code paths emit
        `users.username` and some `users.email` for the same human, so an id-only filter under-reports."""
        rendered_sql = sql(report_filter_predicate(ReportFilter(users=(UserTerm("u1", frozenset({"alice"})),))))

        assert "attr_user_id = 'u1'" in rendered_sql
        assert "attr_principal IN ('alice')" in rendered_sql

    def test_several_users_are_ored_because_a_dashboard_naming_three_asks_for_all_three(self) -> None:
        rendered_sql = sql(
            report_filter_predicate(ReportFilter(users=(UserTerm("u1", frozenset()), UserTerm("u2", frozenset()))))
        )

        assert "attr_user_id = 'u1' OR" in rendered_sql
        assert "attr_user_id = 'u2'" in rendered_sql

    def test_an_unresolvable_alias_set_yields_the_explicit_half_alone(self) -> None:
        """The only honest SQL for an alias set nobody could resolve: there is no fallback conjunct
        to write, so the figure is short by that person's principal-form rows and the ledger labels
        the hole with `identity_bridge` rather than this predicate guessing at it."""
        rendered_sql = sql(report_filter_predicate(ReportFilter(users=(UserTerm("u1", None),))))

        assert rendered_sql == "litellm_spend_logs.attr_user_id = 'u1'"

    def test_a_project_term_is_the_project_scoped_match_every_project_read_shares(self) -> None:
        rendered_sql = sql(report_filter_predicate(ReportFilter(projects=("P", "Q"))))

        assert "attr_form = 'project_scoped'" in rendered_sql
        assert "attr_project IN ('P', 'Q')" in rendered_sql

    def test_both_kinds_of_term_are_anded_because_one_asks_for_their_spend_in_it(self) -> None:
        rendered_sql = sql(report_filter_predicate(ReportFilter(users=(UserTerm("u1", frozenset()),), projects=("P",))))

        assert "attr_user_id = 'u1'" in rendered_sql
        assert "attr_project IN ('P')" in rendered_sql
        assert " AND " in rendered_sql


COMMON = ReportFilter(projects=("B",))


def _rows_sharing(common: ReportFilter, count: int) -> list[ReportFilter]:
    """`count` user rows of one table, each carrying its own person and the table's common term."""
    return [
        ReportFilter(
            users=(UserTerm(f"row-user-{index}", frozenset({f"row-alias-{index}"})),), projects=common.projects
        )
        for index in range(count)
    ]


async def rendered_batch(report_filters, access=None, common_filter=None) -> list[str]:
    """The SQL the grouped read issues for one batch, through the projection's path."""
    session = _SqlSession()
    group_key = SpendGroupBatch.of(report_filters).group_key
    await SpendLogsRepository(projection=True).sum_for_spend_groups_per_attribution(
        session, PERIOD, group_key, access=access, common_filter=common_filter
    )
    return session.statements


class TestTheBatchedReadIsOneStatementWhateverTheRowCount:
    @pytest.mark.asyncio
    async def test_one_statement_answers_every_filter(self) -> None:
        statements = await rendered_batch(_rows_sharing(COMMON, 50), ALICE_SCOPE, COMMON)

        assert len(statements) == 1

    @pytest.mark.asyncio
    async def test_it_reads_the_window_rows_grouped_by_the_key_with_the_group_aggregates(self) -> None:
        """The same source the per-filter projected read takes, hourly rows plus raw edge hours, with a
        group whose rows net to zero dropped rather than returned as a zero row."""
        (statement,) = await rendered_batch(_rows_sharing(COMMON, 2), ALICE_SCOPE, COMMON)
        grouping = " ".join(statement.split(" GROUP BY ")[1].split())

        assert "AS window_rows" in statement
        assert grouping.startswith(
            "window_rows.attr_source, window_rows.attr_form, window_rows.attr_user_id, "
            "window_rows.attr_principal, window_rows.attr_project HAVING sum(window_rows.row_count) > 0"
        )
        assert re.findall(r"AS (group_\d+)", statement) == [f"group_{index}" for index in range(len(SpendGroup))]
        assert statement.count("FILTER (WHERE") == len(SpendGroup) - 1

    @pytest.mark.asyncio
    async def test_its_where_is_the_boundary_and_the_common_term_alone(self) -> None:
        (statement,) = await rendered_batch(_rows_sharing(COMMON, 2), ALICE_SCOPE, COMMON)

        assert "attr_user_id = 'alice'" in statement, "the boundary's person"
        assert "attr_project = ANY (ARRAY['A'])" in statement, "the boundary's projects"
        assert "attr_project = ANY (ARRAY['B'])" in statement, "the common term"
        assert "row-" not in statement, "no row's own term"

    @pytest.mark.asyncio
    async def test_ten_thousand_filters_issue_the_statement_one_filter_does(self) -> None:
        (one,) = await rendered_batch(_rows_sharing(COMMON, 1), ALICE_SCOPE, COMMON)
        (many,) = await rendered_batch(_rows_sharing(COMMON, 10_000), ALICE_SCOPE, COMMON)

        assert many == one

    @pytest.mark.asyncio
    async def test_ten_thousand_project_rows_issue_the_statement_one_does_and_name_none_of_them(self) -> None:
        (one,) = await rendered_batch(_project_rows_sharing(USERS_COMMON, 1), ALICE_SCOPE, USERS_COMMON)
        (many,) = await rendered_batch(_project_rows_sharing(USERS_COMMON, 10_000), ALICE_SCOPE, USERS_COMMON)

        assert many == one
        assert "row-" not in many, "no row's project or owner"


USERS_COMMON = ReportFilter(users=(UserTerm("carol", frozenset({"carol@example.com"})),))


def _project_rows_sharing(common: ReportFilter, count: int) -> list[ReportFilter]:
    """`count` project rows of one table, each carrying its own project and owner and the table's users term."""
    return [
        ReportFilter(
            users=common.users, projects=(f"row-project-{index}",), personal_project_owners=(f"row-owner-{index}",)
        )
        for index in range(count)
    ]


class _StubRepository:
    """`attributions` are the grouped read's rows, each as the attribution columns it carries; every row
    sums to `totals`, so a filter matching exactly one row is answered what the single read answers."""

    def __init__(self, totals=None, attributions=()) -> None:
        self._totals = totals or dict.fromkeys(SpendGroup, Decimal("0"))
        self._attributions = attributions
        self.reads = 0
        self.range_seen = None
        self.access_seen = "unset"
        self.filter_seen = "unset"

    async def sum_for_spend_groups(self, session, period, access=None, report_filter=None):
        self.reads += 1
        self.range_seen = period
        self.access_seen = access
        self.filter_seen = report_filter
        return dict(self._totals)

    async def sum_for_spend_groups_per_attribution(self, session, period, group_key, access=None, common_filter=None):
        self.reads += 1
        self.range_seen = period
        self.access_seen = access
        self.filter_seen = common_filter
        figures = tuple(self._totals[group] for group in SpendGroup)
        return [tuple(attribution.get(column) for column in group_key) + figures for attribution in self._attributions]


class _StubSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def ledger(totals=None, coverage: LedgerCoverage | None = None, attributions=()):
    repository = _StubRepository(totals=totals, attributions=attributions)
    covered = coverage or LedgerCoverage(coverage_floor=datetime(2026, 1, 1, tzinfo=timezone.utc), ready=True)
    return SpendLogsLedger(covered, session_factory=_StubSession, repository=repository), repository


class TestTheAdapterAnswersEveryGroupOnce:
    @pytest.mark.asyncio
    async def test_every_group_is_answered_and_none_is_omitted(self) -> None:
        """This table holds every LiteLLM-billed row, so a group whose traffic a deployment does not
        route through LiteLLM is a **measured zero** here - a fact about this ledger, not an absence -
        and the facade adds the other adapter's figure to it."""
        led, repository = ledger(
            totals={
                SpendGroup.TOTAL: Decimal("10"),
                SpendGroup.PLATFORM: Decimal("7"),
                SpendGroup.CLI: Decimal("3"),
                SpendGroup.EMBEDDING: Decimal("2"),
            }
        )
        result = await led.aggregate_by_spend_group(PERIOD, access=None)

        assert set(result) == set(SpendGroup)
        assert result[SpendGroup.PLATFORM].figure == Decimal("7")
        assert repository.reads == 1

    @pytest.mark.asyncio
    async def test_no_group_carries_a_partial_label(self) -> None:
        """The four selectors are native columns, so there is no unresolved remainder to name - and the
        read is not project-keyed, so no project portion applies either."""
        led, _ = ledger()
        result = await led.aggregate_by_spend_group(PERIOD, access=None)

        assert all(not entry.partial for entry in result.values())
        assert all(entry.is_exact for entry in result.values())

    @pytest.mark.asyncio
    async def test_a_filter_on_an_unbridged_person_labels_every_card(self) -> None:
        """The scan matches that person's canonical id alone, so every card is short by the
        principal-form rows nobody can key back to them - and short by an amount nobody can compute,
        which is why the portion is the unknown one."""
        led, _ = ledger()
        asked = ReportFilter(users=(UserTerm("u1", None),))

        result = await led.aggregate_by_spend_group(PERIOD, access=None, report_filter=asked)

        assert set(result) == set(SpendGroup)
        for group, entry in result.items():
            assert entry.partial[SpendDimension.IDENTITY_BRIDGE] == identity_bridge_portion(), group

    @pytest.mark.asyncio
    async def test_a_filter_whose_personal_projects_nobody_could_look_up_labels_every_card(self) -> None:
        """Without the lookup a personal project is matched by its name alone, which no principal-form
        row carries - so every card may be short by that project's whole pot, by an amount nobody can
        compute."""
        led, _ = ledger()
        asked = ReportFilter(projects=("P",), personal_project_owners=None)

        result = await led.aggregate_by_spend_group(PERIOD, access=None, report_filter=asked)

        for group, entry in result.items():
            assert entry.partial[SpendDimension.IDENTITY_BRIDGE] == identity_bridge_portion(), group

    @pytest.mark.asyncio
    async def test_a_resolved_personal_project_owner_leaves_every_card_exact(self) -> None:
        led, _ = ledger()
        asked = ReportFilter(projects=("alice@example.com",), personal_project_owners=("u1",))

        result = await led.aggregate_by_spend_group(PERIOD, access=None, report_filter=asked)

        assert all(entry.is_exact for entry in result.values())

    @pytest.mark.asyncio
    async def test_a_resolved_alias_set_leaves_every_card_exact(self) -> None:
        """An empty alias set is an answer - this person has no fallback identities - not a failure,
        and labelling it reports four complete cards as short."""
        led, _ = ledger()
        asked = ReportFilter(users=(UserTerm("u1", frozenset()),))

        result = await led.aggregate_by_spend_group(PERIOD, access=None, report_filter=asked)

        assert all(entry.is_exact for entry in result.values())


class TestTheFloorIsAppliedOnceForTheWholeRange:
    @pytest.mark.asyncio
    async def test_a_rejected_range_yields_no_figure_for_every_group(self) -> None:
        unready = LedgerCoverage(coverage_floor=datetime(2026, 1, 1, tzinfo=timezone.utc), ready=False)
        led, repository = ledger(coverage=unready)
        result = await led.aggregate_by_spend_group(PERIOD, access=None)

        assert result == {group: SpendResult.no_figure(PERIOD) for group in SpendGroup}
        assert repository.reads == 0, "a range no figure exists for is not queried"

    @pytest.mark.asyncio
    async def test_a_range_crossing_the_floor_flags_every_group_with_the_range_flag(self) -> None:
        """A zero without the flag reads as measured. The flag is the only thing that says otherwise,
        and it names the whole range rather than being restated per card."""
        crossing = LedgerCoverage(coverage_floor=datetime(2026, 8, 15, tzinfo=timezone.utc), ready=True)
        led, repository = ledger(totals=dict.fromkeys(SpendGroup, Decimal("4")), coverage=crossing)
        result = await led.aggregate_by_spend_group(PERIOD, access=None)

        flags = {entry.unavailable for entry in result.values()}
        assert flags == {TimeRange(PERIOD.start, crossing.coverage_floor)}
        assert all(entry.figure == Decimal("4") for entry in result.values())
        assert repository.range_seen == TimeRange(crossing.coverage_floor, PERIOD.end)


class TestTheBoundaryAndTheFilterReachTheRepository:
    @pytest.mark.asyncio
    async def test_both_are_handed_down_unchanged(self) -> None:
        """The cards sit beside tables `SecureQueryBuilder` narrowed to the caller; a figure computed
        without the boundary foots a member's own rows with the platform's spend."""
        led, repository = ledger()
        await led.aggregate_by_spend_group(PERIOD, access=ALICE_SCOPE, report_filter=ASKED)

        assert (repository.access_seen, repository.filter_seen) == (ALICE_SCOPE, ASKED)

    @pytest.mark.asyncio
    async def test_a_read_with_no_caller_narrows_nothing(self) -> None:
        led, repository = ledger()
        await led.aggregate_by_spend_group(PERIOD, access=None)

        assert (repository.access_seen, repository.filter_seen) == (None, None)


UNBRIDGED_PERSON = ReportFilter(users=(UserTerm("u1", None),))
UNLOOKED_UP_OWNERS = ReportFilter(projects=("P",), personal_project_owners=None)
RESOLVED_PERSON = ReportFilter(users=(UserTerm("u2", frozenset({"u2@example.com"})),))

#: One grouped row per filter above, each matched by that filter alone.
PER_FILTER_ATTRIBUTIONS = (
    {"attr_user_id": "u1"},
    {"attr_form": "project_scoped", "attr_project": "P"},
    {"attr_user_id": "u2"},
)

CARD_TOTALS = {
    SpendGroup.TOTAL: Decimal("10"),
    SpendGroup.PLATFORM: Decimal("7"),
    SpendGroup.CLI: Decimal("3"),
    SpendGroup.EMBEDDING: Decimal("2"),
}


class TestEveryFilterOfABatchGetsTheCardsItsOwnReadGives:
    @pytest.mark.parametrize(
        "asked",
        [
            pytest.param(UNBRIDGED_PERSON, id="unbridged-person"),
            pytest.param(UNLOOKED_UP_OWNERS, id="unlooked-up-personal-project-owners"),
            pytest.param(RESOLVED_PERSON, id="resolved-person"),
        ],
    )
    @pytest.mark.parametrize(
        "coverage",
        [
            pytest.param(
                LedgerCoverage(coverage_floor=datetime(2026, 1, 1, tzinfo=timezone.utc), ready=True), id="covered"
            ),
            pytest.param(
                LedgerCoverage(coverage_floor=datetime(2026, 8, 15, tzinfo=timezone.utc), ready=True),
                id="crossing-the-floor",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_its_figures_and_flags_equal_the_single_read(self, asked, coverage) -> None:
        """A table read one row at a time and one read in a batch must not disagree about one row's
        money, nor about whether that money is short or flagged."""
        led, _ = ledger(totals=CARD_TOTALS, coverage=coverage, attributions=PER_FILTER_ATTRIBUTIONS)

        batched = await led.aggregate_by_spend_group_per_filter(
            PERIOD,
            access=ALICE_SCOPE,
            common_filter=None,
            report_filters=[UNBRIDGED_PERSON, UNLOOKED_UP_OWNERS, RESOLVED_PERSON],
        )

        assert batched[asked] == await led.aggregate_by_spend_group(PERIOD, access=ALICE_SCOPE, report_filter=asked)

    @pytest.mark.asyncio
    async def test_the_boundary_the_common_term_and_the_admitted_range_reach_the_one_read(self) -> None:
        crossing = LedgerCoverage(coverage_floor=datetime(2026, 8, 15, tzinfo=timezone.utc), ready=True)
        led, repository = ledger(coverage=crossing)

        await led.aggregate_by_spend_group_per_filter(
            PERIOD, access=ALICE_SCOPE, common_filter=COMMON, report_filters=_rows_sharing(COMMON, 3)
        )

        assert (repository.access_seen, repository.filter_seen) == (ALICE_SCOPE, COMMON)
        assert repository.range_seen == TimeRange(crossing.coverage_floor, PERIOD.end)


class TestTheBatchedCardsAreOneRead:
    @pytest.mark.asyncio
    async def test_fifty_filters_make_one_repository_read(self) -> None:
        led, repository = ledger()

        result = await led.aggregate_by_spend_group_per_filter(
            PERIOD, access=None, common_filter=COMMON, report_filters=_rows_sharing(COMMON, 50)
        )

        assert len(result) == 50
        assert repository.reads == 1

    @pytest.mark.asyncio
    async def test_a_rejected_range_yields_what_the_single_read_does_without_a_read(self) -> None:
        unready = LedgerCoverage(coverage_floor=datetime(2026, 1, 1, tzinfo=timezone.utc), ready=False)
        led, repository = ledger(coverage=unready)
        asked = [UNBRIDGED_PERSON, RESOLVED_PERSON]

        result = await led.aggregate_by_spend_group_per_filter(
            PERIOD, access=None, common_filter=None, report_filters=asked
        )
        batched_reads = repository.reads
        single = {
            report_filter: await led.aggregate_by_spend_group(PERIOD, access=None, report_filter=report_filter)
            for report_filter in asked
        }

        assert result == single
        assert batched_reads == 0, "a range no figure exists for is not queried"

    @pytest.mark.asyncio
    async def test_no_filters_ask_nothing_and_read_nothing(self) -> None:
        led, repository = ledger()

        result = await led.aggregate_by_spend_group_per_filter(
            PERIOD, access=None, common_filter=None, report_filters=[]
        )

        assert result == {}
        assert repository.reads == 0
