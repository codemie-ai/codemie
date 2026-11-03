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

"""Handler-level wiring tests for the spending endpoints.

They import no spending helper by name and patch only the handler's own collaborators, so they hold unchanged
while the helpers behind the handlers move between modules.
"""

import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import SpendResult, TimeRange

SPENDING_MODULE = "codemie.rest_api.routers.analytics.spending"
LITELLM = "codemie.enterprise.litellm.dependencies"
MEMBER_SPEND_SERVICE = "codemie.service.analytics.handlers.member_spend_service.member_spend_service"

TIME_UNTIL_RESET = re.compile(r"\d+ days \d+ hours \d+ mins")
MATCHED = "<matches N days N hours N mins>"
EXPIRED_TEXT = "0 days 0 hours 0 mins"


def _metric(
    metric_id: str, label: str, value, value_type: str, format_type: str | None = None, description: str = ""
) -> dict:
    """A metric as the endpoint serializes it: the Metric model adds the three unset optional fields."""
    return {
        "id": metric_id,
        "label": label,
        "type": value_type,
        "value": value,
        "format": format_type,
        "description": description,
        "fixed_timeframe": None,
        "secondary_metrics": None,
    }


def _money(metric: dict, unavailable: bool = False) -> dict:
    """A currency metric carries the spend port's three companion keys beside its figure."""
    return {**metric, "spend_unavailable": unavailable, "spend_partial": False, "spend_missing_amount": None}


def _current(value: float | None) -> dict:
    return _money(
        _metric(
            "current_spending",
            "Current Spending ($)",
            value,
            "number",
            "currency",
            "Total amount spent in current budget period",
        ),
        unavailable=value is None,
    )


def _limit(value: float) -> dict:
    return _metric(
        "budget_limit", "Budget Limit ($)", value, "number", "currency", "Maximum budget for the period (hard limit)"
    )


def _reset_at(value: str, value_type: str, format_type: str | None) -> dict:
    return _metric(
        "budget_reset_at", "Budget Reset Date", value, value_type, format_type, "Timestamp when budget will reset"
    )


def _until_reset(value: str) -> dict:
    return _metric("time_until_reset", "Time Until Reset", value, "string", None, "Time remaining until budget resets")


def _extra(metric_id: str, value: float) -> dict:
    """Appended by the handler itself: unknown to the definitions, so its own id is the label."""
    return _money(_metric(metric_id, metric_id, value, "number", "currency"))


@contextmanager
def _litellm(customer, proxy=None, premium=None):
    """The enterprise budget-policy lookups; a budget counts as configured only when its policy is given."""
    with (
        patch(f"{LITELLM}.get_customer_spending", return_value=customer),
        patch(f"{LITELLM}.get_proxy_customer_spending", return_value=proxy),
        patch(f"{LITELLM}.get_premium_customer_spending", return_value=premium),
        patch(f"{LITELLM}.is_premium_models_enabled", return_value=premium is not None),
        patch(f"{LITELLM}.is_proxy_budget_enabled", return_value=proxy is not None),
    ):
        yield


def _consumed(**amounts: float) -> dict:
    """What the spend port answered per budget category."""
    categories = {
        "platform": BudgetCategory.PLATFORM,
        "cli": BudgetCategory.CLI,
        "premium": BudgetCategory.PREMIUM_MODELS,
    }
    return {categories[name]: SpendResult(figure=Decimal(str(amount))) for name, amount in amounts.items()}


async def _get_metrics(customer, proxy=None, premium=None, consumed=None) -> list[dict]:
    from codemie.rest_api.routers.analytics.spending import get_user_spending

    with (
        _litellm(customer, proxy, premium),
        patch(f"{SPENDING_MODULE}._consumed_per_budget_category", new=AsyncMock(return_value=consumed or {})),
    ):
        response = await get_user_spending(user=MagicMock(id="u-1", username="testuser"))

    return json.loads(response.body)["data"]["metrics"]


def _spending(reset: str | None) -> dict:
    """Budget policy only: the money comes from the spend port, never from this dict."""
    return {"max_budget": 100.0, "budget_reset_at": reset}


class TestUserSpendingEndpoint:
    @pytest.mark.asyncio
    async def test_future_reset_date_returns_the_four_metrics(self):
        metrics = await _get_metrics(_spending("2999-01-01T00:00:00Z"), consumed=_consumed(platform=15.5))

        assert TIME_UNTIL_RESET.fullmatch(metrics[3]["value"])
        metrics[3]["value"] = MATCHED
        assert metrics == [
            _current(15.5),
            _limit(100.0),
            _reset_at("2999-01-01T00:00:00Z", "date", "timestamp"),
            _until_reset(MATCHED),
        ]

    @pytest.mark.asyncio
    async def test_past_reset_date_reports_no_time_left(self):
        metrics = await _get_metrics(_spending("2020-01-01T00:00:00Z"), consumed=_consumed(platform=15.5))

        assert metrics == [
            _current(15.5),
            _limit(100.0),
            _reset_at("2020-01-01T00:00:00Z", "date", "timestamp"),
            _until_reset(EXPIRED_TEXT),
        ]

    @pytest.mark.asyncio
    async def test_missing_reset_date_reports_not_available_twice(self):
        metrics = await _get_metrics(_spending(None), consumed=_consumed(platform=15.5))

        assert metrics == [
            _current(15.5),
            _limit(100.0),
            _reset_at("N/A", "string", None),
            _until_reset("N/A"),
        ]

    @pytest.mark.asyncio
    async def test_unavailable_budget_tracking_reports_no_figure_and_not_available(self):
        metrics = await _get_metrics(None)

        assert metrics == [
            _current(None),
            _metric(
                "budget_limit", "Budget Limit ($)", "N/A", "string", None, "Maximum budget for the period (hard limit)"
            ),
            _reset_at("N/A", "string", None),
            _until_reset("N/A"),
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("proxy", "premium", "extras"),
        [
            pytest.param({"max_budget": 10.0}, None, [_extra("cli_current_spending", 1.5)], id="proxy-only"),
            pytest.param(None, {"max_budget": 20.0}, [_extra("premium_current_spending", 2.5)], id="premium-only"),
            pytest.param(
                {"max_budget": 10.0},
                {"max_budget": 20.0},
                [_extra("cli_current_spending", 1.5), _extra("premium_current_spending", 2.5)],
                id="proxy-then-premium",
            ),
        ],
    )
    async def test_proxy_and_premium_spending_are_appended_after_the_four_metrics(self, proxy, premium, extras):
        consumed = _consumed(platform=15.5, **({"cli": 1.5} if proxy else {}), **({"premium": 2.5} if premium else {}))
        metrics = await _get_metrics(_spending(None), proxy, premium, consumed=consumed)

        assert metrics == [
            _current(15.5),
            _limit(100.0),
            _reset_at("N/A", "string", None),
            _until_reset("N/A"),
            *extras,
        ]


POLICY = {"max_budget": 100.0, "budget_duration": "30d", "budget_reset_at": "2099-07-01T00:00:00Z"}
POLICY_PERIOD_START = datetime(2099, 6, 1, tzinfo=timezone.utc)
POLICY_PERIOD_END = datetime(2099, 7, 1, tzinfo=timezone.utc)


async def _period_asked_about(spend_reset_at: datetime | None, assigned: bool = True) -> TimeRange:
    """The period the spend port is asked about for a user whose platform assignment carries `spend_reset_at`."""
    from codemie.rest_api.routers.analytics.spending import get_user_spending

    assignments = [SimpleNamespace(category="platform", budget_id="b-1", spend_reset_at=spend_reset_at)]
    budget = SimpleNamespace(pinned_reset_at=None, pinned_period_start=None)
    port = AsyncMock(return_value={})
    with (
        _litellm(POLICY),
        patch(f"{SPENDING_MODULE}.get_async_session", return_value=_session_context()),
        patch(f"{SPENDING_MODULE}.resolve_identity", new=AsyncMock(return_value=MagicMock(aliases=frozenset()))),
        patch(
            f"{SPENDING_MODULE}.budget_repository.get_user_category_assignments",
            new=AsyncMock(return_value=assignments if assigned else []),
        ),
        patch(f"{SPENDING_MODULE}.budget_repository.get_by_ids", new=AsyncMock(return_value={"b-1": budget})),
        patch(f"{SPENDING_MODULE}.spend_service.consumed_for_holders", new=port),
    ):
        await get_user_spending(user=MagicMock(id="u-1", username="testuser"))

    return port.await_args.args[1]


class TestSpendingCountsFromTheManualReset:
    @pytest.mark.asyncio
    async def test_a_reset_inside_the_period_starts_it_at_the_reset(self):
        reset = datetime(2099, 6, 14, 9, 30, tzinfo=timezone.utc)

        assert await _period_asked_about(reset) == TimeRange(reset, POLICY_PERIOD_END)

    @pytest.mark.asyncio
    async def test_a_naive_reset_is_read_as_utc(self):
        reset = datetime(2099, 6, 14, 9, 30)

        assert await _period_asked_about(reset) == TimeRange(reset.replace(tzinfo=timezone.utc), POLICY_PERIOD_END)

    @pytest.mark.asyncio
    async def test_a_reset_from_an_earlier_period_leaves_the_period_unchanged(self):
        reset = datetime(2099, 5, 20, 9, 30, tzinfo=timezone.utc)

        assert await _period_asked_about(reset) == TimeRange(POLICY_PERIOD_START, POLICY_PERIOD_END)

    @pytest.mark.asyncio
    async def test_no_reset_leaves_the_period_unchanged(self):
        assert await _period_asked_about(None) == TimeRange(POLICY_PERIOD_START, POLICY_PERIOD_END)

    @pytest.mark.asyncio
    async def test_a_category_with_no_assignment_leaves_the_period_unchanged(self):
        period = await _period_asked_about(datetime(2099, 6, 14, tzinfo=timezone.utc), assigned=False)

        assert period == TimeRange(POLICY_PERIOD_START, POLICY_PERIOD_END)


EMAIL = "someone@example.com"
PROJECT = "atlas-core"
COLUMNS = [{"id": "name", "label": "Name", "type": "string", "format": None, "description": ""}]
ROWS = [{"name": f"row-{index}"} for index in range(5)]


def _session_context() -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=AsyncMock())
    context.__aexit__ = AsyncMock(return_value=False)
    return context


async def _call_user_project_spending(page: int, per_page: int):
    from codemie.rest_api.routers.analytics.spending import get_user_project_spending

    target = MagicMock()
    target.id = "target-uuid"
    with (
        patch(f"{MEMBER_SPEND_SERVICE}.resolve_spend_subject", return_value=(target, {PROJECT})),
        patch(f"{MEMBER_SPEND_SERVICE}.get_user_project_spend", new=AsyncMock(return_value=(COLUMNS, ROWS))),
        patch(f"{SPENDING_MODULE}.get_async_session", return_value=_session_context()),
    ):
        return await get_user_project_spending(user=MagicMock(is_admin=True), users=EMAIL, page=page, per_page=per_page)


async def _call_project_member_spending(page: int, per_page: int):
    from codemie.rest_api.routers.analytics.spending import get_project_member_spending

    with (
        patch(f"{MEMBER_SPEND_SERVICE}.get_project_member_spend", new=AsyncMock(return_value=(COLUMNS, ROWS))),
        patch(f"{SPENDING_MODULE}.get_async_session", return_value=_session_context()),
    ):
        return await get_project_member_spending(
            user=MagicMock(is_admin=True), projects=PROJECT, page=page, per_page=per_page
        )


PAGED_ENDPOINTS = pytest.mark.parametrize(
    ("call", "filters"),
    [
        pytest.param(_call_user_project_spending, {"users": EMAIL}, id="user-project-spending"),
        pytest.param(_call_project_member_spending, {"projects": PROJECT}, id="project-member-spending"),
    ],
)


class TestSpendingPagination:
    @pytest.mark.asyncio
    @PAGED_ENDPOINTS
    async def test_second_page_returns_its_slice_with_the_unsliced_total(self, call, filters):
        response = await call(page=1, per_page=2)

        assert response["data"]["rows"] == ROWS[2:4]
        assert response["data"]["columns"] == COLUMNS
        assert response["metadata"]["filters_applied"] == filters
        pagination = response["pagination"]
        assert (pagination["page"], pagination["per_page"], pagination["total_count"]) == (1, 2, 5)
        assert pagination["has_more"] is True

    @pytest.mark.asyncio
    @PAGED_ENDPOINTS
    async def test_page_past_the_end_returns_no_rows(self, call, filters):
        response = await call(page=3, per_page=2)

        assert response["data"]["rows"] == []
        assert response["pagination"]["total_count"] == 5
        assert response["pagination"]["has_more"] is False
