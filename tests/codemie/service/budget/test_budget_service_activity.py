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
  - delete_budget emits BudgetManagementEvent.BUDGET_DELETED and enforces its guards
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api.security.user import User
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    BudgetManagementEvent,
)
from codemie.service.budget.budget_enums import AllocationMode, BudgetCategory, BudgetType, SyncStatus
from codemie.service.budget.budget_models import Budget
from codemie.service.budget.budget_service import BudgetService
from codemie.service.budget.provider import BudgetProviderMemberState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ACTOR = User(id="admin-1", email="admin@example.com", name="Admin One")
_ACTOR_1 = User(id="actor-1", email="actor@example.com", name="Actor One")


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

        await service.create_budget(session, data, actor=_ACTOR)

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.BUDGET_CREATED
        assert event_dto.entity_type == ActivityEntityType.BUDGET
        assert event_dto.entity_id == "new-budget"
        assert event_dto.entity_label == "Test Budget"
        assert event_dto.actor_id == "admin-1"
        assert event_dto.project_name is None
        assert event_dto.actor_email == "admin@example.com"
        assert event_dto.actor_display_name == "Admin One"
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
            await service.create_budget(session, data, actor=_ACTOR)

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
            await service.update_budget(session, "test-budget", data, actor=_ACTOR)

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.BUDGET_UPDATED
        assert event_dto.entity_type == ActivityEntityType.BUDGET
        assert event_dto.entity_id == "test-budget"
        assert event_dto.entity_label == "Test Budget"
        assert event_dto.actor_id == "admin-1"
        assert event_dto.project_name is None
        assert event_dto.actor_email == "admin@example.com"
        assert event_dto.actor_display_name == "Admin One"
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
            await service.update_budget(session, "predefined-budget", data, actor=_ACTOR)

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
        db_user.is_active = True
        db_user.deleted_at = None

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
        with patch.object(
            service, "validate_assignment_budget_categories", AsyncMock(return_value={"test-budget": "Test Budget"})
        ):
            await service.assign_budget_to_user(
                session,
                user_id="user-123",
                assignments={BudgetCategory.PLATFORM: "test-budget"},
                actor=_ACTOR,
            )

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.USER_BUDGET_ASSIGNED
        assert event_dto.entity_type == ActivityEntityType.USER_BUDGET_ASSIGNMENT
        assert event_dto.entity_id == "user-123"
        assert event_dto.entity_label == "Test Budget"
        assert event_dto.actor_id == "admin-1"
        assert event_dto.attributes == {"budget_id": "test-budget"}
        assert event_dto.project_name is None
        assert event_dto.actor_email == "admin@example.com"
        assert event_dto.actor_display_name == "Admin One"
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
        db_user.is_active = True
        db_user.deleted_at = None

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
                actor=_ACTOR,
            )

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.USER_BUDGET_REMOVED
        assert event_dto.entity_type == ActivityEntityType.USER_BUDGET_ASSIGNMENT
        assert event_dto.entity_id == "user-123"
        assert event_dto.entity_label == BudgetCategory.PLATFORM.value
        assert event_dto.actor_id == "admin-1"
        assert event_dto.attributes == {"category": BudgetCategory.PLATFORM.value}
        assert event_dto.project_name is None
        assert event_dto.actor_email == "admin@example.com"
        assert event_dto.actor_display_name == "Admin One"
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
        db_user.is_active = True
        db_user.deleted_at = None

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

        with patch.object(
            service,
            "validate_assignment_budget_categories",
            AsyncMock(return_value={"platform-budget": "Platform Budget", "cli-budget": "CLI Budget"}),
        ):
            await service.assign_budget_to_user(
                session,
                user_id="user-123",
                assignments={
                    BudgetCategory.PLATFORM: "platform-budget",
                    BudgetCategory.CLI: "cli-budget",
                },
                actor=_ACTOR,
            )

        assert mock_activity.async_insert.call_count == 2
        emitted_budget_ids = {call[0][0].attributes["budget_id"] for call in mock_activity.async_insert.call_args_list}
        assert emitted_budget_ids == {"platform-budget", "cli-budget"}
        for call in mock_activity.async_insert.call_args_list:
            emitted_event = call[0][0]
            assert emitted_event.project_name is None
            assert emitted_event.actor_email == "admin@example.com"
            assert emitted_event.actor_display_name == "Admin One"


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


class TestBudgetGrantToDeactivatedUser:
    @pytest.mark.asyncio
    async def test_assign_budget_refused_for_deactivated_user(self):
        db_user = MagicMock(id="user-1", email="alice@example.com", is_active=False, deleted_at=None)
        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=db_user))))
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _make_service().assign_budget_to_user(
                session, user_id="user-1", assignments={BudgetCategory.PLATFORM: "b1"}, actor=_ACTOR
            )

        assert exc_info.value.code == 409
        assert exc_info.value.message == "User is deactivated"

    def test_clearing_a_budget_is_allowed_for_deactivated_user(self):
        db_user = MagicMock(id="user-1", email="alice@example.com", is_active=False, deleted_at=None)

        _make_service()._reject_budget_grant_to_deactivated([db_user], {BudgetCategory.PLATFORM: None})


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
            await service.create_project_budget(session, data, actor=_ACTOR)

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.PROJECT_BUDGET_CREATED
        assert event_dto.entity_type == ActivityEntityType.PROJECT_BUDGET_GROUP
        assert event_dto.entity_id == created_budget.budget_id
        assert event_dto.actor_id == "admin-1"
        assert event_dto.attributes == {"project_name": "my-project"}
        assert event_dto.project_name == "my-project"
        assert event_dto.actor_email == "admin@example.com"
        assert event_dto.actor_display_name == "Admin One"
        assert event_dto.entity_label == created_budget.name
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
                await service.create_project_budget(session, data, actor=_ACTOR)

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
            await service.delete_project_budget(session, "proj-budget-abc123", actor=_ACTOR)

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.PROJECT_BUDGET_DELETED
        assert event_dto.entity_type == ActivityEntityType.PROJECT_BUDGET_GROUP
        assert event_dto.entity_id == "proj-budget-abc123"
        assert event_dto.actor_id == "admin-1"
        assert event_dto.project_name == "my-project"
        assert event_dto.actor_email == "admin@example.com"
        assert event_dto.actor_display_name == "Admin One"
        assert event_dto.entity_label == budget.name
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
            await service.delete_project_budget(session, "nonexistent-id", actor=_ACTOR)

        mock_activity.async_insert.assert_not_called()


class TestUpdateProjectBudgetActivityEvent:
    @pytest.mark.asyncio
    async def test_update_project_budget_emits_project_name_and_actor_snapshot(self):
        """update_project_budget must stamp project_name from the active assignment; no actor display name available."""
        from codemie.rest_api.routers.project_budget_router import ProjectBudgetUpdateRequest

        service = _make_project_service()
        session = AsyncMock()
        budget = SimpleNamespace(
            budget_id="proj-budget-1",
            budget_type="project",
            budget_category="cli",
            name="Proj Budget One",
            soft_budget=10.0,
            max_budget=100.0,
            budget_duration="30d",
        )
        assignment = SimpleNamespace(project_name="proj-x")
        data = ProjectBudgetUpdateRequest(soft_budget=15.0)

        with (
            patch(
                "codemie.service.budget.project_budget_service.budget_repository.get_by_id",
                AsyncMock(return_value=budget),
            ),
            patch(
                "codemie.service.budget.project_budget_service.budget_repository.update",
                AsyncMock(return_value=budget),
            ),
            patch.object(
                service,
                "_sync_updated_project_budget",
                AsyncMock(return_value=(budget, assignment, SimpleNamespace())),
            ),
            patch.object(service, "_resync_member_allocations", AsyncMock()),
            patch(
                "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
                AsyncMock(),
            ) as mock_insert,
        ):
            await service.update_project_budget(session, "proj-budget-1", data, actor=_ACTOR_1)

        mock_insert.assert_called_once()
        event_dto = mock_insert.call_args[0][0]
        assert event_dto.project_name == "proj-x"
        assert event_dto.actor_email == "actor@example.com"
        assert event_dto.actor_display_name == "Actor One"
        assert event_dto.entity_label == "Proj Budget One"

    @pytest.mark.asyncio
    async def test_update_project_budget_non_amount_change_still_stamps_project_name(self):
        """A non-amount field change (e.g. name only) must still resolve and stamp
        project_name via the active assignment lookup, even though the amounts/models sync
        branch (which normally resolves the assignment) is not entered."""
        from codemie.rest_api.routers.project_budget_router import ProjectBudgetUpdateRequest

        service = _make_project_service()
        session = AsyncMock()
        budget = SimpleNamespace(
            budget_id="proj-budget-1",
            budget_type="project",
            budget_category="cli",
            name="Proj Budget One",
            soft_budget=10.0,
            max_budget=100.0,
            budget_duration="30d",
            provider_metadata={},
        )
        assignment = SimpleNamespace(project_name="proj-x")
        data = ProjectBudgetUpdateRequest(name="Renamed Budget")

        with (
            patch(
                "codemie.service.budget.project_budget_service.budget_repository.get_by_id",
                AsyncMock(return_value=budget),
            ),
            patch(
                "codemie.service.budget.project_budget_service.budget_repository.update",
                AsyncMock(return_value=budget),
            ),
            patch.object(service, "_sync_updated_project_budget", AsyncMock()) as mock_sync,
            patch.object(service, "_member_sync_failed", AsyncMock(return_value=False)),
            patch.object(service, "_resync_member_allocations", AsyncMock()),
            patch(
                "codemie.service.budget.project_budget_service.project_budget_assignment_repository."
                "get_active_by_budget_id",
                AsyncMock(return_value=assignment),
            ) as mock_get_assignment,
            patch(
                "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
                AsyncMock(),
            ) as mock_insert,
        ):
            await service.update_project_budget(session, "proj-budget-1", data, actor=_ACTOR_1)

        mock_sync.assert_not_called()
        mock_get_assignment.assert_called_once_with(session, "proj-budget-1")
        mock_insert.assert_called_once()
        event_dto = mock_insert.call_args[0][0]
        assert event_dto.project_name == "proj-x"
        assert event_dto.actor_email == "actor@example.com"
        assert event_dto.actor_display_name == "Actor One"
        assert event_dto.entity_label == "Proj Budget One"


class TestRebalanceProjectBudgetActivityEvent:
    @pytest.mark.asyncio
    async def test_rebalance_project_budget_emits_project_name_and_actor_snapshot(self):
        service = _make_project_service()
        session = AsyncMock()
        budget = SimpleNamespace(
            budget_id="proj-budget-1",
            budget_category="cli",
            name="Proj Budget One",
            max_budget=100.0,
            soft_budget=80.0,
        )
        assignment = SimpleNamespace(project_name="proj-x", budget_category="cli")

        with (
            patch.object(service, "get_project_budget", AsyncMock(return_value=(budget, assignment, []))),
            patch.object(service, "_resync_member_allocations", AsyncMock()),
            patch(
                "codemie.service.budget.project_budget_service.get_active_provider",
                return_value=SimpleNamespace(),
            ),
            patch(
                "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
                AsyncMock(),
            ) as mock_insert,
        ):
            await service.rebalance_project_budget(session, "proj-budget-1", actor=_ACTOR_1)

        event_dto = mock_insert.call_args[0][0]
        assert event_dto.project_name == "proj-x"
        assert event_dto.actor_email == "actor@example.com"
        assert event_dto.actor_display_name == "Actor One"
        assert event_dto.entity_label == "Proj Budget One"


class TestResetProjectBudgetActivityEvent:
    @pytest.mark.asyncio
    async def test_reset_project_budget_emits_project_name_and_actor_snapshot(self):
        service = _make_project_service()
        session = AsyncMock()
        budget = SimpleNamespace(
            budget_id="proj-budget-1",
            budget_category="cli",
            name="Proj Budget One",
            provider_metadata={},
            budget_reset_at=None,
        )
        assignment = SimpleNamespace(project_name="proj-x")
        provider = SimpleNamespace(
            reset_project_budget_spend=AsyncMock(
                return_value=SimpleNamespace(
                    provider="litellm",
                    provider_budget_ref="ref",
                    sync_status=SyncStatus.OK,
                    budget_reset_at=None,
                    metadata={},
                )
            ),
        )

        with (
            patch.object(service, "get_project_budget", AsyncMock(return_value=(budget, assignment, []))),
            patch(
                "codemie.service.budget.project_budget_service.get_active_provider",
                return_value=provider,
            ),
            patch(
                "codemie.service.budget.project_budget_service.budget_repository.update",
                AsyncMock(return_value=budget),
            ),
            patch(
                "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
                AsyncMock(),
            ) as mock_insert,
        ):
            await service.reset_project_budget(session, "proj-budget-1", actor=_ACTOR_1)

        event_dto = mock_insert.call_args[0][0]
        assert event_dto.project_name == "proj-x"
        assert event_dto.actor_email == "actor@example.com"
        assert event_dto.actor_display_name == "Actor One"
        assert event_dto.entity_label == "Proj Budget One"


class TestOverrideMemberAllocationActivityEvent:
    @pytest.mark.asyncio
    async def test_override_member_allocation_emits_project_name_and_actor_snapshot(self):
        service = _make_project_service()
        session = AsyncMock()
        allocation = SimpleNamespace(
            id="alloc-1",
            user_id="user-a",
            shared_budget_id="proj-budget-1:shared",
            override_budget_id=None,
        )
        budget = SimpleNamespace(
            budget_id="proj-budget-1", budget_type="project", budget_category="cli", name="Proj Budget One"
        )
        assignment = SimpleNamespace(project_name="proj-a", budget_category="cli")

        with (
            patch.object(service, "get_project_budget", AsyncMock(return_value=(budget, assignment, [allocation]))),
            patch(
                "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository."
                "update_member_override",
                AsyncMock(return_value=allocation),
            ),
            patch.object(
                service,
                "_ensure_override_child_budget",
                AsyncMock(return_value=SimpleNamespace(budget_id="override-bud-1")),
            ),
            patch(
                "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository."
                "update_member_budget_routing",
                AsyncMock(return_value=allocation),
            ),
            patch.object(service, "rebalance_project_budget", AsyncMock()),
            patch(
                "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
                AsyncMock(),
            ) as mock_insert,
        ):
            await service.override_member_allocation(
                session=session,
                budget_id="proj-budget-1",
                user_id="user-a",
                allocated_max_budget=20.0,
                allocated_soft_budget=15.0,
                override_reason="manual",
                actor=_ACTOR_1,
            )

        event_dto = mock_insert.call_args[0][0]
        assert event_dto.project_name == "proj-a"
        assert event_dto.actor_email == "actor@example.com"
        assert event_dto.actor_display_name == "Actor One"
        assert event_dto.entity_label == "Proj Budget One"


class TestClearMemberOverrideActivityEvent:
    @pytest.mark.asyncio
    async def test_clear_member_override_emits_project_name_and_actor_snapshot(self):
        service = _make_project_service()
        session = AsyncMock()
        allocation = SimpleNamespace(
            id="alloc-1",
            user_id="user-a",
            project_budget_id="proj-budget-1",
            project_name="proj-a",
            budget_category="cli",
            override_budget_id=None,
            shared_budget_id=None,
            effective_budget_id=None,
            allocation_mode=AllocationMode.EQUAL.value,
            allocated_max_budget=50.0,
            allocated_soft_budget=40.0,
            override_reason=None,
        )
        budget = SimpleNamespace(
            budget_id="proj-budget-1",
            budget_type="project",
            budget_category="cli",
            name="Proj Budget One",
            budget_duration="30d",
            budget_reset_at=None,
            max_budget=100.0,
            soft_budget=80.0,
        )
        assignment = SimpleNamespace(project_name="proj-a", budget_category="cli")
        shared_budget_result = SimpleNamespace(budget_id="proj-budget-1:shared")
        member_state = BudgetProviderMemberState(
            provider="litellm",
            provider_member_ref="ref-1",
            provider_budget_id="provider-bud-1",
            sync_status=SyncStatus.OK,
        )
        mock_provider = SimpleNamespace(sync_member_allocation=AsyncMock(return_value=member_state))

        with (
            patch(
                "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository."
                "clear_member_override",
                AsyncMock(return_value=allocation),
            ),
            patch.object(service, "get_project_budget", AsyncMock(return_value=(budget, assignment, []))),
            patch(
                "codemie.service.budget.project_budget_service.budget_repository.get_by_id",
                AsyncMock(return_value=None),
            ),
            patch.object(service, "_ensure_shared_child_budget", AsyncMock(return_value=shared_budget_result)),
            patch(
                "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository."
                "update_member_budget_routing",
                AsyncMock(return_value=allocation),
            ),
            patch(
                "codemie.service.budget.project_budget_service.get_active_provider",
                return_value=mock_provider,
            ),
            patch.object(service, "_persist_child_budget_provider_state", AsyncMock()),
            patch(
                "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository."
                "update_provider_metadata",
                AsyncMock(),
            ),
            patch.object(service, "rebalance_project_budget", AsyncMock()),
            patch(
                "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
                AsyncMock(),
            ) as mock_insert,
        ):
            await service.clear_member_override(
                session=session, budget_id="proj-budget-1", user_id="user-a", actor=_ACTOR_1
            )

        event_dto = mock_insert.call_args[0][0]
        assert event_dto.project_name == "proj-a"
        assert event_dto.actor_email == "actor@example.com"
        assert event_dto.actor_display_name == "Actor One"
        assert event_dto.entity_label == "Proj Budget One"


class TestCreateProjectBudgetGroupActivityEvent:
    @pytest.mark.asyncio
    @patch("codemie.service.budget.project_budget_service.activity_event_repository")
    @patch("codemie.service.budget.project_budget_service.get_active_provider")
    @patch("codemie.service.budget.project_budget_service.project_member_budget_assignment_repository")
    @patch("codemie.service.budget.project_budget_service.project_budget_assignment_repository")
    @patch("codemie.service.budget.project_budget_service.project_budget_group_repository")
    @patch("codemie.service.budget.project_budget_service.budget_repository")
    async def test_create_project_budget_group_emits_project_name_and_actor_snapshot(
        self,
        mock_budget_repo,
        mock_group_repo,
        mock_assignment_repo,
        mock_member_repo,
        mock_get_provider,
        mock_activity,
    ):
        from codemie.rest_api.models.project_budget import CategoryBudgetSpec
        from codemie.rest_api.routers.project_budget_router import ProjectBudgetGroupCreateRequest

        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        group = MagicMock(id="group-1", project_name="proj-g")
        group.name = "Group G"
        mock_group_repo.get_active_by_project = AsyncMock(return_value=None)
        mock_group_repo.insert = AsyncMock(return_value=group)

        created_budget = _make_budget(budget_id="proj-g-platform-abc123")
        mock_budget_repo.insert = AsyncMock(return_value=created_budget)

        mock_assignment_repo.insert = AsyncMock(return_value=MagicMock(project_name="proj-g"))
        mock_member_repo.insert_many = AsyncMock(return_value=[])

        provider = AsyncMock()
        mock_get_provider.return_value = provider

        service = _make_project_service()
        data = ProjectBudgetGroupCreateRequest(
            project_name="proj-g",
            name="Group G",
            total_amount=100.0,
            budget_duration="30d",
            categories={"platform": CategoryBudgetSpec(pct=100)},
        )

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
            await service.create_project_budget_group(session, data, actor=_ACTOR)

        mock_activity.async_insert.assert_called_once()
        event_dto = mock_activity.async_insert.call_args[0][0]
        assert event_dto.project_name == "proj-g"
        assert event_dto.actor_email == "admin@example.com"
        assert event_dto.actor_display_name == "Admin One"
        assert event_dto.entity_label == "Group G"


class TestUpdateProjectBudgetGroupActivityEvent:
    @pytest.mark.asyncio
    async def test_update_project_budget_group_emits_project_name_and_actor_snapshot(self):
        from codemie.rest_api.models.project_budget import CategoryBudgetSpecUpdate
        from codemie.rest_api.routers.project_budget_router import ProjectBudgetGroupUpdateRequest

        service = _make_project_service()
        session = AsyncMock()
        session.refresh = AsyncMock()

        group = SimpleNamespace(
            id="group-1",
            project_name="proj-g",
            name="Group G",
            deleted_at=None,
            budget_duration="30d",
            description=None,
        )
        data = ProjectBudgetGroupUpdateRequest(
            total_amount=100.0,
            categories={"platform": CategoryBudgetSpecUpdate(pct=100)},
        )
        full_result = SimpleNamespace(categories=[], total_amount=100.0)

        with (
            patch(
                "codemie.service.budget.project_budget_service.project_budget_group_repository.get_by_id",
                AsyncMock(return_value=group),
            ),
            patch.object(service, "check_group_request", AsyncMock()),
            patch.object(service, "_update_group_scalar_fields", AsyncMock()),
            patch.object(service, "_load_group_full_result", AsyncMock(return_value=full_result)),
            patch.object(service, "_update_group_categories", AsyncMock()),
            patch(
                "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
                AsyncMock(),
            ) as mock_insert,
        ):
            await service.update_project_budget_group(session, "group-1", data, actor=_ACTOR_1)

        event_dto = mock_insert.call_args[0][0]
        assert event_dto.project_name == "proj-g"
        assert event_dto.actor_email == "actor@example.com"
        assert event_dto.actor_display_name == "Actor One"
        assert event_dto.entity_label == "Group G"


class TestResetProjectBudgetGroupActivityEvent:
    @pytest.mark.asyncio
    async def test_reset_project_budget_group_emits_project_name_and_actor_snapshot(self):
        service = _make_project_service()
        session = AsyncMock()
        group = SimpleNamespace(id="group-1", project_name="proj-g", name="Group G", deleted_at=None)

        with (
            patch(
                "codemie.service.budget.project_budget_service.project_budget_group_repository.get_by_id",
                AsyncMock(return_value=group),
            ),
            patch(
                "codemie.service.budget.project_budget_service.project_budget_assignment_repository."
                "get_active_by_group_id",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                service,
                "_load_group_full_result",
                AsyncMock(return_value=SimpleNamespace(group=group, categories=[])),
            ),
            patch(
                "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
                AsyncMock(),
            ) as mock_insert,
        ):
            await service.reset_project_budget_group(session, "group-1", actor=_ACTOR_1)

        event_dto = mock_insert.call_args[0][0]
        assert event_dto.project_name == "proj-g"
        assert event_dto.actor_email == "actor@example.com"
        assert event_dto.actor_display_name == "Actor One"
        assert event_dto.entity_label == "Group G"


class TestRebalanceProjectBudgetGroupActivityEvent:
    @pytest.mark.asyncio
    async def test_rebalance_project_budget_group_emits_project_name_and_actor_snapshot(self):
        service = _make_project_service()
        session = AsyncMock()
        group = SimpleNamespace(id="group-1", project_name="proj-g", name="Group G", deleted_at=None)

        with (
            patch(
                "codemie.service.budget.project_budget_service.project_budget_group_repository.get_by_id",
                AsyncMock(return_value=group),
            ),
            patch(
                "codemie.service.budget.project_budget_service.project_budget_assignment_repository."
                "get_active_by_group_id",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                service,
                "_load_group_full_result",
                AsyncMock(return_value=SimpleNamespace(group=group, categories=[])),
            ),
            patch(
                "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
                AsyncMock(),
            ) as mock_insert,
        ):
            await service.rebalance_project_budget_group(session, "group-1", actor=_ACTOR_1)

        event_dto = mock_insert.call_args[0][0]
        assert event_dto.project_name == "proj-g"
        assert event_dto.actor_email == "actor@example.com"
        assert event_dto.actor_display_name == "Actor One"
        assert event_dto.entity_label == "Group G"


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
                new=AsyncMock(
                    return_value={
                        "user-1": MagicMock(is_active=True, deleted_at=None),
                        "user-2": MagicMock(is_active=True, deleted_at=None),
                    }
                ),
            ),
            patch.object(service, "_persist_bulk_budget_assignments", new=AsyncMock()),
            patch.object(service, "_propagate_bulk_budget_assignments", new=AsyncMock()),
        ):
            await service.bulk_set_user_budgets(
                session,
                user_ids=["user-1", "user-2"],
                assignments={BudgetCategory.PLATFORM: "shared-budget"},
                actor=_ACTOR,
            )

        mock_logger.info.assert_called_once()
        assert "bulk_user_budget_assignment_completed" in mock_logger.info.call_args[0][0]


# ---------------------------------------------------------------------------
# TestDeleteBudget (EPMCDME-15633)
# ---------------------------------------------------------------------------


def _mock_delete_repo(
    mock_budget_repo, *, budget=None, assignment_count: int = 0, all_assignment_count: int | None = None
) -> None:
    """assignment_count: non-deleted users; all_assignment_count: every assignment row (deleted users too)."""
    mock_budget_repo.get_by_id_for_update = AsyncMock(return_value=budget)
    live_counts = {budget.budget_id: assignment_count} if budget is not None and assignment_count else {}
    mock_budget_repo.count_users_by_budget_ids = AsyncMock(return_value=live_counts)
    mock_budget_repo.count_assignments = AsyncMock(
        return_value=assignment_count if all_assignment_count is None else all_assignment_count
    )
    mock_budget_repo.delete = AsyncMock()


def _mock_provider(mock_get_provider, **kwargs) -> AsyncMock:
    provider = AsyncMock()
    provider.provider_name = "litellm"
    provider.delete_global_budget = AsyncMock(**kwargs)
    mock_get_provider.return_value = provider
    return provider


@patch("codemie.service.budget.budget_service.activity_event_repository")
@patch("codemie.service.budget.budget_service.get_active_provider")
@patch("codemie.service.budget.budget_service.budget_config")
@patch("codemie.service.budget.budget_service.budget_repository")
class TestDeleteBudget:
    @pytest.mark.asyncio
    async def test_deletes_budget_calls_provider_and_emits_event(
        self, mock_budget_repo, mock_budget_cfg, mock_get_provider, mock_activity
    ):
        mock_activity.async_insert = AsyncMock()
        mock_budget_cfg.predefined_budgets = []
        _mock_delete_repo(mock_budget_repo, budget=_make_budget(budget_id="manual-budget"))
        provider = _mock_provider(mock_get_provider)
        session = AsyncMock()

        budget = mock_budget_repo.get_by_id_for_update.return_value
        await _make_service().delete_budget(session, "manual-budget", actor=_ACTOR)

        mock_budget_repo.delete.assert_awaited_once_with(session, "manual-budget")
        provider.delete_global_budget.assert_awaited_once_with(budget_id="manual-budget")
        mock_activity.async_insert.assert_awaited_once()
        event_dto, event_session = mock_activity.async_insert.await_args.args
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.BUDGET_DELETED
        assert event_dto.entity_type == ActivityEntityType.BUDGET
        assert event_dto.entity_id == "manual-budget"
        assert event_dto.entity_label == budget.name
        assert event_dto.project_name is None
        assert event_dto.actor_id == _ACTOR.id
        assert event_dto.actor_email == _ACTOR.email
        assert event_dto.actor_display_name == _ACTOR.name
        assert event_dto.attributes == {"reason": "manual_delete"}
        assert event_session is session

    @pytest.mark.asyncio
    async def test_locks_row_before_counting_assignments(
        self, mock_budget_repo, mock_budget_cfg, mock_get_provider, mock_activity
    ):
        """The row lock must be taken before the count so a concurrent assignment cannot slip in."""
        mock_activity.async_insert = AsyncMock()
        mock_budget_cfg.predefined_budgets = []
        _mock_delete_repo(mock_budget_repo, budget=_make_budget(budget_id="manual-budget"))
        _mock_provider(mock_get_provider)
        call_order = MagicMock()
        call_order.attach_mock(mock_budget_repo.get_by_id_for_update, "lock")
        call_order.attach_mock(mock_budget_repo.count_users_by_budget_ids, "count")
        call_order.attach_mock(mock_budget_repo.delete, "delete")

        await _make_service().delete_budget(AsyncMock(), "manual-budget", actor=_ACTOR)

        assert [c[0] for c in call_order.mock_calls] == ["lock", "count", "delete"]
        mock_budget_repo.get_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_provider_failure_rolls_back_and_returns_502(
        self, mock_budget_repo, mock_budget_cfg, mock_get_provider, mock_activity
    ):
        from codemie.core.exceptions import ExtendedHTTPException

        mock_activity.async_insert = AsyncMock()
        mock_budget_cfg.predefined_budgets = []
        _mock_delete_repo(mock_budget_repo, budget=_make_budget(budget_id="manual-budget"))
        _mock_provider(mock_get_provider, side_effect=RuntimeError("provider down"))
        session = AsyncMock()

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _make_service().delete_budget(session, "manual-budget", actor=_ACTOR)

        assert exc_info.value.code == 502
        assert exc_info.value.message == "Failed to delete budget in enforcement provider"
        session.rollback.assert_awaited_once()
        mock_activity.async_insert.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("count", "suffix"), [(1, "1 user assigned"), (3, "3 users assigned")])
    async def test_rejects_budget_with_user_assignments(
        self, mock_budget_repo, mock_budget_cfg, mock_get_provider, mock_activity, count, suffix
    ):
        from codemie.core.exceptions import ExtendedHTTPException

        mock_activity.async_insert = AsyncMock()
        mock_budget_cfg.predefined_budgets = []
        _mock_delete_repo(mock_budget_repo, budget=_make_budget(budget_id="busy-budget"), assignment_count=count)
        provider = _mock_provider(mock_get_provider)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _make_service().delete_budget(AsyncMock(), "busy-budget", actor=_ACTOR)

        assert exc_info.value.code == 409
        assert exc_info.value.message == f"Budget 'busy-budget' is in use: {suffix}"
        mock_budget_repo.delete.assert_not_called()
        provider.delete_global_budget.assert_not_called()
        mock_activity.async_insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_deletes_budget_whose_only_user_is_deleted(
        self, mock_budget_repo, mock_budget_cfg, mock_get_provider, mock_activity
    ):
        mock_activity.async_insert = AsyncMock()
        mock_budget_cfg.predefined_budgets = []
        _mock_delete_repo(
            mock_budget_repo,
            budget=_make_budget(budget_id="orphan-budget"),
            assignment_count=0,
            all_assignment_count=1,
        )
        _mock_provider(mock_get_provider)
        session = AsyncMock()

        await _make_service().delete_budget(session, "orphan-budget", actor=_ACTOR)

        mock_budget_repo.delete.assert_awaited_once_with(session, "orphan-budget")
        mock_budget_repo.count_assignments.assert_not_called()
        mock_activity.async_insert.assert_awaited_once()
        event_dto = mock_activity.async_insert.await_args.args[0]
        assert event_dto.event_type == BudgetManagementEvent.BUDGET_DELETED
        assert event_dto.attributes == {"reason": "manual_delete"}

    @pytest.mark.asyncio
    async def test_ignores_deleted_users_in_in_use_count(
        self, mock_budget_repo, mock_budget_cfg, mock_get_provider, mock_activity
    ):
        from codemie.core.exceptions import ExtendedHTTPException

        mock_activity.async_insert = AsyncMock()
        mock_budget_cfg.predefined_budgets = []
        _mock_delete_repo(
            mock_budget_repo,
            budget=_make_budget(budget_id="mixed-budget"),
            assignment_count=1,
            all_assignment_count=2,
        )
        provider = _mock_provider(mock_get_provider)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _make_service().delete_budget(AsyncMock(), "mixed-budget", actor=_ACTOR)

        assert exc_info.value.code == 409
        assert exc_info.value.message == "Budget 'mixed-budget' is in use: 1 user assigned"
        mock_budget_repo.delete.assert_not_called()
        provider.delete_global_budget.assert_not_called()
        mock_activity.async_insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_predefined_budget_before_lookup(
        self, mock_budget_repo, mock_budget_cfg, mock_get_provider, mock_activity
    ):
        from codemie.core.exceptions import ExtendedHTTPException

        mock_activity.async_insert = AsyncMock()
        predefined = MagicMock()
        predefined.budget_id = "predefined-budget"
        mock_budget_cfg.predefined_budgets = [predefined]
        _mock_delete_repo(mock_budget_repo, budget=_make_budget(budget_id="predefined-budget"))
        provider = _mock_provider(mock_get_provider)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _make_service().delete_budget(AsyncMock(), "predefined-budget", actor=_ACTOR)

        assert exc_info.value.code == 403
        assert exc_info.value.message == "Budget 'predefined-budget' is preconfigured and cannot be deleted via API"
        mock_budget_repo.get_by_id_for_update.assert_not_called()
        mock_budget_repo.delete.assert_not_called()
        provider.delete_global_budget.assert_not_called()
        mock_activity.async_insert.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "budget",
        [None, _make_budget(budget_id="missing-budget", budget_type=BudgetType.PROJECT)],
        ids=["unknown", "project_budget"],
    )
    async def test_returns_404_for_unknown_or_non_global_budget(
        self, mock_budget_repo, mock_budget_cfg, mock_get_provider, mock_activity, budget
    ):
        from codemie.core.exceptions import ExtendedHTTPException

        mock_activity.async_insert = AsyncMock()
        mock_budget_cfg.predefined_budgets = []
        _mock_delete_repo(mock_budget_repo, budget=budget)
        provider = _mock_provider(mock_get_provider)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _make_service().delete_budget(AsyncMock(), "missing-budget", actor=_ACTOR)

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Budget 'missing-budget' not found"
        mock_budget_repo.count_assignments.assert_not_called()
        mock_budget_repo.delete.assert_not_called()
        provider.delete_global_budget.assert_not_called()
        mock_activity.async_insert.assert_not_called()


# TestSyncBudgetsFromProviderOrphanDeleteActivityEvent
# ---------------------------------------------------------------------------


class TestSyncBudgetsFromProviderOrphanDeleteActivityEvent:
    @pytest.mark.asyncio
    @patch("codemie.service.budget.budget_service.activity_event_repository")
    @patch("codemie.service.budget.budget_service.get_active_provider")
    @patch("codemie.service.budget.budget_service.budget_repository")
    async def test_sync_budgets_from_provider_emits_budget_deleted_event_for_orphan(
        self, mock_budget_repo, mock_get_provider, mock_activity
    ):
        """sync_budgets_from_provider must emit BUDGET_DELETED with no project/actor snapshot for orphans."""
        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()
        session.commit = AsyncMock()

        provider = AsyncMock()
        provider.provider_name = "litellm"
        provider.list_global_budget_states = AsyncMock(return_value=[])
        mock_get_provider.return_value = provider

        from datetime import datetime, timezone

        orphan_budget = _make_budget(budget_id="orphan-budget", created_at=datetime.now(timezone.utc))
        from codemie.service.budget.budget_enums import BudgetType

        mock_budget_repo.get_all_keyed_by_id = AsyncMock(
            return_value={"orphan-budget": SimpleNamespace(budget_type=BudgetType.GLOBAL, name="Orphan Budget")}
        )
        mock_budget_repo.delete = AsyncMock()
        mock_budget_repo.list_paginated = AsyncMock(return_value=([orphan_budget], 1))

        service = _make_service()

        await service.sync_budgets_from_provider(session, actor=_ACTOR)

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
        assert event_dto.event_type == BudgetManagementEvent.BUDGET_DELETED
        assert event_dto.entity_type == ActivityEntityType.BUDGET
        assert event_dto.entity_id == "orphan-budget"
        assert event_dto.entity_label == "Orphan Budget"
        assert event_dto.actor_id == "admin-1"
        assert event_dto.project_name is None
        assert event_dto.actor_email == "admin@example.com"
        assert event_dto.actor_display_name == "Admin One"
        assert call_args[1] is session


# ---------------------------------------------------------------------------
# TestPersistBulkBudgetAssignmentsActivityEvent
# ---------------------------------------------------------------------------


class TestPersistBulkBudgetAssignmentsActivityEvent:
    @pytest.mark.asyncio
    @patch("codemie.service.budget.budget_service.activity_event_repository")
    @patch("codemie.service.budget.budget_service.budget_repository")
    async def test_persist_bulk_budget_assignments_emits_assigned_event_with_actor_snapshot(
        self, mock_budget_repo, mock_activity
    ):
        """_persist_bulk_budget_assignments must emit USER_BUDGET_ASSIGNED with the actor snapshot and the budget name as label."""
        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        mock_budget_repo.upsert_user_category_assignment = AsyncMock()

        service = _make_service()

        await service._persist_bulk_budget_assignments(
            session,
            user_ids=["user-1"],
            assignments={BudgetCategory.PLATFORM: "platform-budget"},
            actor=_ACTOR,
            budget_names={"platform-budget": "Platform Budget"},
        )

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.event_type == BudgetManagementEvent.USER_BUDGET_ASSIGNED
        assert event_dto.entity_id == "user-1"
        assert event_dto.entity_label == "Platform Budget"
        assert event_dto.actor_id == "admin-1"
        assert event_dto.project_name is None
        assert event_dto.actor_email == "admin@example.com"
        assert event_dto.actor_display_name == "Admin One"

    @pytest.mark.asyncio
    @patch("codemie.service.budget.budget_service.activity_event_repository")
    @patch("codemie.service.budget.budget_service.budget_repository")
    async def test_persist_bulk_budget_assignments_emits_removed_event_with_actor_snapshot(
        self, mock_budget_repo, mock_activity
    ):
        """_persist_bulk_budget_assignments must emit USER_BUDGET_REMOVED with the actor snapshot."""
        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        mock_budget_repo.delete_user_category_assignment = AsyncMock()

        service = _make_service()

        await service._persist_bulk_budget_assignments(
            session,
            user_ids=["user-1"],
            assignments={BudgetCategory.PLATFORM: None},
            actor=_ACTOR,
            budget_names={},
        )

        mock_activity.async_insert.assert_called_once()
        call_args = mock_activity.async_insert.call_args[0]
        event_dto = call_args[0]
        assert event_dto.event_type == BudgetManagementEvent.USER_BUDGET_REMOVED
        assert event_dto.entity_id == "user-1"
        assert event_dto.entity_label == BudgetCategory.PLATFORM.value
        assert event_dto.actor_id == "admin-1"
        assert event_dto.project_name is None
        assert event_dto.actor_email == "admin@example.com"
        assert event_dto.actor_display_name == "Admin One"


# ---------------------------------------------------------------------------
# TestActiveProjectBudgetRestoreActivityEvent
# ---------------------------------------------------------------------------


class TestActiveProjectBudgetRestoreActivityEvent:
    @pytest.mark.asyncio
    async def test_restore_project_emits_budget_restored_event_without_snapshot(self):
        """ActiveProjectBudgetRestoreService._restore_project must emit PROJECT_BUDGET_RESTORED
        with the resolved project_name but no actor snapshot (no actor context in this scheduler)."""
        from codemie.service.budget.active_project_budget_restore_service import (
            ActiveProjectBudgetRestoreService,
        )

        session = AsyncMock()
        budget = SimpleNamespace(budget_id="budget-1", name="Restored Budget", is_active=False)

        with (
            patch(
                "codemie.service.budget.active_project_budget_restore_service.budget_repository.list_stopped_project_budgets",
                AsyncMock(return_value=[budget]),
            ),
            patch(
                "codemie.service.budget.active_project_budget_restore_service.activity_event_repository.async_insert",
                AsyncMock(),
            ) as mock_insert,
        ):
            service = ActiveProjectBudgetRestoreService()
            count = await service._restore_project(session, "proj-x")

        assert count == 1
        mock_insert.assert_called_once()
        event_dto = mock_insert.call_args[0][0]
        assert event_dto.event_type == BudgetManagementEvent.PROJECT_BUDGET_RESTORED
        assert event_dto.entity_id == "budget-1"
        assert event_dto.entity_label == "Restored Budget"
        assert event_dto.project_name == "proj-x"
        assert event_dto.actor_email is None
        assert event_dto.actor_display_name is None


# ---------------------------------------------------------------------------
# TestInactiveCostCenterBudgetStopActivityEvent
# ---------------------------------------------------------------------------


class TestInactiveCostCenterBudgetStopActivityEvent:
    @pytest.mark.asyncio
    async def test_run_emits_budget_stopped_event_without_snapshot(self):
        """InactiveCostCenterBudgetStopService.run must emit PROJECT_BUDGET_STOPPED
        with the resolved project_name but no actor snapshot (no actor context in this scheduler)."""
        from codemie.service.budget.inactive_cost_center_budget_stop_service import (
            InactiveCostCenterBudgetStopService,
        )

        session = AsyncMock()
        budget = SimpleNamespace(budget_id="budget-1", name="Cost Center Budget", is_active=True)

        mock_admin_result = MagicMock()
        mock_admin_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_admin_result)

        with (
            patch(
                "codemie.service.budget.inactive_cost_center_budget_stop_service."
                "project_enrichment_repository.get_inactive_cost_center_names",
                AsyncMock(return_value=["cc-1"]),
            ),
            patch(
                "codemie.service.budget.inactive_cost_center_budget_stop_service."
                "application_repository.aget_project_names_by_cost_center_name",
                AsyncMock(return_value=["proj-x"]),
            ),
            patch(
                "codemie.service.budget.inactive_cost_center_budget_stop_service."
                "budget_repository.list_active_project_budgets",
                AsyncMock(return_value=[budget]),
            ),
            patch(
                "codemie.service.budget.inactive_cost_center_budget_stop_service.clear_budget_resolution_cache",
            ),
            patch(
                "codemie.service.budget.inactive_cost_center_budget_stop_service."
                "activity_event_repository.async_insert",
                AsyncMock(),
            ) as mock_insert,
            patch("codemie.service.email_service.email_service", AsyncMock()),
        ):
            service = InactiveCostCenterBudgetStopService()
            await service.run(session)

        assert budget.is_active is False
        mock_insert.assert_called_once()
        event_dto = mock_insert.call_args[0][0]
        assert event_dto.event_type == BudgetManagementEvent.PROJECT_BUDGET_STOPPED
        assert event_dto.entity_id == "budget-1"
        assert event_dto.entity_label == "Cost Center Budget"
        assert event_dto.project_name == "proj-x"
        assert event_dto.actor_email is None
        assert event_dto.actor_display_name is None
