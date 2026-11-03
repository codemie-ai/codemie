# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Unit tests for BudgetHandler — the two limit dashboards, computed from the ledger.

The previous version of this file asserted Elasticsearch aggregation shapes and call counts against
a source that no longer exists (the retired `attributes.spent` metric documents), so those tests are
re-baselined here rather than adapted. The rules under test now are: who is allowed to see the table,
which holders it lists, and what a figure that is absent or qualified renders as.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from itertools import count
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codemie.repository.user_repository import _UserFields
from codemie.rest_api.security.user import User
from codemie.service.analytics.access_filter import AccessFilter
from codemie.service.analytics.handlers import budget_handler as budget_handler_module
from codemie.service.analytics.handlers.budget_handler import CANDIDATE_LIMIT, BudgetHandler
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.identity_bridge import aliases_for_user
from codemie.service.spend.ledger import PersonalHolder

WINDOW_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 2, 1, tzinfo=timezone.utc)
WINDOW = TimeRange(WINDOW_START, WINDOW_END)

USER_ONE = _UserFields(id="11111111-1111-1111-1111-111111111111", email="one@example.com", username="one", name="One")
USER_TWO = _UserFields(id="22222222-2222-2222-2222-222222222222", email="two@example.com", username="two", name="Two")


def _budget(*, soft: float = 10.0, max_budget: float = 100.0, budget_id: str = "budget-1") -> SimpleNamespace:
    return SimpleNamespace(budget_id=budget_id, soft_budget=soft, max_budget=max_budget, deleted_at=None)


def _assignment(user_id: str, category: str = BudgetCategory.PLATFORM.value, budget_id: str = "budget-1"):
    return SimpleNamespace(user_id=user_id, category=category, budget_id=budget_id)


def _holder(record: _UserFields | None, user_id: str, category: BudgetCategory = BudgetCategory.PLATFORM):
    """The exact holder the handler builds, so a stub can key its answer on it."""
    aliases = aliases_for_user(record.username, record.email) if record is not None else None
    return PersonalHolder(user_id=user_id, category=category, aliases=aliases)


def _exact(amount: str) -> SpendResult:
    return SpendResult(figure=Decimal(amount))


def _partial(amount: str, missing: str = "5") -> SpendResult:
    """A real figure that is a floor: complete in money, incomplete in the project dimension."""
    return SpendResult(
        figure=Decimal(amount),
        partial={SpendDimension.PROJECT: Portion(spend=Decimal(missing), rows=2, sub_range=WINDOW)},
    )


def _no_figure() -> SpendResult:
    return SpendResult.no_figure(WINDOW)


#: AccessFilter caches its verdict per user id, so every caller in a test gets its own id.
_CALLER_IDS = count()


def _user(*, is_admin: bool = False, is_auditor: bool = False) -> MagicMock:
    user = MagicMock(spec=User)
    user.id = f"caller-{next(_CALLER_IDS)}"
    user.is_admin = is_admin
    user.is_auditor = is_auditor
    user.project_names = []
    user.admin_project_names = []
    return user


@pytest.fixture(autouse=True)
def clear_access_context_cache():
    """AccessFilter caches its verdict per user id on the class, which would leak between tests."""
    AccessFilter._context_cache.clear()
    yield
    AccessFilter._context_cache.clear()


@pytest.fixture
def logs(monkeypatch):
    """Record what the handler logged.

    The project's logging configuration does not propagate to `caplog`, so the module logger is
    replaced rather than captured.
    """
    recorded = SimpleNamespace(info=[], warning=[], debug=[])
    monkeypatch.setattr(
        budget_handler_module,
        "logger",
        SimpleNamespace(
            info=recorded.info.append,
            warning=recorded.warning.append,
            debug=recorded.debug.append,
        ),
    )
    return recorded


@pytest.fixture
def stub(monkeypatch):
    """Stand in for the database and the spend port, recording what the handler asked them."""
    state = SimpleNamespace(
        assignments=[],
        records={},
        consumed={},
        identifier_lookups=[],
        repository_calls=[],
        holder_reads=[],
    )

    @asynccontextmanager
    async def fake_session():
        yield "session"

    async def fake_list(session, *, user_ids, limit):
        state.repository_calls.append({"session": session, "user_ids": user_ids, "limit": limit})
        return state.assignments

    async def fake_find(session, identifiers, *args, **kwargs):
        state.identifier_lookups.append(set(identifiers))
        return {key: record for key, record in state.records.items() if key in identifiers}

    async def fake_consumed(holders, period):
        state.holder_reads.append((list(holders), period))
        return {holder: state.consumed[holder] for holder in holders if holder in state.consumed}

    monkeypatch.setattr(budget_handler_module, "get_async_session", fake_session)
    monkeypatch.setattr(budget_handler_module.budget_repository, "list_personal_assignments_with_budgets", fake_list)
    monkeypatch.setattr(budget_handler_module.user_repository, "afind_users_by_identifiers", fake_find)
    monkeypatch.setattr(budget_handler_module.spend_service, "consumed_for_holders", fake_consumed)
    return state


async def _soft_limit(user, **kwargs) -> dict:
    return await BudgetHandler(user).get_budget_soft_limit(start_date=WINDOW_START, end_date=WINDOW_END, **kwargs)


async def _hard_limit(user, **kwargs) -> dict:
    return await BudgetHandler(user).get_budget_hard_limit(start_date=WINDOW_START, end_date=WINDOW_END, **kwargs)


def _rows(response: dict) -> list[dict]:
    return response["data"]["rows"]


class TestAccessBoundary:
    """D4: the move from Elasticsearch to the database must not widen who can read this table."""

    @pytest.mark.asyncio
    async def test_admin_sees_every_breaching_holder(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _exact("42")}

        response = await _soft_limit(_user(is_admin=True))

        assert [row["user_email"] for row in _rows(response)] == ["one@example.com"]
        assert _rows(response)[0]["max_spent"] == 42.0

    @pytest.mark.asyncio
    async def test_auditor_counts_as_unrestricted(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _exact("42")}

        response = await _soft_limit(_user(is_auditor=True))

        assert len(_rows(response)) == 1

    @pytest.mark.asyncio
    async def test_plain_user_gets_no_rows_and_no_database_read(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _exact("42")}

        response = await _soft_limit(_user())

        assert _rows(response) == []
        assert response["pagination"]["total_count"] == 0
        # Not merely filtered out afterwards: the holders are never read at all.
        assert stub.repository_calls == []
        assert stub.holder_reads == []

    @pytest.mark.asyncio
    async def test_hard_limit_applies_the_same_boundary(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(max_budget=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _exact("42")}

        assert _rows(await _hard_limit(_user())) == []
        assert len(_rows(await _hard_limit(_user(is_admin=True)))) == 1


class TestFilters:
    """D5 and D6: what the projects and users filters select."""

    @pytest.mark.asyncio
    async def test_projects_filter_lists_no_holders(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _exact("42")}

        response = await _soft_limit(_user(is_admin=True), projects=["proj-a"])

        assert _rows(response) == []
        assert stub.repository_calls == []

    @pytest.mark.asyncio
    async def test_users_filter_narrows_the_candidate_set_to_resolved_ids(self, stub):
        stub.records = {"one@example.com": USER_ONE, USER_ONE.id: USER_ONE}
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _exact("42")}

        response = await _soft_limit(_user(is_admin=True), users=["one@example.com"])

        assert stub.repository_calls[0]["user_ids"] == [USER_ONE.id]
        assert [row["user_email"] for row in _rows(response)] == ["one@example.com"]

    @pytest.mark.asyncio
    async def test_no_users_filter_reads_every_holder(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _exact("42")}

        await _soft_limit(_user(is_admin=True))

        assert stub.repository_calls[0]["user_ids"] is None

    @pytest.mark.asyncio
    async def test_unresolvable_users_filter_lists_nothing_and_is_logged(self, stub, logs):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]

        response = await _soft_limit(_user(is_admin=True), users=["ghost@example.com"])

        assert _rows(response) == []
        assert stub.repository_calls == []
        assert any("ghost@example.com" in message for message in logs.info)


class TestWhichHoldersAreListed:
    """D2 and D3: the comparison, and the direction an unprovable comparison resolves in."""

    @pytest.mark.asyncio
    async def test_spend_equal_to_the_limit_is_listed(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _exact("10")}

        assert [row["max_spent"] for row in _rows(await _soft_limit(_user(is_admin=True)))] == [10.0]

    @pytest.mark.asyncio
    async def test_exact_figure_under_the_limit_is_excluded(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _exact("9.99")}

        response = await _soft_limit(_user(is_admin=True))

        assert _rows(response) == []
        # Nothing produced a figure, so the footer carries no money key at all.
        assert "max_spent" not in response["data"]["totals"]

    @pytest.mark.asyncio
    async def test_partial_figure_under_the_limit_is_listed_and_annotated(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _partial("9", missing="5")}

        row = _rows(await _soft_limit(_user(is_admin=True)))[0]

        assert row["max_spent"] == 9.0
        assert row["spend_partial"] is True
        assert row["spend_unavailable"] is False
        assert row["spend_missing_amount"] == 5.0

    @pytest.mark.asyncio
    async def test_truncated_figure_under_the_limit_is_listed_and_annotated(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): SpendResult(figure=Decimal("9"), unavailable=WINDOW)}

        row = _rows(await _soft_limit(_user(is_admin=True)))[0]

        assert row["max_spent"] == 9.0
        assert row["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_absent_figure_is_listed_with_no_number(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _no_figure()}

        row = _rows(await _soft_limit(_user(is_admin=True)))[0]

        # Never 0: a fabricated zero would claim the holder spent nothing.
        assert row["max_spent"] is None
        assert row["spend_unavailable"] is True
        assert row["spend_partial"] is False

    @pytest.mark.asyncio
    async def test_holder_the_port_never_answered_for_is_listed_with_no_number(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {}

        row = _rows(await _soft_limit(_user(is_admin=True)))[0]

        assert row["max_spent"] is None
        assert row["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_measured_zero_against_a_zero_limit_is_listed(self, stub):
        """A holder at a zero limit with a measured zero satisfies `spend >= limit`."""
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=0.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _exact("0")}

        row = _rows(await _soft_limit(_user(is_admin=True)))[0]

        assert row["max_spent"] == 0.0
        assert row["spend_unavailable"] is False


class TestLimitColumn:
    """Which limit each endpoint compares the same spend against."""

    @pytest.mark.asyncio
    async def test_soft_reads_soft_budget_and_hard_reads_max_budget(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0, max_budget=100.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _exact("50")}

        assert len(_rows(await _soft_limit(_user(is_admin=True)))) == 1
        assert _rows(await _hard_limit(_user(is_admin=True))) == []


class TestHolders:
    """What the port is asked about."""

    @pytest.mark.asyncio
    async def test_holder_carries_the_category_and_the_alias_set(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id, category=BudgetCategory.CLI.value), _budget(soft=10.0))]
        stub.records = {USER_ONE.id: USER_ONE}

        await _soft_limit(_user(is_admin=True))

        holders, period = stub.holder_reads[0]
        assert holders == [
            PersonalHolder(
                user_id=USER_ONE.id,
                category=BudgetCategory.CLI,
                aliases=frozenset({"one", "one@example.com"}),
            )
        ]
        # D1: the period is the requested analytics window, not a budget reset period.
        assert period == WINDOW

    @pytest.mark.asyncio
    async def test_unresolved_user_gets_no_alias_set_and_falls_back_to_the_id(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=10.0))]
        stub.records = {}
        stub.consumed = {_holder(None, USER_ONE.id): _exact("42")}

        row = _rows(await _soft_limit(_user(is_admin=True)))[0]

        holders, _ = stub.holder_reads[0]
        # None, not frozenset(): the person's fallback rows cannot be identified at all.
        assert holders[0].aliases is None
        assert row["user_email"] == USER_ONE.id

    @pytest.mark.asyncio
    async def test_unrecognised_category_never_reaches_a_holder(self, stub, logs):
        stub.assignments = [
            (_assignment(USER_ONE.id, category="not_a_category"), _budget(soft=10.0)),
            (_assignment(USER_TWO.id), _budget(soft=10.0)),
        ]
        stub.records = {USER_ONE.id: USER_ONE, USER_TWO.id: USER_TWO}
        stub.consumed = {_holder(USER_TWO, USER_TWO.id): _exact("42")}

        response = await _soft_limit(_user(is_admin=True))

        holders, _ = stub.holder_reads[0]
        assert [holder.user_id for holder in holders] == [USER_TWO.id]
        assert [row["user_email"] for row in _rows(response)] == ["two@example.com"]
        assert any("not_a_category" in message for message in logs.warning)


class TestOrderingAndFooter:
    """How the kept rows are ranked and what the footer says about them."""

    @pytest.mark.asyncio
    async def test_largest_figure_first_and_absent_figures_last(self, stub):
        third = _UserFields(
            id="33333333-3333-3333-3333-333333333333", email="three@example.com", username="three", name=None
        )
        stub.assignments = [
            (_assignment(USER_ONE.id), _budget(soft=1.0)),
            (_assignment(USER_TWO.id), _budget(soft=1.0)),
            (_assignment(third.id), _budget(soft=1.0)),
        ]
        stub.records = {USER_ONE.id: USER_ONE, USER_TWO.id: USER_TWO, third.id: third}
        stub.consumed = {
            _holder(USER_ONE, USER_ONE.id): _exact("10"),
            _holder(USER_TWO, USER_TWO.id): _no_figure(),
            _holder(third, third.id): _exact("30"),
        }

        rows = _rows(await _soft_limit(_user(is_admin=True)))

        assert [row["max_spent"] for row in rows] == [30.0, 10.0, None]

    @pytest.mark.asyncio
    async def test_footer_carries_the_annotations_and_never_a_money_total(self, stub):
        """The triple travels; the sum does not, at any figure.

        `TotalsCalculator.EXCLUSION_PATTERNS` matches `^max_.*`, so this table's `totals` were `{}`
        before the rewire. A sum here would be a **gained key** on a response whose shape the
        contract keeps — which is why this asserts the money key's absence even when every row has a
        figure, not only when none does.
        """
        stub.assignments = [
            (_assignment(USER_ONE.id), _budget(soft=1.0)),
            (_assignment(USER_TWO.id), _budget(soft=1.0)),
        ]
        stub.records = {USER_ONE.id: USER_ONE, USER_TWO.id: USER_TWO}
        stub.consumed = {
            _holder(USER_ONE, USER_ONE.id): _exact("10"),
            _holder(USER_TWO, USER_TWO.id): _partial("5", missing="2"),
        }

        totals = (await _soft_limit(_user(is_admin=True)))["data"]["totals"]

        assert "max_spent" not in totals
        assert totals["max_spent_spend_partial"] is True
        assert totals["max_spent_spend_missing_amount"] == 2.0

    @pytest.mark.asyncio
    async def test_footer_says_the_list_may_be_short_when_no_row_has_a_figure(self, stub):
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=1.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _no_figure()}

        totals = (await _soft_limit(_user(is_admin=True)))["data"]["totals"]

        # On a *who reached their limit* list the annotation says the list itself may be short: a
        # holder whose figure is unavailable might belong on it.
        assert "max_spent" not in totals
        assert totals["max_spent_spend_unavailable"] is True


class TestPaginationAndCap:
    """The page is sliced from the kept rows, and a truncated candidate read says so."""

    @pytest.mark.asyncio
    async def test_total_count_counts_every_kept_row_not_the_page(self, stub):
        stub.assignments = [
            (_assignment(USER_ONE.id), _budget(soft=1.0)),
            (_assignment(USER_TWO.id), _budget(soft=1.0)),
        ]
        stub.records = {USER_ONE.id: USER_ONE, USER_TWO.id: USER_TWO}
        stub.consumed = {
            _holder(USER_ONE, USER_ONE.id): _exact("10"),
            _holder(USER_TWO, USER_TWO.id): _exact("30"),
        }

        response = await _soft_limit(_user(is_admin=True), page=0, per_page=1)

        assert [row["max_spent"] for row in _rows(response)] == [30.0]
        assert response["pagination"] == {"page": 0, "per_page": 1, "total_count": 2, "has_more": True}

        last = await _soft_limit(_user(is_admin=True), page=1, per_page=1)
        assert [row["max_spent"] for row in _rows(last)] == [10.0]
        assert last["pagination"]["has_more"] is False

    @pytest.mark.asyncio
    async def test_candidate_cap_is_passed_down_and_logged_when_reached(self, stub, monkeypatch, logs):
        monkeypatch.setattr(budget_handler_module, "CANDIDATE_LIMIT", 1)
        stub.assignments = [(_assignment(USER_ONE.id), _budget(soft=1.0))]
        stub.records = {USER_ONE.id: USER_ONE}
        stub.consumed = {_holder(USER_ONE, USER_ONE.id): _exact("10")}

        await _soft_limit(_user(is_admin=True))

        assert stub.repository_calls[0]["limit"] == 1
        assert any("truncated" in message for message in logs.warning)

    def test_candidate_cap_matches_the_elasticsearch_pipeline(self):
        assert CANDIDATE_LIMIT == 10_000


class TestResponseShape:
    """The endpoints' contract is unchanged by the source move."""

    @pytest.mark.asyncio
    async def test_columns_and_metadata_are_reported(self, stub):
        response = await _soft_limit(_user(is_admin=True))

        assert response["data"]["columns"] == [
            {"id": "user_email", "label": "User", "type": "string"},
            {"id": "max_spent", "label": "Max Spent ($)", "type": "number", "format": "currency"},
        ]
        filters = response["metadata"]["filters_applied"]
        assert filters["time_period"] == "custom"
        assert filters["start_date"] == WINDOW_START.isoformat()
        assert filters["end_date"] == WINDOW_END.isoformat()

    def test_no_elasticsearch_source_remains_in_the_module(self):
        """AD-14: money leaves Elasticsearch entirely, so neither the pipeline nor its repository."""
        assert not hasattr(budget_handler_module, "AnalyticsQueryPipeline")
        assert not hasattr(budget_handler_module, "MetricsElasticRepository")
