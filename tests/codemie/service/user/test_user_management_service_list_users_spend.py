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

"""Unit tests for the consumed amounts list_users reports per budget assignment.

The figures come from the spend port, and the sharpest rule under test is both directions of the
absent-figure rule: an absent figure stays absent (never 0.0), while a measured zero renders as 0.0.
"""

from datetime import datetime, UTC
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from codemie.rest_api.models.user_management import UserBudgetAssignmentInfo, UserDB
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.ledger import PersonalHolder
from codemie.service.user.user_management_service import UserManagementService

_RESET_AT = "2099-02-01T00:00:00+00:00"
_DURATION = "30d"


@pytest.fixture
def mock_session():
    return MagicMock()


@pytest.fixture(autouse=True)
def refresh_overdue():
    """The reset reconciliation moves nothing unless a test says otherwise - it is a provider call."""
    with patch(
        "codemie.service.user.user_management_service.daily_budget_reset_reconciliation_service.refresh_overdue",
        new_callable=AsyncMock,
        return_value=set(),
    ) as refresh:
        yield refresh


def _user(email: str = "test@example.com", username: str = "testuser") -> UserDB:
    return UserDB(
        id=str(uuid4()),
        username=username,
        email=email,
        name="Test User",
        password_hash="hashed_password",
        auth_source="local",
        user_type="regular",
        is_active=True,
        is_admin=False,
        email_verified=True,
        date=datetime.now(UTC),
        update_date=datetime.now(UTC),
    )


def _assignment(
    user_id: str,
    category: str = "platform",
    budget_id: str = "budget-1",
    spend_reset_at: datetime | None = None,
) -> MagicMock:
    return MagicMock(user_id=user_id, category=category, budget_id=budget_id, spend_reset_at=spend_reset_at)


def _budget(reset_at: str | None = _RESET_AT, duration: str | None = _DURATION) -> MagicMock:
    budget = MagicMock(
        budget_id="budget-1",
        max_budget=100.0,
        budget_duration=duration,
        budget_reset_at=reset_at,
        pinned_reset_at=None,
        pinned_period_start=None,
    )
    budget.name = "Budget"  # `name` is a MagicMock constructor keyword, so it must be set after
    return budget


def _holder(user: UserDB) -> PersonalHolder:
    """The holder list_users derives for a platform-category assignment on `user`."""
    return PersonalHolder(
        user_id=user.id,
        category=BudgetCategory.PLATFORM,
        aliases=frozenset({user.username, user.email}),
    )


def _list_users(mock_session):
    return UserManagementService.list_users(
        mock_session, requesting_user_id="admin-user", is_project_admin=True, page=0, per_page=20
    )


def _arrange(mock_repo, mock_user_proj_repo, users, assignments_map):
    mock_repo.count_users.return_value = len(users)
    mock_repo.query_users.return_value = users
    mock_repo.fetch_projects_map.return_value = {}
    mock_repo.fetch_budget_assignments_map.return_value = assignments_map
    mock_user_proj_repo.filter_visible_projects_from_map.return_value = {}


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.spend_service")
def test_measured_zero_renders_as_zero(mock_spend, mock_repo, mock_user_proj_repo, mock_session):
    user = _user()
    _arrange(mock_repo, mock_user_proj_repo, [user], {user.id: [(_assignment(user.id), _budget())]})
    mock_spend.consumed_for_holders = AsyncMock(
        return_value={
            PersonalHolder(
                user_id=user.id,
                category=BudgetCategory.PLATFORM,
                aliases=frozenset({user.username, user.email}),
            ): SpendResult(figure=Decimal("0"))
        }
    )

    result = _list_users(mock_session)

    assert result.data[0].budget_assignments[0].current_spending == 0.0


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.spend_service")
def test_absent_figure_stays_absent(mock_spend, mock_repo, mock_user_proj_repo, mock_session):
    """A no-figure result must not be coerced to 0.0 - unknown consumption is not zero consumption."""
    user = _user()
    _arrange(mock_repo, mock_user_proj_repo, [user], {user.id: [(_assignment(user.id), _budget())]})
    holder = PersonalHolder(
        user_id=user.id,
        category=BudgetCategory.PLATFORM,
        aliases=frozenset({user.username, user.email}),
    )
    period = TimeRange(datetime(2099, 1, 1, tzinfo=UTC), datetime(2099, 2, 1, tzinfo=UTC))
    mock_spend.consumed_for_holders = AsyncMock(return_value={holder: SpendResult.no_figure(period)})

    result = _list_users(mock_session)

    assert result.data[0].budget_assignments[0].current_spending is None
    # The blank must say why it is blank: a missing number alone reads as "nothing was spent".
    assert result.data[0].budget_assignments[0].spend_unavailable is True


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.spend_service")
def test_real_figure_is_reported(mock_spend, mock_repo, mock_user_proj_repo, mock_session):
    user = _user()
    _arrange(mock_repo, mock_user_proj_repo, [user], {user.id: [(_assignment(user.id), _budget())]})
    mock_spend.consumed_for_holders = AsyncMock(
        return_value={
            PersonalHolder(
                user_id=user.id,
                category=BudgetCategory.PLATFORM,
                aliases=frozenset({user.username, user.email}),
            ): SpendResult(figure=Decimal("12.5"))
        }
    )

    result = _list_users(mock_session)

    assignment = result.data[0].budget_assignments[0]
    assert assignment.current_spending == 12.5
    # The one state safe to render bare: the whole range, complete in every dimension.
    assert assignment.spend_unavailable is False
    assert assignment.spend_partial is False
    assert assignment.spend_missing_amount is None


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.spend_service")
def test_a_reset_codemie_set_starts_the_period_where_it_was_recorded(
    mock_spend, mock_repo, mock_user_proj_repo, mock_session
):
    """EPMCDME-15204: the users list reads a new budget's first day, as the profile does."""
    user = _user()
    created = datetime(2099, 1, 9, 15, 5, tzinfo=UTC)
    pinned = datetime(2099, 2, 9, tzinfo=UTC)
    budget = _budget(reset_at="2099-02-09T00:00:00+00:00")
    budget.pinned_reset_at, budget.pinned_period_start = pinned, created
    _arrange(mock_repo, mock_user_proj_repo, [user], {user.id: [(_assignment(user.id), budget)]})
    mock_spend.consumed_for_holders = AsyncMock(return_value={})

    _list_users(mock_session)

    assert mock_spend.consumed_for_holders.await_args.args[1] == TimeRange(created, pinned)


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.spend_service")
@pytest.mark.parametrize(
    ("spend_reset_at", "expected_start"),
    [
        pytest.param(datetime(2099, 1, 14, 9, 30, tzinfo=UTC), datetime(2099, 1, 14, 9, 30, tzinfo=UTC), id="inside"),
        pytest.param(datetime(2099, 1, 14, 9, 30), datetime(2099, 1, 14, 9, 30, tzinfo=UTC), id="naive-is-utc"),
        pytest.param(datetime(2098, 12, 20, tzinfo=UTC), datetime(2099, 1, 1, tzinfo=UTC), id="earlier-period"),
        pytest.param(None, datetime(2099, 1, 1, tzinfo=UTC), id="never-reset"),
    ],
)
def test_a_manual_reset_moves_the_period_start_only_when_it_falls_inside_the_period(
    mock_spend, mock_repo, mock_user_proj_repo, mock_session, spend_reset_at, expected_start
):
    user = _user()
    assignment = _assignment(user.id, spend_reset_at=spend_reset_at)
    _arrange(mock_repo, mock_user_proj_repo, [user], {user.id: [(assignment, _budget())]})
    mock_spend.consumed_for_holders = AsyncMock(return_value={})

    _list_users(mock_session)

    period = mock_spend.consumed_for_holders.await_args.args[1]
    assert period == TimeRange(expected_start, datetime(2099, 2, 1, tzinfo=UTC))


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.spend_service")
def test_holders_sharing_a_period_are_read_in_one_call(mock_spend, mock_repo, mock_user_proj_repo, mock_session):
    """Two users on the same reset schedule collapse to one period, so one batched port read."""
    user_1 = _user("one@example.com", "one")
    user_2 = _user("two@example.com", "two")
    _arrange(
        mock_repo,
        mock_user_proj_repo,
        [user_1, user_2],
        {
            user_1.id: [(_assignment(user_1.id), _budget())],
            user_2.id: [(_assignment(user_2.id), _budget())],
        },
    )
    mock_spend.consumed_for_holders = AsyncMock(return_value={})

    _list_users(mock_session)

    mock_spend.consumed_for_holders.assert_awaited_once()
    holders = mock_spend.consumed_for_holders.await_args.args[0]
    assert {h.user_id for h in holders} == {user_1.id, user_2.id}


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.spend_service")
def test_distinct_periods_are_read_once_each(mock_spend, mock_repo, mock_user_proj_repo, mock_session):
    user_1 = _user("one@example.com", "one")
    user_2 = _user("two@example.com", "two")
    _arrange(
        mock_repo,
        mock_user_proj_repo,
        [user_1, user_2],
        {
            user_1.id: [(_assignment(user_1.id), _budget(duration="30d"))],
            user_2.id: [(_assignment(user_2.id), _budget(duration="1d"))],
        },
    )
    mock_spend.consumed_for_holders = AsyncMock(return_value={})

    _list_users(mock_session)

    assert mock_spend.consumed_for_holders.await_count == 2


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.spend_service")
def test_underivable_period_is_not_queried(mock_spend, mock_repo, mock_user_proj_repo, mock_session):
    """No reset instant means no budget period, so there is no figure and no query."""
    user = _user()
    _arrange(
        mock_repo,
        mock_user_proj_repo,
        [user],
        {user.id: [(_assignment(user.id), _budget(reset_at=None))]},
    )
    mock_spend.consumed_for_holders = AsyncMock()

    result = _list_users(mock_session)

    assert result.data[0].budget_assignments[0].current_spending is None
    # Never asked is the same fact to a reader as no figure available, and is reported as such.
    assert result.data[0].budget_assignments[0].spend_unavailable is True
    mock_spend.consumed_for_holders.assert_not_awaited()


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.spend_service")
def test_unknown_category_skips_the_figure_without_raising(mock_spend, mock_repo, mock_user_proj_repo, mock_session):
    user = _user()
    _arrange(
        mock_repo,
        mock_user_proj_repo,
        [user],
        {user.id: [(_assignment(user.id, category="not_a_category"), _budget())]},
    )
    mock_spend.consumed_for_holders = AsyncMock()

    result = _list_users(mock_session)

    assert result.data[0].budget_assignments[0].current_spending is None
    mock_spend.consumed_for_holders.assert_not_awaited()


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.run_coroutine_blocking")
def test_bridge_timeout_yields_absent_figures_without_failing_the_listing(
    mock_bridge, mock_repo, mock_user_proj_repo, mock_session
):
    user = _user()
    _arrange(mock_repo, mock_user_proj_repo, [user], {user.id: [(_assignment(user.id), _budget())]})

    def _timeout(coro, **_kwargs):
        coro.close()
        return None

    mock_bridge.side_effect = _timeout

    result = _list_users(mock_session)

    assert len(result.data) == 1
    assert result.data[0].budget_assignments[0].current_spending is None
    # Every figure is absent, and each row says so rather than showing an unexplained blank.
    assert result.data[0].budget_assignments[0].spend_unavailable is True


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.spend_service")
def test_truncated_figure_survives_and_is_flagged(mock_spend, mock_repo, mock_user_proj_repo, mock_session):
    """A figure covering only part of the period is a subtotal, and must not render as a total."""
    user = _user()
    _arrange(mock_repo, mock_user_proj_repo, [user], {user.id: [(_assignment(user.id), _budget())]})
    mock_spend.consumed_for_holders = AsyncMock(
        return_value={
            _holder(user): SpendResult(
                figure=Decimal("5.0"),
                unavailable=TimeRange(datetime(2099, 1, 1, tzinfo=UTC), datetime(2099, 1, 10, tzinfo=UTC)),
            )
        }
    )

    result = _list_users(mock_session)

    assignment = result.data[0].budget_assignments[0]
    assert assignment.current_spending == 5.0
    assert assignment.spend_unavailable is True


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.spend_service")
def test_uncomputable_partial_portion_reports_an_unknown_missing_amount(
    mock_spend, mock_repo, mock_user_proj_repo, mock_session
):
    """An unresolvable alias set has no rows to sum, so the missing amount is unknown - not zero."""
    user = _user()
    _arrange(mock_repo, mock_user_proj_repo, [user], {user.id: [(_assignment(user.id), _budget())]})
    mock_spend.consumed_for_holders = AsyncMock(
        return_value={
            _holder(user): SpendResult(
                figure=Decimal("5.0"),
                partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
            )
        }
    )

    result = _list_users(mock_session)

    assignment = result.data[0].budget_assignments[0]
    assert assignment.current_spending == 5.0
    assert assignment.spend_partial is True
    assert assignment.spend_missing_amount is None
    assert assignment.spend_unavailable is False


def test_annotation_fields_are_additive():
    """Every other client of this model constructs it without the annotation and must keep working."""
    assignment = UserBudgetAssignmentInfo(category="platform", budget_id="budget-1")

    assert (assignment.spend_unavailable, assignment.spend_partial, assignment.spend_missing_amount) == (
        False,
        False,
        None,
    )


@patch("codemie.repository.user_project_repository.user_project_repository")
@patch("codemie.service.user.user_management_service.user_repository")
@patch("codemie.service.user.user_management_service.spend_service")
def test_an_overdue_reset_is_reconciled_and_reloaded_before_any_period_is_derived(
    mock_spend, mock_repo, mock_user_proj_repo, mock_session, refresh_overdue
):
    """The stored reset names the period that just closed; the list reconciles it first, so the
    figure's period and the reset on screen are the current ones."""
    user = _user()
    budget = _budget()
    _arrange(mock_repo, mock_user_proj_repo, [user], {user.id: [(_assignment(user.id), budget)]})

    def reload(obj, attributes):
        obj.budget_reset_at = "2099-03-01T00:00:00+00:00"

    mock_session.refresh.side_effect = reload
    refresh_overdue.return_value = {"budget-1"}
    mock_spend.consumed_for_holders = AsyncMock(return_value={_holder(user): SpendResult(figure=Decimal("2"))})

    result = _list_users(mock_session)

    assert list(refresh_overdue.await_args.args[0]) == [budget]
    mock_session.refresh.assert_called_once_with(budget, ["budget_reset_at"])
    period = mock_spend.consumed_for_holders.await_args.args[1]
    assert period == TimeRange(datetime(2099, 2, 1, tzinfo=UTC), datetime(2099, 3, 1, tzinfo=UTC))
    assert result.data[0].budget_assignments[0].budget_reset_at == "2099-03-01T00:00:00+00:00"
