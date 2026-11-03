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

"""Which windowed ledger reads the repository answers from the hourly projection, and which it never does.

The projection is an exact copy of whole hours, so a read routed there prints the same figure as the
raw read - which is exactly why a routing mistake is invisible on a screen. Pinned here as compiled
SQL: a repository built without the flag issues today's statements unchanged; one built with it
answers projectable reads from `litellm_spend_hourly`; and a read naming a column the projection does
not carry, or a period holding no whole hour, falls back to the very statement the raw path issues.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from codemie.configs import Config
from codemie.enterprise.litellm import spend_ledger
from codemie.repository.spend_logs_repository import SpendLogsRepository, spend_logs_repository
from codemie.repository.spend_projection import read_statement
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import SpendDimension, TimeRange
from codemie.service.spend.ledger import (
    ComposedScope,
    DimensionScope,
    PersonalHolder,
    ProjectHolder,
    ReportFilter,
    UserTerm,
)
from codemie.service.spend.traffic_class import TrafficClass

#: Thirty whole days from midnight UTC, then a partial hour - whole hours to project and an edge to read raw.
PERIOD = TimeRange(datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 10, 1, 10, 17, tzinfo=timezone.utc))

#: Inside one clock hour, so no hour of it is whole and the projection has nothing to answer.
NO_WHOLE_HOUR = TimeRange(
    datetime(2026, 10, 1, 10, 5, tzinfo=timezone.utc), datetime(2026, 10, 1, 10, 40, tzinfo=timezone.utc)
)

PROJECTION_TABLE = "litellm_spend_hourly"

#: The raw rows the projected read takes back out and re-adds, bound as one array over every resolved id.
#: Spelled out whole because `= ANY` alone also matches the class predicates' `codemie_origin` and
#: `call_type` lists, which every projected statement carries whether or not any id reached it.
CORRECTION_OF_REQ_1_AND_REQ_2 = "litellm_spend_logs.codemie_request_id = ANY (ARRAY['req-1', 'req-2'])"


def sql(clause) -> str:
    """The clause as PostgreSQL would see it, with literals inlined so it can be asserted on."""
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).replace("\n", " ")


class _CapturingSession:
    """Records each statement and answers it with a row as wide as its own column list.

    Sized from the statement because the projected and raw statements of one read are captured on the
    same fake, and a fixed-width row would fail a `strict=True` zip for a reason unrelated to routing.
    """

    def __init__(self) -> None:
        self.statements: list = []
        self.executed_on: list[int] = []

    async def execute(self, statement):
        self.statements.append(statement)
        self.executed_on.append(threading.get_ident())
        row = (Decimal("0"),) * len(statement.selected_columns)

        class _Result:
            def one(self_inner):
                return row

            def scalar_one(self_inner):
                return row[0]

            def all(self_inner):
                """The bucketed read's accessor - no buckets is enough, only its statement is asserted."""
                return []

        return _Result()


async def issued_sql(repository: SpendLogsRepository, read) -> str:
    """The one statement `read` issues through `repository`, compiled."""
    session = _CapturingSession()
    await read(repository, session)
    (statement,) = session.statements
    return sql(statement)


def _spend_groups(repository, session, period=PERIOD):
    return repository.sum_for_spend_groups(session, period)


PROJECTABLE_READS = {
    "spend groups": _spend_groups,
    "project holders": lambda repository, session: repository.sum_for_holders(
        session, [ProjectHolder("A", BudgetCategory.PLATFORM), ProjectHolder("B", BudgetCategory.CLI)], PERIOD
    ),
    "assistant residue": lambda repository, session: repository.dimension_residue(
        session, PERIOD, DimensionScope(SpendDimension.ASSISTANT), ("req-1", "req-2")
    ),
    "workflow values": lambda repository, session: repository.sum_for_dimension_values(
        session, PERIOD, SpendDimension.WORKFLOW, {"wf-1": ("req-1",), "wf-2": ()}
    ),
    # A personal project is its owner's principal pot: identity and form columns, all projected (UD-H-D2).
    "spend groups filtered to a personal project": lambda repository, session: repository.sum_for_spend_groups(
        session,
        PERIOD,
        report_filter=ReportFilter(projects=("alice@example.com",), personal_project_owners=("alice",)),
    ),
    # A personal figure keys on the charged customer, which the projection carries as `end_user`.
    "personal holder": lambda repository, session: repository.sum_for_holder(
        session, PersonalHolder("alice", BudgetCategory.PLATFORM, frozenset({"alice@example.com"})), PERIOD
    ),
}

#: Reads whose resolved ids come from more than one disjoint set - every set must reach the correction.
UNION_READS = {
    "workflow values": lambda repository, session: repository.sum_for_dimension_values(
        session, PERIOD, SpendDimension.WORKFLOW, {"wf-1": ("req-1",), "wf-2": ("req-2",)}
    ),
    "composed scope by class": lambda repository, session: repository.sum_for_composed_scope_by_traffic_class(
        session,
        PERIOD,
        ComposedScope(user=UserTerm("u-1", None)),
        dimension_ids=("req-1",),
        ids_by_class={TrafficClass.ASSISTANT: ("req-2",)},
    ),
}

RAW_ONLY_READS = {
    # An execution value is matched on `codemie_request_id`, which the projection does not carry.
    "execution scope": lambda repository, session: repository.sum_for_dimension_scope(
        session, PERIOD, DimensionScope(SpendDimension.EXECUTION, "exec-1")
    ),
    "no whole hour": lambda repository, session: _spend_groups(repository, session, NO_WHOLE_HOUR),
}


class TestARepositoryBuiltWithoutTheFlagReadsOnlyRawRows:
    @pytest.mark.asyncio
    async def test_the_default_repository_never_names_the_projection(self) -> None:
        """Off by default, so every suite that builds `SpendLogsRepository()` keeps pinning today's SQL."""
        statement = await issued_sql(SpendLogsRepository(), _spend_groups)

        assert PROJECTION_TABLE not in statement
        assert "FROM litellm_spend_logs" in statement


class TestAnEnabledRepositoryAnswersProjectableReadsFromTheProjection:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("read", PROJECTABLE_READS.values(), ids=PROJECTABLE_READS.keys())
    async def test_a_read_over_projected_columns_reads_the_whole_hours_from_the_projection(self, read) -> None:
        """Every predicate here names only projected columns or resolved ids, so nothing forces raw rows."""
        statement = await issued_sql(SpendLogsRepository(projection=True), read)

        assert f"FROM {PROJECTION_TABLE}" in statement

    @pytest.mark.asyncio
    async def test_a_bucketed_class_split_corrects_resolved_ids_and_drops_empty_buckets(self) -> None:
        """The id set is bound as one array, and `HAVING` drops a bucket left only by a cancelled pair.

        Both are spellings only the projected statement has: the raw read binds `IN (...)` and returns
        a bucket only where some row exists, so their presence proves the read was not answered raw.
        """
        statement = await issued_sql(
            SpendLogsRepository(projection=True),
            lambda repository, session: repository.sum_by_time_bucket(
                session, PERIOD, "day", {TrafficClass.ASSISTANT: ("req-1", "req-2")}
            ),
        )

        assert f"FROM {PROJECTION_TABLE}" in statement
        assert CORRECTION_OF_REQ_1_AND_REQ_2 in statement
        assert "HAVING" in statement

    @pytest.mark.asyncio
    @pytest.mark.parametrize("read", UNION_READS.values(), ids=UNION_READS.keys())
    async def test_every_disjoint_id_set_a_read_applies_reaches_the_correction(self, read) -> None:
        """An id left out of the correction keeps its rows counted only through the projection.

        Such a row sits in the projection unlabelled, so the predicate that ids it never matches it
        there, and the figure drops it - silently, since the id was supplied and the read still ran.
        """
        statement = await issued_sql(SpendLogsRepository(projection=True), read)

        assert f"FROM {PROJECTION_TABLE}" in statement
        assert CORRECTION_OF_REQ_1_AND_REQ_2 in statement


class TestAnEnabledRepositoryFallsBackToTheRawStatement:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("read", RAW_ONLY_READS.values(), ids=RAW_ONLY_READS.keys())
    async def test_a_read_the_projection_cannot_answer_issues_exactly_the_raw_statement(self, read) -> None:
        """Identical to the flag-off SQL, not merely free of the projection - a fallback is today's read."""
        enabled = await issued_sql(SpendLogsRepository(projection=True), read)

        assert PROJECTION_TABLE not in enabled
        assert enabled == await issued_sql(SpendLogsRepository(), read)


class TestAnEnabledRepositoryBuildsItsStatementOffTheEventLoop:
    """A projected statement costs several times the raw one's CPU to build, so a worker thread builds it.

    Pinned by thread identity, not timing: the statement is built on another thread than the one that
    executes it, which is the test's own event-loop thread.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("period", "projected"), [(PERIOD, True), (NO_WHOLE_HOUR, False)], ids=["projected", "raw fallback"]
    )
    async def test_the_statement_is_built_on_a_worker_and_executed_on_the_loop(self, period, projected) -> None:
        """The fallback too: a projected read that falls back to raw rows is decided on the worker."""
        built_on: list[int] = []

        def recording_read_statement(*args):
            built_on.append(threading.get_ident())
            return read_statement(*args)

        session = _CapturingSession()
        with patch("codemie.repository.spend_logs_repository.read_statement", recording_read_statement):
            await _spend_groups(SpendLogsRepository(projection=True), session, period)

        loop_thread = threading.get_ident()
        assert built_on and built_on[0] != loop_thread
        assert session.executed_on == [loop_thread]
        assert (f"FROM {PROJECTION_TABLE}" in sql(session.statements[0])) is projected

    @pytest.mark.asyncio
    async def test_a_raw_repository_builds_its_statement_on_the_loop(self) -> None:
        """The flag-off path is today's read: no worker thread for a statement a quarter as costly."""
        with patch("codemie.repository.spend_logs_repository.asyncio.to_thread") as to_thread:
            await issued_sql(SpendLogsRepository(), _spend_groups)

        to_thread.assert_not_called()


class TestProjectedSpendGroupsKeepTheirColumnOrder:
    """The four cards are mapped back to `SpendGroup` by position, so a reordered column relabels a card."""

    @pytest.mark.asyncio
    async def test_the_projected_columns_carry_the_group_labels_in_enum_order(self) -> None:
        session = _CapturingSession()
        await SpendLogsRepository(projection=True).sum_for_spend_groups(session, PERIOD)
        (statement,) = session.statements

        assert f"FROM {PROJECTION_TABLE}" in sql(statement)
        assert list(statement.selected_columns.keys()) == ["group_0", "group_1", "group_2", "group_3"]

    @pytest.mark.asyncio
    async def test_each_group_label_sits_on_its_own_groups_selector(self) -> None:
        """A label pinned to the wrong expression relabels a card while the label order still holds.

        Bound by each group's own predicate: the total filters nothing, the platform card is what is
        *not* CLI, the CLI card falls back to the category and the embedding card to the call type.
        """
        session = _CapturingSession()
        await SpendLogsRepository(projection=True).sum_for_spend_groups(session, PERIOD)
        (statement,) = session.statements
        selectors = {label: sql(column) for label, column in statement.selected_columns.items()}

        assert "FILTER" not in selectors["group_0"]
        assert "attr_category IS DISTINCT FROM 'cli'" in selectors["group_1"]
        assert "attr_category = 'cli'" in selectors["group_2"]
        assert "call_type = ANY (ARRAY['embedding', 'aembedding'])" in selectors["group_3"]


class TestTheCompositionRootDecidesWhetherTheLedgerProjects:
    @pytest.mark.parametrize("enabled", [True, False])
    def test_the_ledger_reads_through_a_repository_built_from_the_setting(self, enabled: bool) -> None:
        """The setting is the rollback lever only if it reaches the repository the ledger reads through.

        Both values, so a root that hard-codes either one fails here.
        """
        with patch.object(spend_ledger.config, "SPEND_LEDGER_PROJECTION_ENABLED", enabled):
            ledger = spend_ledger.build_spend_logs_ledger(MagicMock(name="coverage"))

        assert ledger._repository._projection is enabled

    def test_the_module_singleton_reads_only_raw_rows(self) -> None:
        """Only the composition root opts into the projection; an importer of the singleton gets raw reads."""
        assert spend_logs_repository._projection is False

    def test_the_setting_is_on_by_default(self) -> None:
        """The approved rollout answers from the projection; turning it off is the opt-out."""
        assert Config.model_fields["SPEND_LEDGER_PROJECTION_ENABLED"].default is True
