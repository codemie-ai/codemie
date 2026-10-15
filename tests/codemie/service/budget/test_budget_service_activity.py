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

"""Unit tests for BudgetService activity event instrumentation.

Covers:
  - create_budget emits BudgetManagementEvent.BUDGET_CREATED
  - update_budget emits BudgetManagementEvent.BUDGET_UPDATED
  - assign_budget_to_user emits BudgetManagementEvent.USER_BUDGET_ASSIGNED
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    BudgetManagementEvent,
)
from codemie.service.budget.budget_enums import BudgetCategory
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
    from codemie.rest_api.models.budget import BudgetCreateRequest

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


def _make_update_request(**kwargs):
    from codemie.rest_api.models.budget import BudgetUpdateRequest

    defaults = {
        "soft_budget": 15.0,
    }
    defaults.update(kwargs)
    return BudgetUpdateRequest(**defaults)


def _make_provider_state(budget_id: str = "new-budget"):
    return SimpleNamespace(
        provider="litellm",
        provider_budget_ref=budget_id,
        sync_status="synced",
        budget_reset_at=None,
    )


# ---------------------------------------------------------------------------
# TestCreateBudgetActivityEvent
# ---------------------------------------------------------------------------


class TestCreateBudgetActivityEvent:
    @pytest.mark.asyncio
    @patch("codemie.service.budget.budget_service.activity_event_repository")
    @patch("codemie.service.budget.budget_service.get_active_provider")
    @patch("codemie.service.budget.budget_service.budget_repository")
    async def test_create_budget_emits_budget_created_event(self, mock_budget_repo, mock_get_provider, mock_activity):
        """create_budget must emit BUDGET_CREATED after persisting the budget."""
        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        budget = _make_budget(budget_id="new-budget")
        mock_budget_repo.get_by_id = AsyncMock(return_value=None)
        mock_budget_repo.get_by_name = AsyncMock(return_value=None)
        mock_budget_repo.insert = AsyncMock(return_value=budget)
        mock_budget_repo.update = AsyncMock(return_value=budget)

        provider = AsyncMock()
        provider.provider_name = "litellm"
        provider.ensure_global_budget = AsyncMock(return_value=_make_provider_state("new-budget"))
        mock_get_provider.return_value = provider

        service = _make_service()
        data = _make_create_request(budget_id="new-budget")

        await service.create_budget(session, data, actor_id="admin-1", actor_name="Admin")

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.BUDGET_CREATED
        assert event_dto.entity_type == ActivityEntityType.BUDGET
        assert event_dto.entity_id == "new-budget"
        assert event_dto.actor_id == "admin-1"
        # The session is passed as the second positional arg
        assert call_args[1] is session

    @pytest.mark.asyncio
    @patch("codemie.service.budget.budget_service.activity_event_repository")
    @patch("codemie.service.budget.budget_service.get_active_provider")
    @patch("codemie.service.budget.budget_service.budget_repository")
    async def test_create_budget_event_not_emitted_on_provider_failure(
        self, mock_budget_repo, mock_get_provider, mock_activity
    ):
        """create_budget must NOT emit an activity event when the provider sync fails (rollback)."""
        from codemie.core.exceptions import ExtendedHTTPException

        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        budget = _make_budget(budget_id="new-budget")
        mock_budget_repo.get_by_id = AsyncMock(return_value=None)
        mock_budget_repo.get_by_name = AsyncMock(return_value=None)
        mock_budget_repo.insert = AsyncMock(return_value=budget)

        provider = AsyncMock()
        provider.provider_name = "litellm"
        provider.ensure_global_budget = AsyncMock(side_effect=RuntimeError("provider failure"))
        mock_get_provider.return_value = provider

        service = _make_service()
        data = _make_create_request(budget_id="new-budget")

        with pytest.raises(ExtendedHTTPException):
            await service.create_budget(session, data, actor_id="admin-1")

        mock_activity.async_insert.assert_not_called()


# ---------------------------------------------------------------------------
# TestUpdateBudgetActivityEvent
# ---------------------------------------------------------------------------


class TestUpdateBudgetActivityEvent:
    @pytest.mark.asyncio
    @patch("codemie.service.budget.budget_service.activity_event_repository")
    @patch("codemie.service.budget.budget_service.budget_config")
    @patch("codemie.service.budget.budget_service.budget_repository")
    async def test_update_budget_emits_budget_updated_event(self, mock_budget_repo, mock_budget_cfg, mock_activity):
        """update_budget must emit BUDGET_UPDATED after persisting the update."""
        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        existing_budget = _make_budget(budget_id="test-budget")
        updated_budget = _make_budget(budget_id="test-budget", soft_budget=15.0)
        mock_budget_repo.get_by_id = AsyncMock(return_value=existing_budget)
        mock_budget_repo.update = AsyncMock(return_value=updated_budget)

        # Ensure this is not treated as a predefined budget
        mock_budget_cfg.predefined_budgets = []

        service = _make_service()
        data = _make_update_request(soft_budget=15.0)

        # update_budget calls self.get_budget which calls budget_repository.get_by_id
        # and also _validate_budget_update_request and _sync_updated_global_budget
        # The simplest path: no provider-owned fields set means no sync call needed
        # soft_budget IS provider-owned, so we need to mock _sync_updated_global_budget
        with patch.object(service, "_sync_updated_global_budget", AsyncMock(return_value=updated_budget)):
            await service.update_budget(session, "test-budget", data, actor_id="admin-1", actor_name="Admin")

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.BUDGET_UPDATED
        assert event_dto.entity_type == ActivityEntityType.BUDGET
        assert event_dto.entity_id == "test-budget"
        assert event_dto.actor_id == "admin-1"
        assert call_args[1] is session

    @pytest.mark.asyncio
    @patch("codemie.service.budget.budget_service.activity_event_repository")
    @patch("codemie.service.budget.budget_service.budget_config")
    @patch("codemie.service.budget.budget_service.budget_repository")
    async def test_update_budget_event_not_emitted_for_predefined_budget(
        self, mock_budget_repo, mock_budget_cfg, mock_activity
    ):
        """update_budget must NOT emit event when updating a predefined (protected) budget."""
        from codemie.core.exceptions import ExtendedHTTPException

        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        predefined = MagicMock()
        predefined.budget_id = "predefined-budget"
        mock_budget_cfg.predefined_budgets = [predefined]

        service = _make_service()
        data = _make_update_request(soft_budget=15.0)

        with pytest.raises(ExtendedHTTPException):
            await service.update_budget(session, "predefined-budget", data, actor_id="admin-1")

        mock_activity.async_insert.assert_not_called()


# ---------------------------------------------------------------------------
# TestAssignBudgetToUserActivityEvent
# ---------------------------------------------------------------------------


class TestAssignBudgetToUserActivityEvent:
    @pytest.mark.asyncio
    @patch("codemie.service.budget.budget_service.logger")
    @patch("codemie.service.budget.budget_service.activity_event_repository")
    @patch("codemie.service.budget.budget_service.get_active_provider")
    @patch("codemie.service.budget.budget_service.budget_repository")
    async def test_assign_budget_to_user_emits_user_budget_assigned_event(
        self, mock_budget_repo, mock_get_provider, mock_activity, mock_logger
    ):
        """assign_budget_to_user must emit USER_BUDGET_ASSIGNED for each non-None assignment and a completion logger.info."""
        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        # Mock user DB lookup
        from codemie.rest_api.models.user_management import UserDB

        db_user = MagicMock(spec=UserDB)
        db_user.id = "user-123"
        db_user.username = "testuser"

        scalars_mock = MagicMock()
        scalars_mock.first.return_value = db_user
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)

        mock_budget_repo.upsert_user_category_assignment = AsyncMock()
        mock_budget_repo.delete_user_category_assignment = AsyncMock()

        provider = AsyncMock()
        provider.assign_user_budget = AsyncMock()
        mock_get_provider.return_value = provider

        service = _make_service()

        # Mock validate_assignment_budget_categories to be a no-op
        with patch.object(service, "validate_assignment_budget_categories", AsyncMock()):
            await service.assign_budget_to_user(
                session,
                user_id="user-123",
                assignments={BudgetCategory.PLATFORM: "test-budget"},
                actor_id="admin-1",
            )

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.USER_BUDGET_ASSIGNED
        assert event_dto.entity_type == ActivityEntityType.USER_BUDGET_ASSIGNMENT
        assert event_dto.entity_id == "user-123"
        assert event_dto.actor_id == "admin-1"
        assert event_dto.attributes == {"budget_id": "test-budget"}
        assert call_args[1] is session
        mock_logger.info.assert_called_once()
        assert "user_budget_assignment_completed" in mock_logger.info.call_args[0][0]

    @pytest.mark.asyncio
    @patch("codemie.service.budget.budget_service.activity_event_repository")
    @patch("codemie.service.budget.budget_service.get_active_provider")
    @patch("codemie.service.budget.budget_service.budget_repository")
    async def test_assign_budget_to_user_emits_user_budget_removed_event_for_none_assignment(
        self, mock_budget_repo, mock_get_provider, mock_activity
    ):
        """assign_budget_to_user must emit USER_BUDGET_REMOVED when budget_id is None (clearing assignment)."""
        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        from codemie.rest_api.models.user_management import UserDB

        db_user = MagicMock(spec=UserDB)
        db_user.id = "user-123"
        db_user.username = "testuser"

        scalars_mock = MagicMock()
        scalars_mock.first.return_value = db_user
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)

        mock_budget_repo.delete_user_category_assignment = AsyncMock()

        provider = AsyncMock()
        provider.clear_user_budget = AsyncMock()
        provider.assign_user_budget = AsyncMock()
        mock_get_provider.return_value = provider

        service = _make_service()

        # Patch _default_budget_id_for_category to return None so clear_user_budget is called
        with patch.object(service, "_default_budget_id_for_category", return_value=None):
            await service.assign_budget_to_user(
                session,
                user_id="user-123",
                assignments={BudgetCategory.PLATFORM: None},
                actor_id="admin-1",
            )

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.USER_BUDGET_REMOVED
        assert event_dto.entity_type == ActivityEntityType.USER_BUDGET_ASSIGNMENT
        assert event_dto.entity_id == "user-123"
        assert event_dto.actor_id == "admin-1"
        assert event_dto.attributes == {"category": BudgetCategory.PLATFORM.value}
        assert call_args[1] is session

    @pytest.mark.asyncio
    @patch("codemie.service.budget.budget_service.activity_event_repository")
    @patch("codemie.service.budget.budget_service.get_active_provider")
    @patch("codemie.service.budget.budget_service.budget_repository")
    async def test_assign_budget_to_user_multiple_assignments_emit_multiple_events(
        self, mock_budget_repo, mock_get_provider, mock_activity
    ):
        """assign_budget_to_user emits one event per non-None category assignment."""
        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        from codemie.rest_api.models.user_management import UserDB

        db_user = MagicMock(spec=UserDB)
        db_user.id = "user-123"
        db_user.username = "testuser"

        scalars_mock = MagicMock()
        scalars_mock.first.return_value = db_user
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)

        mock_budget_repo.upsert_user_category_assignment = AsyncMock()

        provider = AsyncMock()
        provider.assign_user_budget = AsyncMock()
        mock_get_provider.return_value = provider

        service = _make_service()

        with patch.object(service, "validate_assignment_budget_categories", AsyncMock()):
            await service.assign_budget_to_user(
                session,
                user_id="user-123",
                assignments={
                    BudgetCategory.PLATFORM: "platform-budget",
                    BudgetCategory.CLI: "cli-budget",
                },
                actor_id="admin-1",
            )

        assert mock_activity.async_insert.call_count == 2
        emitted_budget_ids = {call[0][0].attributes["budget_id"] for call in mock_activity.async_insert.call_args_list}
        assert emitted_budget_ids == {"platform-budget", "cli-budget"}


# ---------------------------------------------------------------------------
# TestProjectBudgetServiceActivityEvent
# ---------------------------------------------------------------------------


def _make_project_service():
    from codemie.service.budget.project_budget_service import ProjectBudgetService

    return ProjectBudgetService()


def _make_project_budget_create_request(**kwargs):
    from codemie.rest_api.models.project_budget import ProjectBudgetCreateRequest

    defaults = {
        "budget_id": "proj-budget",
        "project_name": "my-project",
        "budget_category": BudgetCategory.PLATFORM,
        "name": "My Project Budget",
        "soft_budget": 50.0,
        "max_budget": 200.0,
        "budget_duration": "30d",
    }
    defaults.update(kwargs)
    return ProjectBudgetCreateRequest(**defaults)


class TestCreateProjectBudgetActivityEvent:
    @pytest.mark.asyncio
    @patch("codemie.service.budget.project_budget_service.activity_event_repository")
    @patch("codemie.service.budget.project_budget_service.get_active_provider")
    @patch("codemie.service.budget.project_budget_service.project_member_budget_assignment_repository")
    @patch("codemie.service.budget.project_budget_service.project_budget_assignment_repository")
    @patch("codemie.service.budget.project_budget_service.budget_repository")
    async def test_create_project_budget_emits_project_budget_created_event(
        self,
        mock_budget_repo,
        mock_assignment_repo,
        mock_member_repo,
        mock_get_provider,
        mock_activity,
    ):
        """create_project_budget must emit PROJECT_BUDGET_CREATED after the budget is persisted."""
        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        created_budget = _make_budget(budget_id="proj-budget-abc123")
        mock_budget_repo.get_by_name = AsyncMock(return_value=None)
        mock_budget_repo.insert = AsyncMock(return_value=created_budget)
        mock_budget_repo.update = AsyncMock(return_value=created_budget)

        mock_assignment_repo.get_active_by_project_category = AsyncMock(return_value=None)
        mock_assignment_repo.insert = AsyncMock(return_value=MagicMock(project_name="my-project"))

        mock_member_repo.insert_many = AsyncMock(return_value=[])

        provider = AsyncMock()
        provider.provider_name = "litellm"
        mock_get_provider.return_value = provider

        service = _make_project_service()
        data = _make_project_budget_create_request()

        with (
            patch.object(service, "_ensure_project_budget_eligible", AsyncMock()),
            patch.object(service, "_get_active_member_user_ids", AsyncMock(return_value=[])),
            patch.object(
                service,
                "_ensure_shared_child_budget",
                AsyncMock(return_value=_make_budget(budget_id="shared-child")),
            ),
            patch.object(service, "_sync_created_project_budget", AsyncMock(return_value=created_budget)),
        ):
            await service.create_project_budget(session, data, actor_id="admin-1", actor_name="Admin")

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.PROJECT_BUDGET_CREATED
        assert event_dto.entity_type == ActivityEntityType.PROJECT_BUDGET_GROUP
        assert event_dto.entity_id == created_budget.budget_id
        assert event_dto.actor_id == "admin-1"
        assert event_dto.attributes == {"project_name": "my-project"}
        assert call_args[1] is session

    @pytest.mark.asyncio
    @patch("codemie.service.budget.project_budget_service.activity_event_repository")
    @patch("codemie.service.budget.project_budget_service.get_active_provider")
    @patch("codemie.service.budget.project_budget_service.project_member_budget_assignment_repository")
    @patch("codemie.service.budget.project_budget_service.project_budget_assignment_repository")
    @patch("codemie.service.budget.project_budget_service.budget_repository")
    async def test_create_project_budget_event_not_emitted_on_provider_failure(
        self,
        mock_budget_repo,
        mock_assignment_repo,
        mock_member_repo,
        mock_get_provider,
        mock_activity,
    ):
        """create_project_budget must NOT emit event when provider sync raises an exception."""
        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        created_budget = _make_budget(budget_id="proj-budget-abc123")
        mock_budget_repo.get_by_name = AsyncMock(return_value=None)
        mock_budget_repo.insert = AsyncMock(return_value=created_budget)

        mock_assignment_repo.get_active_by_project_category = AsyncMock(return_value=None)
        mock_assignment_repo.insert = AsyncMock(return_value=MagicMock(project_name="my-project"))

        mock_member_repo.insert_many = AsyncMock(return_value=[])

        provider = AsyncMock()
        provider.provider_name = "litellm"
        mock_get_provider.return_value = provider

        service = _make_project_service()
        data = _make_project_budget_create_request()

        # _sync_created_project_budget is patched directly; the RuntimeError propagates
        # as-is (the wrapping into ExtendedHTTPException lives inside that method).
        with (
            patch.object(service, "_ensure_project_budget_eligible", AsyncMock()),
            patch.object(service, "_get_active_member_user_ids", AsyncMock(return_value=[])),
            patch.object(
                service,
                "_ensure_shared_child_budget",
                AsyncMock(return_value=_make_budget(budget_id="shared-child")),
            ),
            patch.object(
                service, "_sync_created_project_budget", AsyncMock(side_effect=RuntimeError("provider failure"))
            ),
        ):
            with pytest.raises(RuntimeError):
                await service.create_project_budget(session, data, actor_id="admin-1")

        mock_activity.async_insert.assert_not_called()


class TestDeleteProjectBudgetActivityEvent:
    @pytest.mark.asyncio
    @patch("codemie.service.budget.project_budget_service.activity_event_repository")
    @patch("codemie.service.budget.project_budget_service.get_active_provider")
    @patch("codemie.service.budget.project_budget_service.project_member_budget_assignment_repository")
    @patch("codemie.service.budget.project_budget_service.project_budget_assignment_repository")
    @patch("codemie.service.budget.project_budget_service.budget_repository")
    async def test_delete_project_budget_emits_project_budget_deleted_event(
        self,
        mock_budget_repo,
        mock_assignment_repo,
        mock_member_repo,
        mock_get_provider,
        mock_activity,
    ):
        """delete_project_budget must emit PROJECT_BUDGET_DELETED after soft-deleting rows."""
        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        budget = _make_budget(budget_id="proj-budget-abc123")
        budget.budget_type = "project"
        budget.provider_metadata = {}

        mock_budget_repo.get_by_id = AsyncMock(return_value=budget)
        mock_budget_repo.list_active_child_budgets = AsyncMock(return_value=[])

        assignment = MagicMock()
        assignment.project_name = "my-project"
        mock_assignment_repo.get_active_by_budget_id = AsyncMock(return_value=assignment)
        mock_member_repo.get_active_by_budget_id = AsyncMock(return_value=[])

        provider = AsyncMock()
        mock_get_provider.return_value = provider

        service = _make_project_service()

        with (
            patch.object(service, "_delete_provider_member_allocations", AsyncMock()),
            patch.object(service, "_delete_provider_project_budget", AsyncMock()),
            patch.object(service, "_soft_delete_project_budget_rows", AsyncMock()),
        ):
            await service.delete_project_budget(session, "proj-budget-abc123", actor_id="admin-1")

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.PROJECT_BUDGET_DELETED
        assert event_dto.entity_type == ActivityEntityType.PROJECT_BUDGET_GROUP
        assert event_dto.entity_id == "proj-budget-abc123"
        assert event_dto.actor_id == "admin-1"
        assert call_args[1] is session

    @pytest.mark.asyncio
    @patch("codemie.service.budget.project_budget_service.activity_event_repository")
    @patch("codemie.service.budget.project_budget_service.budget_repository")
    async def test_delete_project_budget_event_not_emitted_when_budget_not_found(
        self,
        mock_budget_repo,
        mock_activity,
    ):
        """delete_project_budget must NOT emit event when the budget does not exist."""
        from codemie.core.exceptions import ExtendedHTTPException

        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        mock_budget_repo.get_by_id = AsyncMock(return_value=None)

        service = _make_project_service()

        with pytest.raises(ExtendedHTTPException):
            await service.delete_project_budget(session, "nonexistent-id", actor_id="admin-1")

        mock_activity.async_insert.assert_not_called()


class TestBulkSetUserBudgetsLogger:
    @pytest.mark.asyncio
    @patch("codemie.service.budget.budget_service.logger")
    async def test_bulk_set_user_budgets_logs_completion(self, mock_logger):
        """bulk_set_user_budgets emits a single completion logger.info after all users are processed."""
        session = AsyncMock()
        service = _make_service()

        with (
            patch.object(service, "validate_assignment_budget_categories", AsyncMock()),
            patch.object(
                service,
                "_load_bulk_budget_users",
                new=AsyncMock(return_value={"user-1": MagicMock(), "user-2": MagicMock()}),
            ),
            patch.object(service, "_persist_bulk_budget_assignments", new=AsyncMock()),
            patch.object(service, "_propagate_bulk_budget_assignments", new=AsyncMock()),
        ):
            await service.bulk_set_user_budgets(
                session,
                user_ids=["user-1", "user-2"],
                assignments={BudgetCategory.PLATFORM: "shared-budget"},
                actor_id="admin-1",
            )

        mock_logger.info.assert_called_once()
        assert "bulk_user_budget_assignment_completed" in mock_logger.info.call_args[0][0]
