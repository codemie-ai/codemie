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

"""The holder reads' access boundary as **figures over real rows** - and the reads that must not have one.

Its neighbour `test_spend_logs_access_boundary.py` does this for the dimension-grouped read. Both exist
for the same reason: the statement that discloses another person's spend is perfectly well-formed, so a
test asserting predicate *shape* cannot see the defect, and what is wrong about it is the number.

**The disclosure, in its own words.** The users-spending and projects-spending tables moved their money
column off an Elasticsearch aggregation - narrowed at construction by `SecureQueryBuilder` - onto
`consumed_for_holders`, whose `PersonTotalHolder` is documented as *"a person's total across everything -
both pots, deliberately"*. The row set stayed narrowed and the figure stopped being, so a project admin
was shown a member's spend from projects they cannot access, and a plain member their project's whole
spend rather than their own share. Both numbers are asserted below, narrowed and not.

**And the half that must NOT be narrowed, asserted just as hard.** The same two methods answer budget
questions: *what has this budget consumed*. That figure is a property of the holder rather than of
whoever is looking, so narrowing it would let a person's spend escape their own limit the moment the
reader could not see the project it landed in - `bob` would consume $64.00 against a $60.00 limit and be
told he had spent $50.00. `TestABudgetReadIsNotNarrowedAndThatIsTheWholePointOfTheDefault` pins the
$64.00.

SQLite in memory rather than PostgreSQL, for the reason the neighbour gives: nothing under test here is
dialect-specific - a sum, a half-open range, an aggregate `FILTER` and the boundary's own `OR`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from codemie.repository.spend_logs_model import LiteLLMSpendLog as Row
from codemie.repository.spend_logs_repository import SpendLogsRepository
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.ledger import (
    HOLDER_BATCH_SIZE,
    AccessScope,
    PersonalHolder,
    PersonTotalHolder,
    ProjectHolder,
    ProjectMemberHolder,
    ReportFilter,
    UserTerm,
)

PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))
IN_PERIOD = datetime(2026, 8, 10, tzinfo=timezone.utc)

ALICE = "alice-1111-1111-1111-111111111111"
BOB = "bob-2222-2222-2222-222222222222"
CAROL = "carol-3333-3333-3333-333333333333"
DAVE = "dave-4444-4444-4444-444444444444"

#: alice's own scope: a plain member of project A and of nothing else.
ALICE_SCOPE = AccessScope(unrestricted=False, user_id=ALICE, plain_projects=("A",))

#: An admin of project A, carrying **no** user term - which is the difference between the two clauses
#: of the boundary and the reason a project admin sees the project total rather than their own rows.
PROJECT_ADMIN_SCOPE = AccessScope(unrestricted=False, user_id=DAVE, admin_projects=("A",))

#: A caller entitled to no project at all - the case an empty collection is most dangerous in.
NO_PROJECTS_SCOPE = AccessScope(unrestricted=False, user_id=CAROL)

BOB_TOTAL = PersonTotalHolder(user_id=BOB, aliases=None)
ALICE_TOTAL = PersonTotalHolder(user_id=ALICE, aliases=None)
PROJECT_A = ProjectHolder(project="A", category=BudgetCategory.PLATFORM)


def _project_row(request_id: str, user_id: str, project: str, spend: str) -> Row:
    """One attributed, project-scoped spend row - the project pot."""
    return Row(
        request_id=request_id,
        start_time=IN_PERIOD,
        end_time=IN_PERIOD,
        spend=Decimal(spend),
        status="success",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        attr_source="explicit",
        attr_form="project_scoped",
        attr_project=project,
        attr_category=BudgetCategory.PLATFORM.value,
        attr_user_id=user_id,
        codemie_request_id=request_id,
    )


def _principal_row(request_id: str, user_id: str, spend: str) -> Row:
    """One principal-form row carrying **no project at all** - the personal pot.

    `parsed` rather than `explicit` so the row also lands in the project-dimension residue: this is
    the same row that makes a project figure partial and the same row a restricted caller may not be
    told about.
    """
    return Row(
        request_id=request_id,
        start_time=IN_PERIOD,
        end_time=IN_PERIOD,
        spend=Decimal(spend),
        status="success",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        attr_source="parsed",
        attr_form="principal",
        attr_project=None,
        attr_category=BudgetCategory.PLATFORM.value,
        attr_user_id=user_id,
        codemie_request_id=request_id,
    )


class _SyncSessionAsAsync:
    """The repository's `AsyncSession` surface over a synchronous SQLite session.

    Only `execute` is used by the reads under test, and awaiting a completed value is exactly what an
    async driver would hand back - so the statement the repository builds is the statement the engine
    runs, unmodified.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement):
        return self._session.execute(statement)


@pytest.fixture
def session():
    """The scenario, with both pots populated and three projects' worth of rows.

    | rows | project A | project B | no project (principal) | total |
    | --- | --- | --- | --- | --- |
    | alice | $0.01 | $3.00 | $5.00 | $8.01 |
    | bob | $50.00 | $9.00 | $5.00 | $64.00 |

    Every column is load-bearing. Without project B a boundary ignoring the project term entirely
    would still pass; without the principal rows a boundary admitting *another person's* project-less
    money would too, and neither the both-pots reading of `PersonTotalHolder` nor the project residue
    would be observable at all.
    """
    engine = create_engine("sqlite://")
    Row.metadata.create_all(engine, tables=[Row.__table__])
    with Session(engine) as sync_session:
        sync_session.add_all(
            [
                _project_row("alice-a", ALICE, "A", "0.01"),
                _project_row("alice-b", ALICE, "B", "3.00"),
                _principal_row("alice-personal", ALICE, "5.00"),
                *[_project_row(f"bob-a-{index}", BOB, "A", "0.10") for index in range(500)],
                _project_row("bob-b", BOB, "B", "9.00"),
                _principal_row("bob-personal", BOB, "5.00"),
            ]
        )
        sync_session.commit()
        yield _SyncSessionAsAsync(sync_session)


def _batched(session, holder, access) -> Decimal:
    return asyncio.run(SpendLogsRepository().sum_for_holders(session, [holder], PERIOD, access))[holder]


def _single(session, holder, access) -> Decimal:
    return asyncio.run(SpendLogsRepository().sum_for_holder(session, holder, PERIOD, access))


class TestARestrictedCallersHolderFigureExcludesSpendTheyMayNotSee:
    """The disclosure, as money. This is the test the mutation check is aimed at."""

    def test_a_project_admin_sees_a_members_spend_in_their_project_and_nothing_beyond_it(self, session) -> None:
        """dave administers project A alone. bob's spend there is $50.00; his platform total is $64.00,
        and the $14.00 difference sits in a project dave cannot access and in bob's personal pot."""
        assert _batched(session, BOB_TOTAL, PROJECT_ADMIN_SCOPE) == Decimal("50.00")

    def test_the_same_figure_unnarrowed_is_the_leak_this_replaces(self, session) -> None:
        """Pinned so the number the defect produced is written down: bob's whole-platform $64.00,
        reported on a row set Elasticsearch had already narrowed to project A."""
        assert _batched(session, BOB_TOTAL, AccessScope.everything()) == Decimal("64.00")

    def test_a_plain_member_sees_their_own_spend_in_their_projects_and_their_own_pot(self, session) -> None:
        """The plain clause pins the caller's own id and admits their projects **and their personal
        pot**: alice's figure is her project-A cent plus her $5.00 pot, which is what Elasticsearch
        showed her before the money moved - a personal-pot call reaches it under her personal project,
        one of her own. Her $3.00 in project B, which she is not a member of, stays out."""
        assert _batched(session, ALICE_TOTAL, ALICE_SCOPE) == Decimal("5.01")
        assert _batched(session, ALICE_TOTAL, AccessScope.everything()) == Decimal("8.01")

    def test_a_plain_member_is_shown_none_of_another_members_money(self, session) -> None:
        assert _batched(session, BOB_TOTAL, ALICE_SCOPE) == Decimal("0")

    def test_the_boundary_is_not_a_global_switch_so_each_caller_gets_their_own_answer(self, session) -> None:
        """bob's own figure under his own scope: his project-A $50.00 and his $5.00 pot, not his $9.00 in B."""
        bob_scope = AccessScope(unrestricted=False, user_id=BOB, plain_projects=("A",))
        assert _batched(session, BOB_TOTAL, bob_scope) == Decimal("55.00")


class TestAProjectHolderIsNarrowedToTheCallersOwnShareOfTheProject:
    """The projects-spending table. `ProjectHolder` answers a project's **whole** total, which is the
    right figure for a budget and the wrong one to foot a plain member's narrowed row set with."""

    def test_a_project_admin_sees_the_project_total(self, session) -> None:
        """The admin half carries no user term, so every member's spend in project A is theirs to see."""
        assert _batched(session, PROJECT_A, PROJECT_ADMIN_SCOPE) == Decimal("50.01")

    def test_a_plain_member_sees_their_own_share_of_it(self, session) -> None:
        assert _batched(session, PROJECT_A, ALICE_SCOPE) == Decimal("0.01")

    def test_and_unnarrowed_the_plain_member_was_shown_every_members_spend(self, session) -> None:
        assert _batched(session, PROJECT_A, AccessScope.everything()) == Decimal("50.01")


class TestACallerEntitledToNothingGetsNothing:
    """The trap this design keeps returning to: an empty collection read as *unconstrained*."""

    def test_a_restricted_caller_with_no_accessible_project_sees_no_money(self, session) -> None:
        assert _batched(session, BOB_TOTAL, NO_PROJECTS_SCOPE) == Decimal("0")
        assert _batched(session, PROJECT_A, NO_PROJECTS_SCOPE) == Decimal("0")

    def test_and_the_same_reads_unnarrowed_prove_the_money_was_there_to_be_seen(self, session) -> None:
        """Without this half the assertions above pass on an empty table."""
        assert _batched(session, BOB_TOTAL, AccessScope.everything()) > Decimal("60")
        assert _batched(session, PROJECT_A, AccessScope.everything()) > Decimal("50")


class TestABudgetReadIsNotNarrowedAndThatIsTheWholePointOfTheDefault:
    """**The half that must not change, and the reason the parameter defaults to `None`.**

    A budget's consumption is a property of the budget, not of whoever happens to be looking. Narrowed
    by a reader's boundary, bob's $64.00 would be enforced as $50.00 and the $14.00 he spent in a
    project that reader cannot see would escape his own limit entirely - silently, and in the
    direction that costs money.
    """

    def test_a_read_with_no_caller_answers_the_holders_whole_consumption(self, session) -> None:
        assert _batched(session, BOB_TOTAL, None) == Decimal("64.00")

    def test_omitting_the_argument_altogether_is_the_same_answer(self, session) -> None:
        """Enforcement paths and scheduled snapshots do not pass one, so the default must be the
        unnarrowed answer rather than a narrowing waiting for a scope nobody supplies."""
        holder_total = asyncio.run(SpendLogsRepository().sum_for_holders(session, [BOB_TOTAL], PERIOD))[BOB_TOTAL]
        assert holder_total == Decimal("64.00")

    def test_a_project_budgets_consumption_is_the_projects_and_not_the_readers(self, session) -> None:
        assert _batched(session, PROJECT_A, None) == Decimal("50.01")

    def test_none_and_everything_narrow_the_same_amount_which_is_nothing(self, session) -> None:
        """Spelled differently on purpose - one is *no caller*, the other a caller entitled to
        everything - so a read that merely forgot its scope stays findable."""
        assert _batched(session, BOB_TOTAL, None) == _batched(session, BOB_TOTAL, AccessScope.everything())


def _filtered(session, holder, access, report_filter) -> Decimal:
    return asyncio.run(SpendLogsRepository().sum_for_holders(session, [holder], PERIOD, access, report_filter))[holder]


class TestTheDashboardsFilterNarrowsTheHolderFiguresOfTheTwoReportTables:
    """The users- and projects-spending tables list rows Elasticsearch already narrowed by the dashboard's
    users and projects filter, so a figure read without it answers a wider question than its own row.

    **The defect, in its own numbers.** An admin filtering the dashboard to project A was shown bob at
    $64.00 - his spend across every project - beside summary cards reading $50.00 for the same filter.
    The filter is a second conjunct beside the boundary, never a replacement for it, and its absence is
    the budget answer `TestABudgetReadIsNotNarrowedAndThatIsTheWholePointOfTheDefault` pins.
    """

    def test_a_person_filtered_to_one_project_is_shown_their_spend_there_alone(self, session) -> None:
        assert _filtered(session, BOB_TOTAL, AccessScope.everything(), ReportFilter(projects=("A",))) == Decimal(
            "50.00"
        )

    def test_a_project_filtered_to_one_user_is_shown_that_users_share_alone(self, session) -> None:
        """alice's cent of project A's $50.01, which is the row the projects-spending table lists under a
        filter naming her."""
        about_alice = ReportFilter(users=(UserTerm(ALICE, frozenset()),))
        assert _filtered(session, PROJECT_A, AccessScope.everything(), about_alice) == Decimal("0.01")

    def test_the_filter_narrows_within_the_boundary_and_never_widens_it(self, session) -> None:
        """dave administers project A alone, so a filter naming project B shows him none of bob's $9.00 there."""
        assert _filtered(session, BOB_TOTAL, PROJECT_ADMIN_SCOPE, ReportFilter(projects=("B",))) == Decimal("0")

    def test_an_empty_filter_asks_about_the_whole_window(self, session) -> None:
        """A dashboard with nothing selected is the unfiltered figure, never a figure matching nothing."""
        assert _filtered(session, BOB_TOTAL, None, ReportFilter()) == Decimal("64.00")


class TestTheSingleHolderReadCannotDisagreeWithTheBatch:
    """The port's own equality, restated with a boundary in it: a batched figure must be what the
    single-holder read returns for the same holder, the same period **and the same boundary**."""

    @pytest.mark.parametrize(
        "access",
        [None, AccessScope.everything(), ALICE_SCOPE, PROJECT_ADMIN_SCOPE, NO_PROJECTS_SCOPE],
        ids=["no_caller", "unrestricted", "plain_member", "project_admin", "no_projects"],
    )
    @pytest.mark.parametrize("holder", [BOB_TOTAL, PROJECT_A], ids=["person_total", "project"])
    def test_both_paths_agree_under_every_boundary(self, session, holder, access) -> None:
        assert _single(session, holder, access) == _batched(session, holder, access)


class TestTheProjectResidueIsNarrowedToo:
    """A portion is money **and a row count** published beside a figure, so an unnarrowed one states
    the magnitude of spend the caller may not see - and over-labels their own figure with a hole none
    of their rows is in."""

    @staticmethod
    def _residue(session, access) -> tuple[Decimal, int]:
        return asyncio.run(SpendLogsRepository().project_dimension_residue(session, PERIOD, access))

    def test_unnarrowed_it_reports_every_project_less_row(self, session) -> None:
        """alice's and bob's personal-pot rows: $10.00 over two rows, none carrying a project."""
        assert self._residue(session, None) == (Decimal("10.00"), 2)

    def test_a_restricted_caller_is_told_of_their_own_hole_and_no_one_elses(self, session) -> None:
        """This portion counts rows carrying **no project**, and the only such rows a restricted scope
        admits are the caller's own pot: alice is told of her $5.00 and not of bob's, and dave, whose
        pot is empty, of nothing. Another person's pot never labels a restricted caller's figure,
        because an access boundary is *not your money* and never *missing money*."""
        assert self._residue(session, ALICE_SCOPE) == (Decimal("5.00"), 1)
        assert self._residue(session, PROJECT_ADMIN_SCOPE) == (Decimal("0"), 0)

    def test_an_unrestricted_caller_still_sees_the_whole_hole(self, session) -> None:
        assert self._residue(session, AccessScope.everything()) == (Decimal("10.00"), 2)


def _legacy_pot_row(request_id: str, principal: str, spend: str) -> Row:
    """A personal-pot row from below the identity floor: the principal the call ran under, no user id."""
    return Row(
        request_id=request_id,
        start_time=IN_PERIOD,
        end_time=IN_PERIOD,
        spend=Decimal(spend),
        status="success",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        attr_source="parsed",
        attr_form="principal",
        attr_project=None,
        attr_principal=principal,
        attr_category=BudgetCategory.PLATFORM.value,
        attr_user_id=None,
        codemie_request_id=request_id,
    )


class TestALegacyPotRowIsNotReachedThroughAnAlias:
    """Below the identity floor a pot row carries a principal and no user id. The boundary admits pot rows
    by user id alone: an alias names a person only probably - one person's username can be another's
    email - so admitting by it would show one caller another's legacy rows. Those rows stay outside."""

    ALICE_WITH_ALIASES = PersonTotalHolder(user_id=ALICE, aliases=frozenset({"alice", "alice@example.com"}))

    @pytest.fixture
    def legacy_session(self):
        engine = create_engine("sqlite://")
        Row.metadata.create_all(engine, tables=[Row.__table__])
        with Session(engine) as sync_session:
            sync_session.add_all(
                [
                    _project_row("alice-a", ALICE, "A", "0.01"),
                    _legacy_pot_row("alice-legacy", "alice@example.com", "2.00"),
                    _principal_row("dave-pot", DAVE, "1.00"),
                ]
            )
            sync_session.commit()
            yield _SyncSessionAsAsync(sync_session)

    def _residue(self, session, scope: AccessScope) -> tuple[Decimal, int]:
        return asyncio.run(SpendLogsRepository().project_dimension_residue(session, PERIOD, scope))

    def test_a_row_carrying_only_a_principal_is_outside_even_its_owners_boundary(self, legacy_session) -> None:
        assert _batched(legacy_session, self.ALICE_WITH_ALIASES, ALICE_SCOPE) == Decimal("0.01")
        assert _batched(legacy_session, self.ALICE_WITH_ALIASES, AccessScope.everything()) == Decimal("2.01")

    def test_no_legacy_pot_row_labels_a_restricted_callers_figure(self, legacy_session) -> None:
        assert self._residue(legacy_session, ALICE_SCOPE) == (Decimal("0"), 0)

    def test_a_project_admin_sees_their_own_pot_beside_their_projects(self, legacy_session) -> None:
        """The pot is the caller's whether they hold plain projects or only administer some."""
        assert self._residue(legacy_session, PROJECT_ADMIN_SCOPE) == (Decimal("1.00"), 1)


def _row(request_id: str, **attribution) -> Row:
    """One row in the period carrying exactly the attribution given."""
    return Row(
        request_id=request_id,
        start_time=IN_PERIOD,
        end_time=IN_PERIOD,
        spend=Decimal("1.25"),
        status="success",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        attr_category=BudgetCategory.PLATFORM.value,
        **attribution,
    )


@pytest.fixture
def every_match_path():
    """One row on each path a person holder's predicate can match, and nothing else.

    | row | matched by |
    | --- | --- |
    | own customer, principal form | alice's personal and total figures |
    | project-scoped, charged to bob's own customer (EPMCDME-15204) | bob's personal and total figures |
    | project-scoped, charged to the project-member customer | carol's total and member figures, not her personal one |
    | parsed principal carrying no user id | dave's alias only |
    """
    engine = create_engine("sqlite://")
    Row.metadata.create_all(engine, tables=[Row.__table__])
    with Session(engine) as sync_session:
        sync_session.add_all(
            [
                _row("own", attr_source="explicit", attr_form="principal", attr_user_id=ALICE, end_user="alice"),
                _row(
                    "charged-to-person",
                    attr_source="explicit",
                    attr_form="project_scoped",
                    attr_project="A",
                    attr_user_id=BOB,
                    end_user="bob",
                ),
                _row(
                    "member",
                    attr_source="explicit",
                    attr_form="project_scoped",
                    attr_project="A",
                    attr_user_id=CAROL,
                    end_user=f"codemie:project:A:category:platform:user:{CAROL}",
                ),
                _row("alias", attr_source="parsed", attr_form="principal", attr_principal="dave@example.com"),
            ]
        )
        sync_session.commit()
        yield _SyncSessionAsAsync(sync_session)


class TestABatchPastOneChunkAgreesWithTheSingleHolderReadOnEveryMatchPath:
    """Past one chunk a person holder can be answered zero by identity, without its predicate ever running.

    That zero is the one batched figure not computed by the expression the single-holder read evaluates, so
    the two are held equal over real rows on every path a person predicate can match - a pruning rule that
    drifted from a predicate zeroes real money here, with every statement still well-formed.
    """

    def test_every_holder_gets_the_single_holder_figure(self, every_match_path) -> None:
        people = {ALICE: None, BOB: None, CAROL: None, DAVE: frozenset({"dave", "dave@example.com"})}
        holders = [
            *(PersonalHolder(user, BudgetCategory.PLATFORM, aliases) for user, aliases in people.items()),
            *(PersonTotalHolder(user, aliases) for user, aliases in people.items()),
            ProjectMemberHolder(project="A", category=BudgetCategory.PLATFORM, user_id=CAROL),
            PROJECT_A,
            *(PersonalHolder(f"idle-{index}", BudgetCategory.PLATFORM, None) for index in range(HOLDER_BATCH_SIZE)),
        ]

        batched = asyncio.run(SpendLogsRepository().sum_for_holders(every_match_path, holders, PERIOD))

        assert batched == {holder: _single(every_match_path, holder, None) for holder in holders}
        assert sum(1 for figure in batched.values() if figure) == 9
