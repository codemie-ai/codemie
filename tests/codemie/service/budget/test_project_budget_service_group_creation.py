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

"""Service tests for project budget validation with zero-allocation categories."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.budget.project_budget_service import ProjectBudgetService
from codemie.core.exceptions import ExtendedHTTPException


class TestValidateGroupCategoriesWithZeroPct:
    """Test _validate_group_categories accepts zero-pct non-platform categories."""

    def test_platform_only_passes_validation(self):
        """Platform 100%, CLI 0%, Premium Models 0% should pass validation."""
        categories = {
            "platform": SimpleNamespace(pct=100.0),
            "cli": SimpleNamespace(pct=0.0),
            "premium_models": SimpleNamespace(pct=0.0),
        }

        # Should not raise
        ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)

    def test_two_categories_with_zero_passes_validation(self):
        """Platform 60%, CLI 40%, Premium Models 0% should pass validation."""
        categories = {
            "platform": SimpleNamespace(pct=60.0),
            "cli": SimpleNamespace(pct=40.0),
            "premium_models": SimpleNamespace(pct=0.0),
        }

        # Should not raise
        ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)

    def test_platform_zero_fails_validation(self):
        """Platform 0% yields a zero hard limit and must be rejected."""
        categories = {
            "platform": SimpleNamespace(pct=0.0),
            "cli": SimpleNamespace(pct=100.0),
            "premium_models": SimpleNamespace(pct=0.0),
        }

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)
        assert exc_info.value.code == 400
        assert "platform" in exc_info.value.message

    def test_platform_tiny_positive_passes_validation(self):
        """Any platform pct greater than 0 is accepted."""
        categories = {
            "platform": SimpleNamespace(pct=0.01),
            "cli": SimpleNamespace(pct=99.99),
        }

        ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)

    def test_zero_soft_budgets_and_zero_secondary_pct_pass_validation(self):
        """Soft budget 0 (including platform) and CLI/premium pct 0 remain valid."""
        categories = {
            "platform": SimpleNamespace(pct=60.0, soft_budget=0),
            "cli": SimpleNamespace(pct=40.0, soft_budget=0),
            "premium_models": SimpleNamespace(pct=0.0, soft_budget=0),
        }

        ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)

    def test_sum_not_100_fails_validation(self):
        """Platform 50%, CLI 30%, Premium Models 0% should fail (sum = 80%)."""
        categories = {
            "platform": SimpleNamespace(pct=50.0),
            "cli": SimpleNamespace(pct=30.0),
            "premium_models": SimpleNamespace(pct=0.0),
        }

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)
        assert exc_info.value.code == 400
        assert "100" in exc_info.value.message

    def test_platform_missing_fails_validation(self):
        """Missing platform category should fail validation."""
        categories = {
            "cli": SimpleNamespace(pct=50.0),
            "premium_models": SimpleNamespace(pct=50.0),
        }

        with pytest.raises(ExtendedHTTPException) as exc_info:
            ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)
        assert exc_info.value.code == 400
        assert "platform" in exc_info.value.message.lower()


@pytest.mark.parametrize(
    "case,offending_key,categories",
    [
        (
            "negative",
            "cli",
            {
                "platform": SimpleNamespace(pct=50.0),
                "cli": SimpleNamespace(pct=-10.0),
            },
        ),
        (
            "nan",
            "platform",
            {
                "platform": SimpleNamespace(pct=float("nan")),
                "cli": SimpleNamespace(pct=0.0),
            },
        ),
        (
            "just_below_zero",
            "cli",
            {
                "platform": SimpleNamespace(pct=50.0),
                "cli": SimpleNamespace(pct=-0.001),
            },
        ),
    ],
)
def test_system_rejects_invalid_distribution_values(case, offending_key, categories):
    with pytest.raises(ExtendedHTTPException) as exc_info:
        ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)
    assert exc_info.value.code == 400
    assert offending_key in exc_info.value.message


def _zero_platform_categories():
    return {
        "platform": SimpleNamespace(pct=0.0, soft_budget=None),
        "cli": SimpleNamespace(pct=100.0, soft_budget=None),
    }


@pytest.mark.asyncio
class TestGroupWritesRejectPlatformZero:
    async def test_create_with_platform_zero_is_rejected_before_persistence(self):
        service = ProjectBudgetService()
        session = AsyncMock()
        session.add = MagicMock()
        data = SimpleNamespace(
            name="group",
            description=None,
            budget_duration="30d",
            total_amount=100.0,
            project_name="proj-a",
            categories=_zero_platform_categories(),
        )
        ensure_project = AsyncMock()
        get_active = AsyncMock(return_value=None)

        with (
            patch.object(service, "_ensure_project_budget_eligible", new=ensure_project),
            patch(
                "codemie.service.budget.project_budget_service.project_budget_group_repository.get_active_by_project",
                new=get_active,
            ),
            pytest.raises(ExtendedHTTPException) as exc_info,
        ):
            await service.create_project_budget_group(session=session, data=data, actor_id="actor-1")

        assert exc_info.value.code == 400
        assert "platform" in exc_info.value.message
        ensure_project.assert_not_awaited()
        get_active.assert_not_awaited()
        session.add.assert_not_called()
        session.commit.assert_not_called()

    async def test_update_legacy_platform_zero_group_with_platform_zero_is_rejected(self):
        service = ProjectBudgetService()
        session = AsyncMock()
        group = SimpleNamespace(id="group-1", project_name="proj-a", deleted_at=None, budget_duration="30d")
        data = SimpleNamespace(
            name=None,
            description=None,
            budget_duration=None,
            total_amount=None,
            notification_owner_email=None,
            soft_limit_notify_once=None,
            soft_limit_notification_enabled=None,
            categories=_zero_platform_categories(),
        )
        update_categories = AsyncMock()

        with (
            patch(
                "codemie.service.budget.project_budget_service.project_budget_group_repository.get_by_id",
                new=AsyncMock(return_value=group),
            ),
            patch.object(service, "_update_group_scalar_fields", new=AsyncMock()),
            patch.object(
                service,
                "_load_group_full_result",
                new=AsyncMock(return_value=SimpleNamespace(categories=[], total_amount=100.0)),
            ),
            patch.object(service, "_update_group_categories", new=update_categories),
            pytest.raises(ExtendedHTTPException) as exc_info,
        ):
            await service.update_project_budget_group(
                session=session, group_id="group-1", data=data, actor_id="actor-1"
            )

        assert exc_info.value.code == 400
        assert "platform" in exc_info.value.message
        update_categories.assert_not_awaited()
        session.commit.assert_not_awaited()
