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

"""Unit tests for budget_usage_service helpers and BudgetUsageService."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


from codemie.service.analytics.handlers.budget_usage_service import (
    BudgetUsageService,
    _build_budget_usage_rows,
    _build_spending_row,
    _calculate_time_until_reset,
    _get_key_spending_columns,
)


class TestCalculateTimeUntilReset:
    """Tests for _calculate_time_until_reset."""

    def test_returns_none_for_none_input(self):
        assert _calculate_time_until_reset(None) is None

    def test_returns_none_for_empty_string(self):
        assert _calculate_time_until_reset("") is None

    def test_returns_expired_for_past_timestamp(self):
        result = _calculate_time_until_reset("2000-01-01T00:00:00Z")
        assert result == "Expired"

    def test_returns_formatted_string_for_future_timestamp(self):
        future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        result = _calculate_time_until_reset(future.isoformat())
        assert result is not None
        assert "days" in result
        assert "hours" in result
        assert "mins" in result

    def test_returns_none_for_invalid_format(self):
        result = _calculate_time_until_reset("not-a-date")
        assert result is None

    def test_handles_isoformat_without_z(self):
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        result = _calculate_time_until_reset(future)
        assert result is not None
        assert result != "Expired"

    def test_formats_days_hours_minutes_correctly(self):
        # 2 days + 3 hours + 30 minutes from now
        delta = timedelta(days=2, hours=3, minutes=30)
        future = datetime.now(timezone.utc) + delta
        result = _calculate_time_until_reset(future.strftime("%Y-%m-%dT%H:%M:%SZ"))
        assert result is not None
        assert "days" in result
        assert "hours" in result
        assert "mins" in result


class TestBuildSpendingRow:
    """Tests for _build_spending_row."""

    def test_calculates_total_percentage_correctly(self):
        spending = {"total_spend": 25.0, "max_budget": 100.0, "budget_reset_at": None}
        row = _build_spending_row("test-project", spending)
        assert row["project_name"] == "test-project"
        assert row["current_spending"] == 25.0
        assert row["budget_limit"] == 100.0
        assert row["total"] == 25.0  # 25/100 * 100

    def test_total_is_zero_when_max_budget_is_none(self):
        spending = {"total_spend": 10.0, "max_budget": None, "budget_reset_at": None}
        row = _build_spending_row("proj", spending)
        assert row["total"] == 0.0
        assert row["budget_limit"] is None

    def test_total_is_zero_when_max_budget_is_zero(self):
        spending = {"total_spend": 10.0, "max_budget": 0.0, "budget_reset_at": None}
        row = _build_spending_row("proj", spending)
        assert row["total"] == 0.0

    def test_rounds_values_to_two_decimal_places(self):
        spending = {"total_spend": 10.123456, "max_budget": 33.333333, "budget_reset_at": None}
        row = _build_spending_row("proj", spending)
        assert row["current_spending"] == 10.12
        assert row["budget_limit"] == 33.33

    def test_time_until_reset_is_none_when_reset_at_missing(self):
        spending = {"total_spend": 5.0, "max_budget": 10.0, "budget_reset_at": None}
        row = _build_spending_row("proj", spending)
        assert row["time_until_reset"] is None


class TestBuildBudgetUsageRows:
    """Tests for _build_budget_usage_rows."""

    def _assignment(self, budget_id: str, category: str):
        return SimpleNamespace(budget_id=budget_id, category=category)

    def _budget(self, max_budget, reset_at=None):
        return SimpleNamespace(max_budget=max_budget, budget_reset_at=reset_at)

    def _spend_row(self, amount: float):
        return SimpleNamespace(budget_period_spend=Decimal(str(amount)))

    def test_returns_correct_columns(self):
        columns, _ = _build_budget_usage_rows("user@example.com", [], {}, {})
        assert columns == _get_key_spending_columns()

    def test_empty_inputs_return_empty_rows(self):
        _, rows = _build_budget_usage_rows("user@example.com", [], {}, {})
        assert rows == []

    def test_platform_category_uses_user_label(self):
        a = self._assignment("b1", "platform")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b1": self._budget(100.0)}, {})
        assert rows[0]["project_name"] == "user@example.com"

    def test_cli_category_appends_cli_suffix(self):
        a = self._assignment("b2", "cli")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b2": self._budget(50.0)}, {})
        assert rows[0]["project_name"] == "user@example.com (cli)"

    def test_premium_models_category_appends_premium_suffix(self):
        a = self._assignment("b3", "premium_models")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b3": self._budget(10.0)}, {})
        assert rows[0]["project_name"] == "user@example.com (premium)"

    def test_unknown_category_appends_category_name(self):
        a = self._assignment("b4", "custom_cat")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b4": self._budget(10.0)}, {})
        assert rows[0]["project_name"] == "user@example.com (custom_cat)"

    def test_assignment_with_missing_budget_is_skipped(self):
        a = self._assignment("unknown-id", "platform")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {}, {})
        assert rows == []

    def test_spend_defaults_to_zero_when_no_spend_row(self):
        a = self._assignment("b1", "platform")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b1": self._budget(100.0)}, {})
        assert rows[0]["current_spending"] == 0.0

    def test_spend_row_value_used_when_present(self):
        a = self._assignment("b1", "platform")
        spend = self._spend_row(42.5)
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b1": self._budget(100.0)}, {"b1": spend})
        assert rows[0]["current_spending"] == 42.5


class TestBudgetUsageServiceNeedsRefresh:
    """Tests for BudgetUsageService._needs_refresh."""

    def test_returns_true_when_spend_map_is_empty(self):
        assert BudgetUsageService()._needs_refresh({}) is True

    def test_returns_false_when_data_is_fresh(self):
        now = datetime.now(timezone.utc)
        row = SimpleNamespace(spend_date=now)
        from codemie.configs.config import config as app_config

        with patch.object(app_config, "BUDGET_USAGE_STALENESS_THRESHOLD_MS", 3_600_000):
            assert BudgetUsageService()._needs_refresh({"b1": row}) is False

    def test_returns_true_when_data_is_stale(self):
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        row = SimpleNamespace(spend_date=old)
        from codemie.configs.config import config as app_config

        with patch.object(app_config, "BUDGET_USAGE_STALENESS_THRESHOLD_MS", 3_600_000):
            assert BudgetUsageService()._needs_refresh({"b1": row}) is True

    def test_handles_naive_spend_date(self):
        naive = datetime(2020, 1, 1)  # no tzinfo
        row = SimpleNamespace(spend_date=naive)
        from codemie.configs.config import config as app_config

        with patch.object(app_config, "BUDGET_USAGE_STALENESS_THRESHOLD_MS", 60_000):
            assert BudgetUsageService()._needs_refresh({"b1": row}) is True

    def test_uses_latest_date_when_multiple_entries(self):
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        fresh = datetime.now(timezone.utc)
        from codemie.configs.config import config as app_config

        with patch.object(app_config, "BUDGET_USAGE_STALENESS_THRESHOLD_MS", 3_600_000):
            # fresh entry in map → should NOT need refresh
            result = BudgetUsageService()._needs_refresh(
                {"b1": SimpleNamespace(spend_date=old), "b2": SimpleNamespace(spend_date=fresh)}
            )
        assert result is False


class TestCalculateTimeUntilResetNaiveDatetime:
    """Tests _calculate_time_until_reset with timezone-naive ISO strings (line 85 branch)."""

    def test_handles_naive_isoformat_string_without_timezone(self):
        # No "Z" and no "+00:00" → fromisoformat yields a naive datetime → triggers tzinfo branch
        result = _calculate_time_until_reset("2099-06-15T12:00:00")
        assert result is not None
        assert "days" in result


class TestBudgetUsageServiceIsLitellmEnabled:
    """Tests for BudgetUsageService._is_litellm_enabled."""

    def test_returns_false_when_enterprise_module_not_importable(self):
        with patch.dict(sys.modules, {"codemie.enterprise.litellm.dependencies": None}):
            result = BudgetUsageService._is_litellm_enabled()
        assert result is False

    def test_returns_result_of_is_litellm_enabled_function(self):
        mock_deps = MagicMock()
        mock_deps.is_litellm_enabled = MagicMock(return_value=True)
        with patch.dict(sys.modules, {"codemie.enterprise.litellm.dependencies": mock_deps}):
            result = BudgetUsageService._is_litellm_enabled()
        assert result is True


class TestBudgetUsageServiceGetBudgetUsage:
    """Tests for BudgetUsageService.get_budget_usage orchestration."""

    @pytest.mark.asyncio
    async def test_returns_rows_when_data_is_fresh(self):
        """No refresh when data is fresh; returns result of _build_budget_usage_rows."""
        service = BudgetUsageService()
        mock_session = AsyncMock()
        fresh_row = SimpleNamespace(spend_date=datetime.now(timezone.utc))

        with patch.object(
            service, "_load_from_db", new_callable=AsyncMock, return_value=([], {}, {"b1": fresh_row}, {})
        ):
            with patch.object(service, "_needs_refresh", return_value=False):
                columns, rows = await service.get_budget_usage(mock_session, "user-1", "user@test.com")

        assert isinstance(columns, list)
        assert isinstance(rows, list)
        mock_session.rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_refresh_when_litellm_disabled(self):
        """No refresh even when data is stale if LiteLLM is not enabled."""
        service = BudgetUsageService()
        mock_session = AsyncMock()
        assignment = SimpleNamespace(budget_id="b1", category="platform")

        with patch.object(service, "_load_from_db", new_callable=AsyncMock, return_value=([assignment], {}, {}, {})):
            with patch.object(service, "_needs_refresh", return_value=True):
                with patch.object(service, "_is_litellm_enabled", return_value=False):
                    columns, rows = await service.get_budget_usage(mock_session, "user-1", "user@test.com")

        mock_session.rollback.assert_not_called()
        assert isinstance(rows, list)

    @pytest.mark.asyncio
    async def test_triggers_refresh_and_reloads_when_stale_and_litellm_enabled(self):
        """Refresh path: calls _refresh_from_litellm, rollback, and reloads from DB."""
        service = BudgetUsageService()
        mock_session = AsyncMock()
        assignment = SimpleNamespace(budget_id="b1", category="platform")
        budget = SimpleNamespace(max_budget=100.0, budget_reset_at=None)
        fresh_spend = {"b1": SimpleNamespace(budget_period_spend=Decimal("10"), spend_date=datetime.now(timezone.utc))}

        load_calls = [
            ([assignment], {"b1": budget}, {}, {}),
            ([assignment], {"b1": budget}, fresh_spend, {}),
        ]

        with patch.object(service, "_load_from_db", new_callable=AsyncMock, side_effect=load_calls):
            with patch.object(service, "_needs_refresh", return_value=True):
                with patch.object(service, "_is_litellm_enabled", return_value=True):
                    with patch.object(
                        service, "_refresh_from_litellm", new_callable=AsyncMock, return_value=fresh_spend
                    ):
                        columns, rows = await service.get_budget_usage(mock_session, "user-1", "user@test.com")

        mock_session.rollback.assert_called_once()
        assert isinstance(rows, list)

    @pytest.mark.asyncio
    async def test_skips_refresh_when_no_assignments(self):
        """Refresh path is skipped when assignments list is empty."""
        service = BudgetUsageService()
        mock_session = AsyncMock()
        refresh_mock = AsyncMock()

        with patch.object(service, "_load_from_db", new_callable=AsyncMock, return_value=([], {}, {}, {})):
            with patch.object(service, "_needs_refresh", return_value=True):
                with patch.object(service, "_is_litellm_enabled", return_value=True):
                    with patch.object(service, "_refresh_from_litellm", refresh_mock):
                        await service.get_budget_usage(mock_session, "user-1", "user@test.com")

        refresh_mock.assert_not_called()


class TestRefreshFromLitellmSpendDedup:
    """Tests that _refresh_from_litellm skips DB insert when spend is unchanged."""

    def _assignment(self, budget_id: str, category: str = "platform"):
        return SimpleNamespace(budget_id=budget_id, category=category)

    @pytest.mark.asyncio
    async def test_skips_insert_when_spend_unchanged(self):
        assignment = self._assignment("b1")
        existing_spend = SimpleNamespace(budget_period_spend=Decimal("42.0"), spend_date=datetime.now(timezone.utc))
        current_spend_map = {"b1": existing_spend}

        mock_tracking = MagicMock()
        mock_tracking.insert_budget_entries = AsyncMock()
        mock_tracking.get_latest_by_budget_ids = AsyncMock(return_value=current_spend_map)

        litellm_result = {"total_spend": 42.0}

        with patch(
            "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository",
            return_value=mock_tracking,
        ):
            with patch(
                "codemie.service.analytics.handlers.budget_usage_service.asyncio.gather",
                new_callable=AsyncMock,
                return_value=[litellm_result],
            ):
                await BudgetUsageService()._refresh_from_litellm(
                    AsyncMock(), "user-1", "user@test.com", [assignment], current_spend_map, {}, {}
                )

        mock_tracking.insert_budget_entries.assert_not_called()

    @pytest.mark.asyncio
    async def test_inserts_when_spend_changed(self):
        """Only rows whose spend changed since last DB read are inserted."""
        service = BudgetUsageService()
        mock_session = AsyncMock()
        assignment = self._assignment("b1")
        existing_spend = SimpleNamespace(budget_period_spend=Decimal("10.0"), spend_date=datetime.now(timezone.utc))
        current_spend_map = {"b1": existing_spend}

        mock_tracking = MagicMock()
        mock_tracking.insert_budget_entries = AsyncMock()
        refreshed_map = {
            "b1": SimpleNamespace(budget_period_spend=Decimal("55.0"), spend_date=datetime.now(timezone.utc))
        }
        mock_tracking.get_latest_by_budget_ids = AsyncMock(return_value=refreshed_map)

        litellm_result = {"total_spend": 55.0}

        with patch("codemie.enterprise.litellm.dependencies.get_customer_spending", return_value=litellm_result):
            with patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository",
                return_value=mock_tracking,
            ):
                with patch(
                    "codemie.service.analytics.handlers.budget_usage_service.asyncio.gather",
                    new_callable=AsyncMock,
                    return_value=[litellm_result],
                ):
                    result = await service._refresh_from_litellm(
                        mock_session, "user-1", "user@test.com", [assignment], current_spend_map, {}, {}
                    )

        mock_tracking.insert_budget_entries.assert_called_once()
        assert result == refreshed_map

    @pytest.mark.asyncio
    async def test_inserts_when_no_existing_spend(self):
        """Row is inserted when there is no previous spend record for that budget."""
        service = BudgetUsageService()
        mock_session = AsyncMock()
        assignment = self._assignment("b1")
        current_spend_map: dict = {}

        mock_tracking = MagicMock()
        mock_tracking.insert_budget_entries = AsyncMock()
        new_map = {"b1": SimpleNamespace(budget_period_spend=Decimal("20.0"), spend_date=datetime.now(timezone.utc))}
        mock_tracking.get_latest_by_budget_ids = AsyncMock(return_value=new_map)

        litellm_result = {"total_spend": 20.0}

        with patch(
            "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository",
            return_value=mock_tracking,
        ):
            with patch(
                "codemie.service.analytics.handlers.budget_usage_service.asyncio.gather",
                new_callable=AsyncMock,
                return_value=[litellm_result],
            ):
                result = await service._refresh_from_litellm(
                    mock_session, "user-1", "user@test.com", [assignment], current_spend_map, {}, {}
                )

        mock_tracking.insert_budget_entries.assert_called_once()
        assert result == new_map


class TestBudgetUsageServiceLoadFromDb:
    """Tests for BudgetUsageService._load_from_db."""

    @pytest.mark.asyncio
    async def test_returns_data_from_repositories(self):
        service = BudgetUsageService()
        mock_session = AsyncMock()

        assignment = SimpleNamespace(budget_id="b1")
        budgets_map = {"b1": MagicMock()}
        spend_map = {"b1": MagicMock()}

        mock_budget_repo = MagicMock()
        mock_budget_repo.get_user_category_assignments = AsyncMock(return_value=[assignment])
        mock_budget_repo.get_by_ids = AsyncMock(return_value=budgets_map)

        mock_tracking = MagicMock()
        mock_tracking.get_latest_by_budget_ids = AsyncMock(return_value=spend_map)
        mock_tracking.get_latest_before_today_by_budget_ids = AsyncMock(return_value={})

        with patch("codemie.repository.budget_repository.budget_repository", mock_budget_repo):
            with patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository",
                return_value=mock_tracking,
            ):
                assignments, bmap, smap, prev_day = await service._load_from_db(mock_session, "user-1", "user@test.com")

        assert assignments == [assignment]
        assert bmap == budgets_map
        assert smap == spend_map

    @pytest.mark.asyncio
    async def test_returns_empty_data_when_no_assignments(self):
        service = BudgetUsageService()
        mock_session = AsyncMock()

        mock_budget_repo = MagicMock()
        mock_budget_repo.get_user_category_assignments = AsyncMock(return_value=[])
        mock_budget_repo.get_by_ids = AsyncMock(return_value={})

        mock_tracking = MagicMock()
        mock_tracking.get_latest_by_budget_ids = AsyncMock(return_value={})
        mock_tracking.get_latest_before_today_by_budget_ids = AsyncMock(return_value={})

        with patch("codemie.repository.budget_repository.budget_repository", mock_budget_repo):
            with patch(
                "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository",
                return_value=mock_tracking,
            ):
                assignments, bmap, smap, prev_day = await service._load_from_db(mock_session, "user-1", "user@test.com")

        assert assignments == []
        assert bmap == {}
        assert smap == {}
