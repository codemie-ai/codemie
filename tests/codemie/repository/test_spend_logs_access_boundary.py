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

"""The access boundary as **figures over real rows**, because a compiled predicate cannot show a leak.

Its neighbour `test_spend_logs_repository.py` asserts predicate *shape*, which is the right tool for a
dropped conjunct. It is the wrong tool for this defect: the statement that discloses another user's
spend is perfectly well-formed, and what is wrong about it is the number it returns. So these tests
run the real statements against a real SQL engine over a handful of rows and assert money.

**The scenario is the one that reached production.** alice is a plain member of project A; workflow W
is shared; alice ran it once for $0.01 and bob ran it five hundred times for $50.00. Elasticsearch
narrowed the table to alice's own executions and kept doing so; the money column moved to the ledger
and the *figure* stopped being narrowed, so alice's one row was footed with $50.01.

SQLite in memory rather than PostgreSQL: nothing under test here is dialect-specific - a sum, a
half-open range, an aggregate `FILTER` and the boundary's own `OR` - and a test that needs a server is
a test that does not run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from codemie.repository.grouped_rows import GroupedRows
from codemie.repository.spend_group_batch import SpendGroupBatch
from codemie.repository.spend_logs_model import LiteLLMSpendLog as Row
from codemie.repository.spend_logs_repository import SpendLogsRepository, report_filter_predicate
from codemie.service.spend.coverage import SpendDimension, TimeRange
from codemie.service.spend.ledger import AccessScope, DimensionScope, ReportFilter, UserTerm
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.traffic_class import TrafficClass

PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))
IN_PERIOD = datetime(2026, 8, 10, tzinfo=timezone.utc)

ALICE = "alice-1111-1111-1111-111111111111"
BOB = "bob-2222-2222-2222-222222222222"
WORKFLOW = "wf-shared"

#: alice's own scope: a plain member of project A and of nothing else.
ALICE_SCOPE = AccessScope(unrestricted=False, user_id=ALICE, plain_projects=("A",))

#: A caller entitled to no project at all - the case an empty collection is most dangerous in.
NO_PROJECTS_SCOPE = AccessScope(unrestricted=False, user_id="carol-3333")


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


def _row(
    request_id: str,
    user_id: str,
    project: str,
    spend: str,
    *,
    workflow: str | None = WORKFLOW,
    origin: str | None = None,
    category: str | None = None,
) -> Row:
    """One attributed, project-scoped spend row - the shape the access boundary is written against.

    `origin` and `category` default to NULL, which is the legacy shape every row here has always had:
    written before the sending release, so the class split reads it by its native columns alone.
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
        attr_source="explicit",
        attr_form="project_scoped",
        attr_project=project,
        attr_category=category,
        attr_user_id=user_id,
        codemie_workflow_id=workflow,
        codemie_origin=origin,
        codemie_request_id=request_id,
    )


@pytest.fixture
def session():
    """A fresh in-memory table holding the alice/bob scenario, plus one row in a third project.

    The extra project-B row is what makes the *admin-projects* half of the boundary observable: a
    boundary that ignored the project term entirely would still pass every alice/bob assertion.
    """
    engine = create_engine("sqlite://")
    Row.metadata.create_all(engine, tables=[Row.__table__])
    with Session(engine) as sync_session:
        sync_session.add_all(
            [
                _row("alice-run", ALICE, "A", "0.01"),
                *[_row(f"bob-run-{index}", BOB, "A", "0.10") for index in range(500)],
                _row("carol-run", "carol-3333", "B", "7.00"),
            ]
        )
        sync_session.commit()
        yield _SyncSessionAsAsync(sync_session)


def _figure(session, access, values=(WORKFLOW,)) -> dict[str, Decimal]:
    return asyncio.run(
        SpendLogsRepository().sum_for_dimension_values(
            session, PERIOD, SpendDimension.WORKFLOW, {value: () for value in values}, access
        )
    )


class TestARestrictedCallersFigureExcludesAnotherUsersSpend:
    """The disclosure, as money. This is the test the mutation check is aimed at."""

    def test_alice_sees_her_own_cent_and_not_bobs_fifty_dollars(self, session) -> None:
        assert _figure(session, ALICE_SCOPE)[WORKFLOW] == Decimal("0.01")

    def test_the_same_row_set_unnarrowed_is_the_leak_this_replaces(self, session) -> None:
        """Pinned so the number the defect produced is written down: the platform-wide $57.01 on this
        workflow, reported over alice's one row, on a table Elasticsearch had already narrowed to it.

        Every dollar here except alice's cent is money she may not see - bob's in her own project, and
        carol's in a project she is not a member of."""
        assert _figure(session, AccessScope.everything())[WORKFLOW] == Decimal("57.01")

    def test_bobs_own_figure_is_his_own_and_the_boundary_is_not_a_global_switch(self, session) -> None:
        bob_scope = AccessScope(unrestricted=False, user_id=BOB, plain_projects=("A",))
        assert _figure(session, bob_scope)[WORKFLOW] == Decimal("50.00")

    def test_an_admin_of_the_project_sees_every_members_spend_in_it(self, session) -> None:
        """The admin half carries no user term, which is the difference between the two clauses - so
        this must be the full project total rather than the admin's own rows."""
        project_admin = AccessScope(unrestricted=False, user_id="dave-4444", admin_projects=("A",))
        assert _figure(session, project_admin)[WORKFLOW] == Decimal("50.01")

    def test_a_project_the_caller_is_not_in_contributes_nothing(self, session) -> None:
        """carol's $7.00 sits in project B on the same workflow; alice is a member of A alone."""
        assert _figure(session, ALICE_SCOPE)[WORKFLOW] == Decimal("0.01")


class TestACallerEntitledToNothingGetsNothing:
    """The trap this design keeps returning to: an empty collection read as *unconstrained*."""

    def test_a_restricted_caller_with_no_accessible_project_sees_no_money(self, session) -> None:
        assert _figure(session, NO_PROJECTS_SCOPE)[WORKFLOW] == Decimal("0")

    def test_and_the_same_read_unnarrowed_proves_the_money_was_there_to_be_seen(self, session) -> None:
        """Without this half the assertion above passes on an empty table."""
        assert _figure(session, AccessScope.everything())[WORKFLOW] > Decimal("50")

    def test_a_caller_with_no_project_is_not_quietly_promoted_by_an_empty_value_set(self, session) -> None:
        """An empty *values* mapping asks nothing and must not become "ask about everything"."""
        assert _figure(session, NO_PROJECTS_SCOPE, values=()) == {}


class TestAnUnrestrictedCallerIsNarrowedByNothing:
    def test_an_admin_or_auditor_scope_sees_every_users_spend(self, session) -> None:
        """Every row on the workflow, across both projects and all three users."""
        assert _figure(session, AccessScope.everything())[WORKFLOW] == Decimal("57.01")

    def test_a_read_with_no_caller_at_all_is_also_unnarrowed(self, session) -> None:
        """`None` is a scheduled job or a budget path - no caller to narrow by. Spelled differently
        from `everything()` on purpose, so a read that merely forgot its scope stays findable, but
        both correctly narrow nothing."""
        assert _figure(session, None)[WORKFLOW] == Decimal("57.01")


DAVE = "dave-4444-4444-4444-444444444444"


def _personal_row(request_id: str, owner: str, spend: str) -> Row:
    """A personal-project call as the ledger stores it: principal form, keyed on the owner, no project."""
    row = _row(request_id, owner, "unused", spend)
    row.attr_form = "principal"
    row.attr_project = None
    return row


@pytest.fixture
def filtered_session():
    """The alice/bob/carol scenario plus dave's personal-project call on the same shared workflow."""
    engine = create_engine("sqlite://")
    Row.metadata.create_all(engine, tables=[Row.__table__])
    with Session(engine) as sync_session:
        sync_session.add_all(
            [
                _row("alice-run", ALICE, "A", "0.01"),
                *[_row(f"bob-run-{index}", BOB, "A", "0.10") for index in range(500)],
                _row("carol-run", "carol-3333", "B", "7.00"),
                _personal_row("dave-run", DAVE, "13.48"),
            ]
        )
        sync_session.commit()
        yield _SyncSessionAsAsync(sync_session)


class TestTheDimensionTableIsNarrowedByTheDashboardFilter:
    """RD-D1, against real rows: a dashboard's filter narrows a dimension table's figure and its residue.

    A value's rows - a shared workflow's column, an assistant's ids - sit in every project and belong
    to every person, so a filtered table read without the filter showed the value's spend everywhere and
    a residue sized on the whole window: live, "(at least; $23.31 unattributed)" under a $13.48 filter.
    """

    DAVES_PROJECT = ReportFilter(projects=("dave@example.com",), personal_project_owners=(DAVE,))

    @staticmethod
    def _figure(session, access, report_filter) -> Decimal:
        totals = asyncio.run(
            SpendLogsRepository().sum_for_dimension_values(
                session, PERIOD, SpendDimension.WORKFLOW, {WORKFLOW: ()}, access, report_filter
            )
        )
        return totals[WORKFLOW]

    @staticmethod
    def _residue(session, access, report_filter) -> tuple[Decimal, int]:
        return asyncio.run(
            SpendLogsRepository().dimension_residue(
                session, PERIOD, DimensionScope(SpendDimension.ASSISTANT), (), access, report_filter
            )
        )

    def test_a_shared_workflows_figure_is_its_spend_in_the_filtered_project_alone(self, filtered_session) -> None:
        everything = AccessScope.everything()
        assert self._figure(filtered_session, everything, None) == Decimal("70.49")
        assert self._figure(filtered_session, everything, ReportFilter(projects=("B",))) == Decimal("7.00")

    def test_the_residue_is_the_filtered_projects_own(self, filtered_session) -> None:
        assert self._residue(filtered_session, AccessScope.everything(), ReportFilter(projects=("B",))) == (
            Decimal("7.00"),
            1,
        )

    def test_a_personal_project_is_its_owners_pot_in_the_figure_and_the_residue(self, filtered_session) -> None:
        """The live RD-D1 case: a personal project's calls carry no project, so the filter reaches them
        through the owner's `users.id` - and the residue is that pot, not the window."""
        everything = AccessScope.everything()
        assert self._figure(filtered_session, everything, self.DAVES_PROJECT) == Decimal("13.48")
        assert self._residue(filtered_session, everything, self.DAVES_PROJECT) == (Decimal("13.48"), 1)

    def test_the_filter_never_widens_a_boundary_to_another_users_personal_project(self, filtered_session) -> None:
        assert self._figure(filtered_session, ALICE_SCOPE, self.DAVES_PROJECT) == Decimal("0")
        assert self._residue(filtered_session, ALICE_SCOPE, self.DAVES_PROJECT) == (Decimal("0"), 0)

    def test_an_empty_filter_is_the_whole_window_like_none(self, filtered_session) -> None:
        everything = AccessScope.everything()
        assert self._residue(filtered_session, everything, ReportFilter()) == self._residue(
            filtered_session, everything, None
        )


class TestTheResidueIsNarrowedToo:
    """Narrowing only the figure leaves the residue publishing the magnitude of spend the caller may
    not see - and over-labelling their own figure with a hole none of their rows is in."""

    @staticmethod
    def _residue(session, access) -> tuple[Decimal, int]:
        return asyncio.run(
            SpendLogsRepository().dimension_residue(
                session, PERIOD, DimensionScope(SpendDimension.ASSISTANT), (), access
            )
        )

    def test_alices_residue_holds_her_row_alone(self, session) -> None:
        """The assistant dimension has no ledger column, so every row in range is unbucketable and the
        residue is exactly the row set the caller may see."""
        spend, rows = self._residue(session, ALICE_SCOPE)
        assert rows == 1
        assert spend == Decimal("0.01")

    def test_an_unnarrowed_residue_would_report_five_hundred_rows_of_someone_elses_money(self, session) -> None:
        spend, rows = self._residue(session, AccessScope.everything())
        assert rows == 502
        assert spend == Decimal("57.01")

    def test_a_caller_entitled_to_nothing_is_told_of_no_hole_at_all(self, session) -> None:
        """Zero rows, so `_dimension_label` adds no portion - correctly, because an access boundary is
        *not your money* and never *missing money*: labelling it would tell the caller that something
        they may not see is absent."""
        spend, rows = self._residue(session, NO_PROJECTS_SCOPE)
        assert (spend, rows) == (Decimal("0"), 0)

    def test_a_resolved_id_leaves_the_residue_on_both_sides_of_the_boundary(self, session) -> None:
        """The boundary composes with the residue's own rule rather than replacing it: alice's one row
        is resolved, so her residue empties while the platform's does not."""
        narrowed = asyncio.run(
            SpendLogsRepository().dimension_residue(
                session, PERIOD, DimensionScope(SpendDimension.ASSISTANT), ("alice-run",), ALICE_SCOPE
            )
        )
        assert narrowed == (Decimal("0"), 0)

        unnarrowed = asyncio.run(
            SpendLogsRepository().dimension_residue(
                session, PERIOD, DimensionScope(SpendDimension.ASSISTANT), ("alice-run",), AccessScope.everything()
            )
        )
        assert unnarrowed == (Decimal("57.00"), 501)


@pytest.fixture
def unclassified_session():
    """The same scenario with the workflow id dropped, so **no** traffic class claims a row.

    The residue's own predicate excludes a workflow row - that row belongs to the workflow series and
    is not missing from anything - so the fixture above would report an empty residue whatever the
    boundary did, and a narrowing that did nothing at all would pass.
    """
    engine = create_engine("sqlite://")
    Row.metadata.create_all(engine, tables=[Row.__table__])
    with Session(engine) as sync_session:
        sync_session.add_all(
            [
                _row("alice-run", ALICE, "A", "0.01", workflow=None),
                *[_row(f"bob-run-{index}", BOB, "A", "0.10", workflow=None) for index in range(500)],
                _row("carol-run", "carol-3333", "B", "7.00", workflow=None),
            ]
        )
        sync_session.commit()
        yield _SyncSessionAsAsync(sync_session)


#: A word the origin vocabulary does not hold - what a later sender, or a defect, puts in the column.
UNKNOWN_ORIGIN = "telemetry_sweep"


@pytest.fixture
def origin_session():
    """One row per way the origin column and the native columns can meet, as money.

    Each row is a case a predicate can get wrong while still returning a plausible figure: a CLI
    session billed to a premium budget, a workflow run whose id never reached the ledger, a proxied
    call no class claims, a word this vocabulary does not hold on a row a native column *would* have
    claimed, and two legacy rows carrying no origin at all.
    """
    engine = create_engine("sqlite://")
    Row.metadata.create_all(engine, tables=[Row.__table__])
    with Session(engine) as sync_session:
        sync_session.add_all(
            [
                _row("cli-premium", ALICE, "A", "3.00", workflow=None, origin="cli", category="premium_models"),
                _row("workflow-origin", ALICE, "A", "5.00", workflow=None, origin="workflow"),
                _row("proxied", ALICE, "A", "7.00", workflow=None, origin="proxy"),
                _row("unknown-word", ALICE, "A", "11.00", origin=UNKNOWN_ORIGIN),
                _row("legacy-workflow", ALICE, "A", "13.00"),
                _row("legacy-cli", ALICE, "A", "0.50", workflow=None, category="cli"),
            ]
        )
        sync_session.commit()
        yield _SyncSessionAsAsync(sync_session)


class TestTheOriginColumnDecidesWhereMoneyLands:
    """The class split and the summary cards over rows that carry an origin, as figures.

    A compiled predicate cannot show any of these failures: every wrong answer here is a well-formed
    statement returning a plausible number in the wrong series.
    """

    @staticmethod
    def _cards(session) -> dict[SpendGroup, Decimal]:
        return asyncio.run(SpendLogsRepository().sum_for_spend_groups(session, PERIOD, AccessScope.everything()))

    @staticmethod
    def _classes(session) -> dict[TrafficClass, Decimal]:
        return asyncio.run(SpendLogsRepository().sum_for_traffic_classes(session, PERIOD))

    @staticmethod
    def _residue(session) -> tuple[Decimal, int]:
        return asyncio.run(SpendLogsRepository().unclassified_residue(session, PERIOD, (), AccessScope.everything()))

    def test_a_cli_session_billed_to_a_premium_budget_is_cli_money(self, origin_session) -> None:
        """The row the column was added for. `attr_category` says which budget paid - premium - and the
        origin says what the request was; before the column, this money was invisible to every CLI
        report because the category was the only word the row had for CLI."""
        cards = self._cards(origin_session)

        assert cards[SpendGroup.CLI] == Decimal("3.50"), "the premium-billed CLI row plus the legacy one"
        assert cards[SpendGroup.PLATFORM] == Decimal("36.00")

    def test_the_cards_still_foot_with_a_cli_row_the_category_calls_premium(self, origin_session) -> None:
        """`TOTAL = PLATFORM + CLI` is a partition, so the row has to leave the platform card as it
        enters the CLI one - a platform card keyed on the category alone would count it in both."""
        cards = self._cards(origin_session)

        assert cards[SpendGroup.TOTAL] == Decimal("39.50")
        assert cards[SpendGroup.TOTAL] == cards[SpendGroup.PLATFORM] + cards[SpendGroup.CLI]

    def test_a_workflow_origin_row_is_workflow_money_without_a_workflow_id(self, origin_session) -> None:
        """The origin is what the caller said it was doing; the id is a correlation key that a thread
        may never have carried. Waiting for the id would leave this money in the residue."""
        assert self._classes(origin_session)[TrafficClass.WORKFLOW] == Decimal("18.00")

    def test_a_word_this_vocabulary_does_not_hold_is_residue_even_over_a_native_column(self, origin_session) -> None:
        """The unknown-word row carries a workflow id, so the pre-origin selector would have filed it
        under the workflow series. Its sender said otherwise in a word we do not know, and a defect we
        can see is worth more than a figure we guessed: 18.00 is the proxied 7.00 plus this 11.00, and
        the workflow series above is 18.00 without it."""
        assert self._residue(origin_session) == (Decimal("18.00"), 2)

    def test_a_row_carrying_no_origin_is_classified_by_its_native_columns_exactly_as_before(
        self, origin_session
    ) -> None:
        """Every row written before the sending release is this shape, and nothing about it changed."""
        classes = self._classes(origin_session)

        assert classes[TrafficClass.WORKFLOW] - Decimal("5.00") == Decimal("13.00"), "the legacy workflow row"
        assert classes[TrafficClass.CLI] - Decimal("3.00") == Decimal("0.50"), "the legacy CLI row"

    def test_the_four_classes_and_the_residue_still_foot_to_the_window_total(self, origin_session) -> None:
        """The invariant the origin branch could break in either direction: a class widened by an
        origin it does not own double-counts, and one narrowed by an origin it does own loses money
        into the residue. Only the sum of all five can say neither happened."""
        classes = self._classes(origin_session)
        residue, _ = self._residue(origin_session)

        assert sum(classes.values()) + residue == self._cards(origin_session)[SpendGroup.TOTAL]


class TestTheSummaryCardsAreNarrowedToo:
    """The four cards, as money on each side of the boundary.

    A card read is one scan of four aggregates, so a boundary missing from its `WHERE` misses all
    four at once: a plain member is shown the platform's total on the screen that names their own
    projects. The cards also have to foot - `TOTAL = PLATFORM + CLI` - on **either** side of it,
    because a boundary applied to some columns and not others is the same screen not adding up.
    """

    @staticmethod
    def _cards(session, access, report_filter=None) -> dict[SpendGroup, Decimal]:
        return asyncio.run(SpendLogsRepository().sum_for_spend_groups(session, PERIOD, access, report_filter))

    def test_alices_total_is_her_own_cent(self, session) -> None:
        assert self._cards(session, ALICE_SCOPE)[SpendGroup.TOTAL] == Decimal("0.01")

    def test_the_same_cards_unnarrowed_are_the_whole_platforms(self, session) -> None:
        """The leak, as the number it would print: 57.01 on a screen entitled to a cent."""
        assert self._cards(session, AccessScope.everything())[SpendGroup.TOTAL] == Decimal("57.01")

    def test_the_cards_foot_against_each_other_on_both_sides_of_the_boundary(self, session) -> None:
        for access in (ALICE_SCOPE, AccessScope.everything()):
            cards = self._cards(session, access)
            assert cards[SpendGroup.TOTAL] == cards[SpendGroup.PLATFORM] + cards[SpendGroup.CLI]

    def test_an_unattributed_category_lands_in_the_platform_card_and_not_only_in_the_total(self, session) -> None:
        """`<> 'cli'` is *unknown* on these rows, which carry no category at all, so a bare inequality
        would print a platform card of zero beside a total of 57.01."""
        cards = self._cards(session, AccessScope.everything())

        assert cards[SpendGroup.PLATFORM] == Decimal("57.01")
        assert cards[SpendGroup.CLI] == Decimal("0")

    def test_a_caller_entitled_to_nothing_is_shown_nothing_on_every_card(self, session) -> None:
        cards = self._cards(session, NO_PROJECTS_SCOPE)

        assert set(cards) == set(SpendGroup)
        assert all(figure == Decimal("0") for figure in cards.values())

    def test_the_filter_narrows_within_the_boundary_and_never_widens_past_it(self, session) -> None:
        """Two narrowings, ANDed: asking about bob does not get alice bob's money, and asking about
        alice inside her own boundary still leaves her her cent."""
        about_bob = ReportFilter(users=(UserTerm(BOB, frozenset()),))
        about_alice = ReportFilter(users=(UserTerm(ALICE, frozenset()),))

        assert self._cards(session, ALICE_SCOPE, about_bob)[SpendGroup.TOTAL] == Decimal("0")
        assert self._cards(session, ALICE_SCOPE, about_alice)[SpendGroup.TOTAL] == Decimal("0.01")
        assert self._cards(session, AccessScope.everything(), about_bob)[SpendGroup.TOTAL] == Decimal("50.00")


class TestTheClassSplitResidueIsNarrowedToo:
    """The residue of a class split, as money. Its rule is `dimension_residue`'s, over other rows.

    Unnarrowed it publishes the magnitude of spend the caller may not see; narrowed on the figure
    alone it labels their own figure with a hole none of their rows is in.
    """

    @staticmethod
    def _residue(session, access, resolved_ids=(), report_filter=None) -> tuple[Decimal, int]:
        return asyncio.run(
            SpendLogsRepository().unclassified_residue(session, PERIOD, resolved_ids, access, report_filter)
        )

    def test_alices_residue_holds_her_row_alone(self, unclassified_session) -> None:
        assert self._residue(unclassified_session, ALICE_SCOPE) == (Decimal("0.01"), 1)

    def test_an_unnarrowed_residue_would_report_five_hundred_rows_of_someone_elses_money(
        self, unclassified_session
    ) -> None:
        assert self._residue(unclassified_session, AccessScope.everything()) == (Decimal("57.01"), 502)

    def test_a_caller_entitled_to_nothing_is_told_of_no_hole_at_all(self, unclassified_session) -> None:
        assert self._residue(unclassified_session, NO_PROJECTS_SCOPE) == (Decimal("0"), 0)

    def test_a_row_a_class_claims_is_not_in_the_residue_at_all(self, session) -> None:
        """Every row in the base fixture carries a workflow id, so the workflow series claims them and
        the window is complete. A residue defined as *the assistant class's unresolved rows* would
        report all 502 of them as money missing from one series instead."""
        assert self._residue(session, AccessScope.everything()) == (Decimal("0"), 0)

    def test_a_resolved_id_leaves_the_residue_on_both_sides_of_the_boundary(self, unclassified_session) -> None:
        """The boundary composes with the residue's own rule rather than replacing it."""
        assert self._residue(unclassified_session, ALICE_SCOPE, ("alice-run",)) == (Decimal("0"), 0)
        assert self._residue(unclassified_session, AccessScope.everything(), ("alice-run",)) == (Decimal("57.00"), 501)

    def test_the_filter_narrows_the_residue_the_way_it_narrows_the_figure(self, unclassified_session) -> None:
        """A residue carrying only the boundary over-labels a filtered dashboard with rows it is not
        showing - and a figure and its label narrowed differently never agree."""
        about_bob = ReportFilter(users=(UserTerm(BOB, frozenset()),))

        assert self._residue(unclassified_session, AccessScope.everything(), (), about_bob) == (Decimal("50.00"), 500)


ALICE_ALIASES = frozenset({"alice@example.com", "alice"})
BOB_ALIASES = frozenset({"bob@example.com"})
ATTRIBUTION_KEY = ("request_id", "attr_user_id", "attr_source", "attr_form", "attr_principal", "attr_project")


def _parsed_principal_row(request_id: str, principal: str) -> Row:
    """A fallback-parsed call: charged to the person's own customer, known only by `users.username` or email."""
    row = _row(request_id, "unused", "unused", "0.50")
    row.attr_source = "parsed"
    row.attr_form = "principal"
    row.attr_user_id = None
    row.attr_project = None
    row.attr_principal = principal
    return row


@contextmanager
def _seeded(rows: list[Row]) -> Iterator[_SyncSessionAsAsync]:
    """An in-memory ledger holding exactly `rows`."""
    engine = create_engine("sqlite://")
    Row.metadata.create_all(engine, tables=[Row.__table__])
    with Session(engine) as sync_session:
        sync_session.add_all(rows)
        sync_session.commit()
        yield _SyncSessionAsAsync(sync_session)


def _attributed() -> list[Row]:
    """Every shape a report filter's predicate tells apart: explicit, parsed, personal and shared-project rows."""
    return [
        _row("alice-run", ALICE, "A", "0.01"),
        _row("alice-in-b", ALICE, "B", "0.02"),
        _row("bob-run", BOB, "A", "0.10"),
        _row("carol-run", "carol-3333", "B", "7.00"),
        _personal_row("dave-run", DAVE, "13.48"),
        _personal_row("bob-personal", BOB, "1.00"),
        _parsed_principal_row("alice-by-email", "alice@example.com"),
        _parsed_principal_row("alice-by-username", "alice"),
        _parsed_principal_row("bob-by-email", "bob@example.com"),
        _parsed_principal_row("stranger", "stranger@example.com"),
    ]


@pytest.fixture
def attributed_rows() -> Iterator[tuple[_SyncSessionAsAsync, list[Row]]]:
    rows = _attributed()
    with _seeded(rows) as session:
        yield session, rows


class TestTheEvaluatorSelectsWhatTheFilterSelects:
    """AC-12: evaluating a filter's predicate over grouped rows picks the rows SQL picks for the same predicate.

    Each ledger row is its own group, keyed by its request id and the five attribution columns, so the
    set of matching groups is directly the set of request ids the `WHERE` returns.
    """

    @pytest.mark.parametrize(
        ("report_filter", "expected"),
        [
            pytest.param(
                ReportFilter(users=(UserTerm(ALICE, ALICE_ALIASES),)),
                {"alice-run", "alice-in-b", "alice-by-email", "alice-by-username"},
                id="one-user",
            ),
            pytest.param(
                ReportFilter(users=(UserTerm(ALICE, ALICE_ALIASES), UserTerm(BOB, BOB_ALIASES))),
                {
                    "alice-run",
                    "alice-in-b",
                    "alice-by-email",
                    "alice-by-username",
                    "bob-run",
                    "bob-personal",
                    "bob-by-email",
                },
                id="several-users",
            ),
            pytest.param(
                ReportFilter(users=(UserTerm(ALICE, None),)), {"alice-run", "alice-in-b"}, id="aliases-unresolved"
            ),
            pytest.param(
                ReportFilter(users=(UserTerm(ALICE, frozenset()),)), {"alice-run", "alice-in-b"}, id="no-aliases"
            ),
            pytest.param(ReportFilter(projects=("A",)), {"alice-run", "bob-run"}, id="project-without-personal-owners"),
            pytest.param(
                ReportFilter(projects=("A", "B")),
                {"alice-run", "alice-in-b", "bob-run", "carol-run"},
                id="several-projects",
            ),
            pytest.param(
                ReportFilter(projects=("dave@example.com",), personal_project_owners=(DAVE,)),
                {"dave-run"},
                id="one-personal-owner",
            ),
            pytest.param(
                ReportFilter(
                    projects=("A", "dave@example.com", "bob@example.com"), personal_project_owners=(DAVE, BOB)
                ),
                {"alice-run", "bob-run", "dave-run", "bob-personal"},
                id="several-personal-owners",
            ),
            pytest.param(
                ReportFilter(projects=("A",), personal_project_owners=None),
                {"alice-run", "bob-run"},
                id="owners-unknown",
            ),
            pytest.param(
                ReportFilter(users=(UserTerm(ALICE, ALICE_ALIASES),), projects=("A",), personal_project_owners=()),
                {"alice-run"},
                id="user-and-project",
            ),
            pytest.param(
                ReportFilter(
                    users=(UserTerm(BOB, BOB_ALIASES),),
                    projects=("A", "bob@example.com"),
                    personal_project_owners=(BOB,),
                ),
                {"bob-run", "bob-personal"},
                id="user-and-personal-project",
            ),
            pytest.param(
                ReportFilter(users=(UserTerm(ALICE, None),), projects=("B",), personal_project_owners=None),
                {"alice-in-b"},
                id="user-and-project-both-unresolved",
            ),
        ],
    )
    def test_agrees_with_the_where_clause(self, attributed_rows, report_filter: ReportFilter, expected) -> None:
        session, rows = attributed_rows
        predicate = report_filter_predicate(report_filter)
        grouped = GroupedRows([tuple(getattr(row, name) for name in ATTRIBUTION_KEY) for row in rows], ATTRIBUTION_KEY)

        selected_by_sql = set(asyncio.run(session.execute(select(Row.request_id).where(predicate))).scalars())
        selected_by_evaluator = {rows[index].request_id for index in grouped.matching(predicate)}

        assert selected_by_sql == expected
        assert selected_by_evaluator == expected


@pytest.fixture
def spend_group_rows() -> Iterator[_SyncSessionAsAsync]:
    """The attributed rows plus two CLI calls in project A, so a filter's platform and CLI figures differ."""
    cli_calls = [
        _row("alice-cli", ALICE, "A", "0.40", category="cli"),
        _row("bob-cli", BOB, "A", "0.30", category="cli"),
    ]
    with _seeded([*_attributed(), *cli_calls]) as session:
        yield session


#: Explicit id, alias fallback, aliases unresolved, and an alias ("alice") bob's term shares with alice's.
USER_TERMS = (
    UserTerm(ALICE, frozenset()),
    UserTerm(ALICE, ALICE_ALIASES),
    UserTerm(BOB, None),
    UserTerm(BOB, BOB_ALIASES | {"alice"}),
)

#: A shared project, a personal-project pot, and owners nobody could look up.
PROJECT_TERMS = (
    {"projects": ("A",)},
    {"projects": ("dave@example.com",), "personal_project_owners": (DAVE,)},
    {"projects": ("B",), "personal_project_owners": None},
)

NO_COMMON_TERM = ReportFilter()
SHARED_PROJECTS = ReportFilter(projects=("A", "bob@example.com"), personal_project_owners=(BOB,))
SHARED_USERS = ReportFilter(users=(UserTerm(ALICE, ALICE_ALIASES),))


def _user_rows(common: ReportFilter) -> tuple[ReportFilter, ...]:
    return tuple(replace(common, users=(term,)) for term in USER_TERMS)


def _project_rows(common: ReportFilter) -> tuple[ReportFilter, ...]:
    return tuple(replace(common, **term) for term in PROJECT_TERMS)


class TestTheBatchedSpendGroupReadEqualsTheSingleRead:
    """AC-1: one grouped read, assigned per filter in Python, gives each filter its own single-read figures.

    Every filter carries the batch's common term unchanged, as the per-row tables build them, so the
    grouped read's `WHERE` admits every row any filter of the batch can match.
    """

    @pytest.mark.parametrize(
        ("access", "common_filter", "report_filters", "totals"),
        [
            pytest.param(
                AccessScope.everything(),
                NO_COMMON_TERM,
                _user_rows(NO_COMMON_TERM),
                ("0.43", "1.43", "1.40", "2.40"),
                id="user-rows-unrestricted",
            ),
            pytest.param(
                AccessScope.everything(),
                SHARED_PROJECTS,
                _user_rows(SHARED_PROJECTS),
                ("0.41", "0.41", "1.40", "1.40"),
                id="user-rows-sharing-a-projects-term-unrestricted",
            ),
            pytest.param(
                ALICE_SCOPE,
                NO_COMMON_TERM,
                _user_rows(NO_COMMON_TERM),
                ("0.41", "0.41", "0", "0"),
                id="user-rows-restricted",
            ),
            pytest.param(
                ALICE_SCOPE,
                SHARED_PROJECTS,
                _user_rows(SHARED_PROJECTS),
                ("0.41", "0.41", "0", "0"),
                id="user-rows-sharing-a-projects-term-restricted",
            ),
            pytest.param(
                AccessScope.everything(),
                NO_COMMON_TERM,
                _project_rows(NO_COMMON_TERM),
                ("0.81", "13.48", "7.02"),
                id="project-rows-unrestricted",
            ),
            pytest.param(
                AccessScope.everything(),
                SHARED_USERS,
                _project_rows(SHARED_USERS),
                ("0.41", "0", "0.02"),
                id="project-rows-sharing-a-users-term-unrestricted",
            ),
            pytest.param(
                ALICE_SCOPE,
                NO_COMMON_TERM,
                _project_rows(NO_COMMON_TERM),
                ("0.41", "0", "0"),
                id="project-rows-restricted",
            ),
            pytest.param(
                ALICE_SCOPE,
                SHARED_USERS,
                _project_rows(SHARED_USERS),
                ("0.41", "0", "0"),
                id="project-rows-sharing-a-users-term-restricted",
            ),
        ],
    )
    def test_every_filter_gets_the_figures_its_own_read_gives(
        self, spend_group_rows, access, common_filter, report_filters, totals
    ) -> None:
        repository = SpendLogsRepository()
        batch = SpendGroupBatch.of(report_filters)

        rows = asyncio.run(
            repository.sum_for_spend_groups_per_attribution(
                spend_group_rows, PERIOD, batch.group_key, access=access, common_filter=common_filter
            )
        )
        figures = batch.figures(rows)

        singles = {
            report_filter: asyncio.run(repository.sum_for_spend_groups(spend_group_rows, PERIOD, access, report_filter))
            for report_filter in report_filters
        }
        assert figures == singles
        assert tuple(figures[report_filter][SpendGroup.TOTAL] for report_filter in report_filters) == tuple(
            map(Decimal, totals)
        )

    def test_a_filter_no_grouped_row_matches_gets_zero_on_every_group(self, spend_group_rows) -> None:
        nobody = ReportFilter(users=(UserTerm("nobody", frozenset()),))
        batch = SpendGroupBatch.of([nobody])

        rows = asyncio.run(
            SpendLogsRepository().sum_for_spend_groups_per_attribution(spend_group_rows, PERIOD, batch.group_key)
        )

        assert batch.figures(rows) == {nobody: dict.fromkeys(SpendGroup, Decimal("0"))}
