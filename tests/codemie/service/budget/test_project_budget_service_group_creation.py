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

from codemie.service.budget.project_budget_service import (
    ProjectBudgetGroupCategoryResult,
    ProjectBudgetGroupFullResult,
    ProjectBudgetService,
)
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.settings.settings import SettingsService


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
        group = SimpleNamespace(
            id="group-1", project_name="proj-a", name="group", description=None, deleted_at=None, budget_duration="30d"
        )
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
            patch(
                "codemie.service.budget.project_budget_service.project_budget_group_repository.get_active_by_project",
                new=AsyncMock(return_value=group),
            ),
            patch.object(service, "_update_group_scalar_fields", new=AsyncMock()),
            patch.object(
                service,
                "_load_group_full_result",
                new=AsyncMock(return_value=SimpleNamespace(group=group, categories=[], total_amount=100.0)),
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


_GROUP_REPO = "codemie.service.budget.project_budget_service.project_budget_group_repository"


def _active_group(duration: str = "30d") -> SimpleNamespace:
    return SimpleNamespace(id="group-1", project_name="proj-a", deleted_at=None, budget_duration=duration)


def _fixed_member(user_id: str, max_budget: float, soft_budget: float) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        allocation_mode="fixed",
        allocated_max_budget=max_budget,
        allocated_soft_budget=soft_budget,
    )


def _active_result(
    *, duration: str = "30d", platform_max: float = 100.0, platform_members: list | None = None
) -> ProjectBudgetGroupFullResult:
    platform = ProjectBudgetGroupCategoryResult(
        budget=SimpleNamespace(
            budget_id="b-platform",
            max_budget=platform_max,
            budget_duration=duration,
            notification_owner_email=None,
            soft_limit_notify_once=False,
            soft_limit_notification_enabled=False,
        ),
        assignment=SimpleNamespace(budget_category="platform"),
        allocations=platform_members or [],
    )
    return ProjectBudgetGroupFullResult(group=_active_group(duration), categories=[platform])


def _update_request(**fields) -> SimpleNamespace:
    defaults = {
        "name": None,
        "description": None,
        "budget_duration": None,
        "total_amount": None,
        "categories": None,
        "notification_owner_email": None,
        "soft_limit_notify_once": None,
        "soft_limit_notification_enabled": None,
    }
    return SimpleNamespace(**{**defaults, **fields})


def _spec(pct: float, soft_budget: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(pct=pct, soft_budget=soft_budget)


_BUDGET_REPO = "codemie.service.budget.project_budget_service.budget_repository"
_ASSIGNMENT_REPO = "codemie.service.budget.project_budget_service.project_budget_assignment_repository"


async def _check_against(
    data, active: ProjectBudgetGroupFullResult | None, *, budget_named=None, category_assignment=None, session=None
) -> None:
    service = ProjectBudgetService()
    group = active.group if active is not None else None
    with (
        patch(f"{_GROUP_REPO}.get_active_by_project", new=AsyncMock(return_value=group)),
        patch.object(service, "_load_group_full_result", new=AsyncMock(return_value=active)),
        patch(f"{_BUDGET_REPO}.get_active_by_name", new=AsyncMock(return_value=budget_named)) as by_name,
        patch(
            f"{_ASSIGNMENT_REPO}.get_active_by_project_category", new=AsyncMock(return_value=category_assignment)
        ) as by_category,
    ):
        await service.check_group_request(session or AsyncMock(), "proj-a", data)
    return by_name, by_category


@pytest.mark.asyncio
class TestCheckGroupRequest:
    async def test_check_group_request_rejects_invalid_duration(self):
        data = _update_request(budget_duration="5x")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _check_against(data, _active_result())

        assert exc_info.value.code == 400
        assert "budget_duration must match" in exc_info.value.message

    async def test_check_group_request_rejects_unknown_category(self):
        data = _update_request(categories={"platform": _spec(50.0), "bogus": _spec(50.0)})

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _check_against(data, _active_result())

        assert exc_info.value.code == 400
        assert "bogus" in exc_info.value.message

    async def test_check_group_request_rejects_missing_platform(self):
        data = _update_request(categories={"cli": _spec(100.0)})

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _check_against(data, _active_result())

        assert exc_info.value.code == 400
        assert "platform" in exc_info.value.message

    async def test_check_group_request_rejects_pct_sum_not_100(self):
        data = _update_request(categories={"platform": _spec(50.0), "cli": _spec(30.0)})

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _check_against(data, _active_result())

        assert exc_info.value.code == 400
        assert "sum to 100" in exc_info.value.message

    async def test_check_group_request_rejects_soft_budget_above_category_max(self):
        data = _update_request(total_amount=100.0, categories={"platform": _spec(100.0, soft_budget=150.0)})

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _check_against(data, _active_result())

        assert exc_info.value.code == 400
        assert "soft_budget must be <= max_budget" in exc_info.value.message

    async def test_check_group_request_rejects_fixed_member_hard_limit_over_planned_max(self):
        members = [_fixed_member("u1", max_budget=60.0, soft_budget=10.0)]
        data = _update_request(total_amount=50.0, categories={"platform": _spec(100.0)})

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _check_against(data, _active_result(platform_members=members))

        assert exc_info.value.code == 400
        assert "Personal hard limits" in exc_info.value.message

    async def test_check_group_request_rejects_fixed_member_soft_limit_over_planned_soft(self):
        members = [_fixed_member("u1", max_budget=45.0, soft_budget=45.0)]
        data = _update_request(total_amount=100.0, categories={"platform": _spec(100.0, soft_budget=40.0)})

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _check_against(data, _active_result(platform_members=members))

        assert exc_info.value.code == 400
        assert "Personal soft limits" in exc_info.value.message

    async def test_check_group_request_plans_from_active_group_total_when_data_omits_it(self):
        members = [_fixed_member("u1", max_budget=60.0, soft_budget=10.0)]
        data = _update_request(categories={"platform": _spec(50.0), "cli": _spec(50.0)})

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _check_against(data, _active_result(platform_max=100.0, platform_members=members))

        assert "Personal hard limits" in exc_info.value.message

    async def test_check_group_request_validates_active_group_duration_when_data_omits_it(self):
        data = _update_request(categories={"platform": _spec(100.0)})

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _check_against(data, _active_result(duration="bogus"))

        assert exc_info.value.code == 400
        assert "budget_duration must match" in exc_info.value.message

    async def test_check_group_request_without_active_group_or_total_is_404(self):
        data = _update_request(categories={"platform": _spec(100.0)})

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _check_against(data, None)

        assert exc_info.value.code == 404

    async def test_check_group_request_accepts_valid_request_and_writes_nothing(self):
        session = AsyncMock()
        members = [_fixed_member("u1", max_budget=20.0, soft_budget=10.0)]
        data = _update_request(total_amount=100.0, categories={"platform": _spec(60.0), "cli": _spec(40.0)})

        await _check_against(data, _active_result(platform_members=members), session=session)

        session.add.assert_not_called()
        session.commit.assert_not_awaited()
        session.flush.assert_not_awaited()

    async def test_check_group_request_rejects_a_new_category_whose_budget_name_is_taken(self):
        data = _update_request(total_amount=100.0, categories={"platform": _spec(60.0), "cli": _spec(40.0)})

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _check_against(data, _active_result(), budget_named=SimpleNamespace(budget_id="standalone"))

        assert (exc_info.value.code, exc_info.value.message) == (409, "Budget name 'proj-a/cli' already in use")

    async def test_check_group_request_rejects_a_new_category_the_project_already_budgets(self):
        data = _update_request(total_amount=100.0, categories={"platform": _spec(60.0), "cli": _spec(40.0)})

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _check_against(data, _active_result(), category_assignment=SimpleNamespace(budget_id="standalone"))

        assert exc_info.value.code == 409
        assert exc_info.value.message == (
            "An active project budget for project 'proj-a' and category 'cli' already exists"
        )

    async def test_check_group_request_looks_up_only_categories_the_group_would_add(self):
        data = _update_request(
            total_amount=100.0,
            categories={"platform": _spec(60.0), "cli": _spec(40.0), "premium_models": _spec(0.0)},
        )

        by_name, by_category = await _check_against(data, _active_result())

        assert [call.args[1:] for call in by_name.await_args_list] == [("proj-a/cli",)]
        assert [call.args[1:] for call in by_category.await_args_list] == [("proj-a", "cli")]

    async def test_check_group_request_for_a_new_group_looks_up_every_planned_category(self):
        data = _update_request(
            total_amount=100.0, budget_duration="30d", categories={"platform": _spec(60.0), "cli": _spec(40.0)}
        )

        by_name, _ = await _check_against(data, None)

        assert [call.args[1:] for call in by_name.await_args_list] == [("proj-a/platform",), ("proj-a/cli",)]


@pytest.mark.asyncio
class TestGroupWritesRunPlanCheckFirst:
    async def test_create_group_runs_check_first(self):
        service = ProjectBudgetService()
        session = AsyncMock()
        data = SimpleNamespace(
            name="group",
            description=None,
            budget_duration="30d",
            total_amount=100.0,
            project_name="proj-a",
            categories={"platform": _spec(100.0)},
        )
        ensure_project = AsyncMock()
        rejection = ExtendedHTTPException(code=400, message="plan rejected")

        with (
            patch.object(service, "_check_group_plan", side_effect=rejection) as check,
            patch.object(service, "_ensure_project_budget_eligible", new=ensure_project),
            pytest.raises(ExtendedHTTPException) as exc_info,
        ):
            await service.create_project_budget_group(session=session, data=data, actor_id="actor-1")

        assert exc_info.value is rejection
        check.assert_called_once_with(data, None)
        ensure_project.assert_not_awaited()

    async def test_update_group_below_fixed_limits_is_400_and_writes_nothing(self):
        service = ProjectBudgetService()
        session = AsyncMock()
        members = [_fixed_member("u1", max_budget=60.0, soft_budget=10.0)]
        active = _active_result(platform_members=members)
        data = _update_request(name="renamed", total_amount=50.0, categories={"platform": _spec(100.0)})
        update_group_row = AsyncMock()
        update_budget = AsyncMock()
        provider_lookup = MagicMock()

        with (
            patch(f"{_GROUP_REPO}.get_by_id", new=AsyncMock(return_value=active.group)),
            patch(f"{_GROUP_REPO}.get_active_by_project", new=AsyncMock(return_value=active.group)),
            patch(f"{_GROUP_REPO}.update", new=update_group_row),
            patch.object(service, "_load_group_full_result", new=AsyncMock(return_value=active)),
            patch.object(service, "_get_active_member_user_ids", new=AsyncMock(return_value=["u1"])),
            patch.object(service, "update_project_budget", new=update_budget),
            patch("codemie.service.budget.project_budget_service.get_active_provider", new=provider_lookup),
            patch(
                "codemie.service.budget.project_budget_service.activity_event_repository.async_insert", new=AsyncMock()
            ),
            patch.object(SettingsService, "get_enforce_member_spend_limits", return_value=True),
            pytest.raises(ExtendedHTTPException) as exc_info,
        ):
            await service.update_project_budget_group(
                session=session, group_id="group-1", data=data, actor_id="actor-1"
            )

        assert exc_info.value.code == 400
        assert "Personal hard limits" in exc_info.value.message
        update_group_row.assert_not_awaited()
        update_budget.assert_not_awaited()
        provider_lookup.assert_not_called()
