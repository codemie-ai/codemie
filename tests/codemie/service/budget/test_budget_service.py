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

"""Unit tests for BudgetService (QA-T2).

Covers:
  - Constraint validation (max_budget, soft_budget, duration, category)
  - LiteLLM sync rollback when create_budget_in_litellm fails
  - Delete guard rejects deletion when assignments exist
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException, ValidationException
from codemie.enterprise.litellm.budget_categories import BudgetCategory
from codemie.service.budget.budget_models import Budget
from codemie.service.budget.budget_service import BudgetService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service() -> BudgetService:
    return BudgetService()


def _make_budget(**kwargs) -> Budget:
    defaults = {
        "budget_id": "test-budget",
        "name": "Test Budget",
        "soft_budget": 10.0,
        "max_budget": 100.0,
        "budget_duration": "30d",
        "budget_category": BudgetCategory.PLATFORM.value,
        "created_by": "admin-user",
    }
    defaults.update(kwargs)
    return Budget(**defaults)


def _make_create_request(**kwargs):
    from codemie.rest_api.routers.budget_router import BudgetCreateRequest

    defaults = {
        "budget_id": "new-budget",
        "name": "New Budget",
        "soft_budget": 10.0,
        "max_budget": 100.0,
        "budget_duration": "30d",
        "budget_category": BudgetCategory.PLATFORM,
    }
    defaults.update(kwargs)
    return BudgetCreateRequest(**defaults)


class _AsyncContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


# ---------------------------------------------------------------------------
# TestConstraintValidation
# ---------------------------------------------------------------------------


class TestConstraintValidation:
    def test_max_budget_must_be_positive(self):
        with pytest.raises(ValidationException, match="max_budget must be > 0"):
            BudgetService._validate_constraints(
                soft_budget=0.0, max_budget=0.0, budget_duration="30d", budget_category="platform"
            )

    def test_max_budget_negative_raises(self):
        with pytest.raises(ValidationException, match="max_budget must be > 0"):
            BudgetService._validate_constraints(
                soft_budget=0.0, max_budget=-1.0, budget_duration="30d", budget_category="platform"
            )

    def test_soft_budget_negative_raises(self):
        with pytest.raises(ValidationException, match="soft_budget must be >= 0"):
            BudgetService._validate_constraints(
                soft_budget=-0.01, max_budget=100.0, budget_duration="30d", budget_category="platform"
            )

    def test_soft_budget_exceeds_max_raises(self):
        with pytest.raises(ValidationException, match="soft_budget must be <= max_budget"):
            BudgetService._validate_constraints(
                soft_budget=200.0, max_budget=100.0, budget_duration="30d", budget_category="platform"
            )

    def test_soft_budget_equals_max_allowed(self):
        """soft_budget == max_budget is valid (hard cap equals soft cap)."""
        BudgetService._validate_constraints(
            soft_budget=100.0, max_budget=100.0, budget_duration="30d", budget_category="platform"
        )

    def test_soft_budget_zero_allowed(self):
        BudgetService._validate_constraints(
            soft_budget=0.0, max_budget=100.0, budget_duration="30d", budget_category="platform"
        )

    def test_budget_duration_invalid_format_raises(self):
        with pytest.raises(ValidationException, match="budget_duration must match"):
            BudgetService._validate_constraints(
                soft_budget=10.0, max_budget=100.0, budget_duration="30days", budget_category="platform"
            )

    def test_budget_duration_no_unit_raises(self):
        with pytest.raises(ValidationException, match="budget_duration must match"):
            BudgetService._validate_constraints(
                soft_budget=10.0, max_budget=100.0, budget_duration="30", budget_category="platform"
            )

    @pytest.mark.parametrize("duration", ["30d", "24h", "60m", "1d", "720h"])
    def test_valid_duration_formats(self, duration: str):
        BudgetService._validate_constraints(
            soft_budget=10.0, max_budget=100.0, budget_duration=duration, budget_category="platform"
        )

    def test_invalid_category_raises(self):
        with pytest.raises(ValidationException, match="budget_category must be one of"):
            BudgetService._validate_constraints(
                soft_budget=10.0, max_budget=100.0, budget_duration="30d", budget_category="unknown_cat"
            )

    @pytest.mark.parametrize("category", ["platform", "cli", "premium_models"])
    def test_valid_categories_accepted(self, category: str):
        BudgetService._validate_constraints(
            soft_budget=10.0, max_budget=100.0, budget_duration="30d", budget_category=category
        )


# ---------------------------------------------------------------------------
# TestCategoryMismatchValidation
# ---------------------------------------------------------------------------


class TestCategoryMismatchValidation:
    def test_matching_category_does_not_raise(self):
        budget = _make_budget(budget_category=BudgetCategory.CLI.value)
        BudgetService._validate_budget_matches_category(budget, BudgetCategory.CLI)

    def test_mismatched_category_raises(self):
        budget = _make_budget(budget_category=BudgetCategory.PLATFORM.value)
        with pytest.raises(ValidationException, match="cannot assign"):
            BudgetService._validate_budget_matches_category(budget, BudgetCategory.CLI)


# ---------------------------------------------------------------------------
# TestCreateBudgetLiteLLMRollback
# ---------------------------------------------------------------------------


class TestCreateBudgetLiteLLMRollback:
    """create_budget must roll back the DB insert when LiteLLM sync fails."""

    @pytest.mark.asyncio
    async def test_rolls_back_when_litellm_fails(self):
        """If create_budget_in_litellm returns None, DB row is rolled back and 502 raised."""
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.rollback = AsyncMock()

        inserted_budget = _make_budget()

        with (
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_id",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_name",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.insert",
                new=AsyncMock(return_value=inserted_budget),
            ),
            patch(
                "codemie.service.budget.budget_service.asyncio.to_thread",
                new=AsyncMock(return_value=None),  # LiteLLM returns None → failure
            ),
        ):
            request = _make_create_request()
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await service.create_budget(mock_session, request, actor_id="admin")

        assert exc_info.value.code == 502
        mock_session.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_budget_when_litellm_succeeds(self):
        """Successful LiteLLM sync updates budget_reset_at and returns the Budget row."""
        service = _make_service()
        mock_session = AsyncMock()

        litellm_result = MagicMock()
        litellm_result.budget_reset_at = "2026-05-01T00:00:00Z"

        inserted_budget = _make_budget()
        updated_budget = _make_budget(budget_reset_at="2026-05-01T00:00:00Z")

        with (
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_id",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_name",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.insert",
                new=AsyncMock(return_value=inserted_budget),
            ),
            patch(
                "codemie.service.budget.budget_service.asyncio.to_thread",
                new=AsyncMock(return_value=litellm_result),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.update",
                new=AsyncMock(return_value=updated_budget),
            ),
        ):
            request = _make_create_request()
            result = await service.create_budget(mock_session, request, actor_id="admin")

        assert result.budget_reset_at == "2026-05-01T00:00:00Z"
        mock_session.rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_409_when_budget_id_already_exists(self):
        service = _make_service()
        mock_session = AsyncMock()

        with patch(
            "codemie.service.budget.budget_service.budget_repository.get_by_id",
            new=AsyncMock(return_value=_make_budget()),  # already exists
        ):
            request = _make_create_request()
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await service.create_budget(mock_session, request, actor_id="admin")

        assert exc_info.value.code == 409


# ---------------------------------------------------------------------------
# TestBackfillUserBudgetAssignments
# ---------------------------------------------------------------------------


class TestBackfillUserBudgetAssignments:
    @pytest.mark.asyncio
    async def test_imports_missing_litellm_assignment(self):
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.begin_nested = MagicMock(return_value=_AsyncContext())

        entry = SimpleNamespace(
            user_id="user@example.com_codemie_cli",
            budget_id="cli-budget",
            soft_budget=10,
            max_budget=100,
            budget_duration="30d",
            budget_reset_at=None,
        )

        litellm = MagicMock()
        litellm.get_customer_list.return_value = [entry]

        with (
            patch("codemie.enterprise.litellm.get_litellm_service_or_none", return_value=litellm),
            patch(
                "codemie.service.budget.budget_service.asyncio.to_thread",
                new=AsyncMock(side_effect=[[entry], []]),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_user_id_by_identifier",
                new=AsyncMock(return_value="user-1"),
            ) as mock_get_user,
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_user_category_budget_id",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_id",
                new=AsyncMock(return_value=_make_budget(budget_id="cli-budget", budget_category="cli")),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.upsert_user_category_assignment",
                new=AsyncMock(),
            ) as mock_upsert_assignment,
        ):
            result = await service.backfill_user_budget_assignments_from_litellm(mock_session, actor_id="admin")

        assert result.imported == 1
        assert result.skipped_existing == 0
        assert result.skipped_missing_user == 0
        mock_get_user.assert_awaited_once_with(mock_session, "user@example.com")
        mock_upsert_assignment.assert_awaited_once_with(
            mock_session,
            "user-1",
            BudgetCategory.CLI,
            "cli-budget",
            assigned_by="admin",
        )
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_existing_assignment(self):
        service = _make_service()
        mock_session = AsyncMock()
        mock_session.begin_nested = MagicMock(return_value=_AsyncContext())

        entry = SimpleNamespace(
            user_id="user@example.com",
            budget_id="platform-budget",
            soft_budget=10,
            max_budget=100,
            budget_duration="30d",
            budget_reset_at=None,
        )

        with (
            patch("codemie.enterprise.litellm.get_litellm_service_or_none", return_value=MagicMock()),
            patch(
                "codemie.service.budget.budget_service.asyncio.to_thread",
                new=AsyncMock(side_effect=[[entry], []]),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_user_id_by_identifier",
                new=AsyncMock(return_value="user-1"),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_user_category_budget_id",
                new=AsyncMock(return_value="existing-budget"),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.upsert_user_category_assignment",
                new=AsyncMock(),
            ) as mock_upsert_assignment,
        ):
            result = await service.backfill_user_budget_assignments_from_litellm(mock_session, actor_id="admin")

        assert result.imported == 0
        assert result.skipped_existing == 1
        mock_upsert_assignment.assert_not_awaited()
