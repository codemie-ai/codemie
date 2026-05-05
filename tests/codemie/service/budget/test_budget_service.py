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
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.budget_models import Budget
from codemie.service.budget.provider import PersonalBudgetEntry
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
        """Provider sync failures roll back the insert and surface a 502."""
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
                "codemie.service.budget.budget_service.get_active_provider",
            ) as mock_get_active_provider,
        ):
            mock_provider = MagicMock()
            mock_provider.ensure_global_budget = AsyncMock(side_effect=RuntimeError("sync failed"))
            mock_get_active_provider.return_value = mock_provider
            request = _make_create_request()
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await service.create_budget(mock_session, request, actor_id="admin")

        assert exc_info.value.code == 502
        mock_session.rollback.assert_awaited_once()

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
                "codemie.service.budget.budget_service.get_active_provider",
            ) as mock_get_active_provider,
            patch(
                "codemie.service.budget.budget_service.budget_repository.update",
                new=AsyncMock(return_value=updated_budget),
            ),
        ):
            mock_provider = MagicMock()
            mock_provider.ensure_global_budget = AsyncMock(return_value=litellm_result)
            mock_get_active_provider.return_value = mock_provider
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


class TestPredefinedBudgetStartupSync:
    @pytest.mark.asyncio
    async def test_skips_provider_update_when_provider_budget_matches_config(self):
        service = _make_service()
        mock_session = AsyncMock()
        mock_provider = MagicMock()
        mock_provider.provider_name = "litellm"
        mock_provider.list_global_budget_states = AsyncMock(
            return_value=[
                SimpleNamespace(
                    budget_id="default",
                    soft_budget=50.0,
                    max_budget=100.0,
                    budget_duration="30d",
                    budget_reset_at="2026-05-01T00:00:00Z",
                )
            ]
        )
        mock_provider.ensure_global_budget = AsyncMock()
        mock_provider.update_global_budget = AsyncMock()

        with (
            patch(
                "codemie.service.budget.budget_service.budget_config.predefined_budgets",
                [
                    SimpleNamespace(
                        budget_id="default",
                        name="Default Budget",
                        description="Default platform budget for new LiteLLM customers.",
                        soft_budget=50.0,
                        max_budget=100.0,
                        budget_duration="30d",
                        budget_category="platform",
                    )
                ],
            ),
            patch("codemie.service.budget.budget_service.get_active_provider", return_value=mock_provider),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_id",
                new=AsyncMock(return_value=_make_budget(budget_id="default")),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.update",
                new=AsyncMock(return_value=_make_budget(budget_id="default")),
            ),
        ):
            await service.ensure_predefined_budgets(mock_session)

        mock_provider.ensure_global_budget.assert_not_awaited()
        mock_provider.update_global_budget.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_preserves_provider_reset_when_only_limits_change(self):
        service = _make_service()
        mock_session = AsyncMock()
        mock_provider = MagicMock()
        mock_provider.provider_name = "litellm"
        mock_provider.list_global_budget_states = AsyncMock(
            return_value=[
                SimpleNamespace(
                    budget_id="default",
                    soft_budget=50.0,
                    max_budget=100.0,
                    budget_duration="30d",
                    budget_reset_at="2026-05-01T00:00:00Z",
                )
            ]
        )
        mock_provider.update_global_budget = AsyncMock(
            return_value=SimpleNamespace(
                provider="litellm",
                provider_budget_ref="default",
                budget_reset_at="2026-05-01T00:00:00Z",
                sync_status="ok",
            )
        )

        with (
            patch(
                "codemie.service.budget.budget_service.budget_config.predefined_budgets",
                [
                    SimpleNamespace(
                        budget_id="default",
                        name="Default Budget",
                        description="Default platform budget for new LiteLLM customers.",
                        soft_budget=60.0,
                        max_budget=110.0,
                        budget_duration="30d",
                        budget_category="platform",
                    )
                ],
            ),
            patch("codemie.service.budget.budget_service.get_active_provider", return_value=mock_provider),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_id",
                new=AsyncMock(return_value=_make_budget(budget_id="default")),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.update",
                new=AsyncMock(return_value=_make_budget(budget_id="default")),
            ),
        ):
            await service.ensure_predefined_budgets(mock_session)

        mock_provider.update_global_budget.assert_awaited_once_with(
            budget_id="default",
            soft_budget=60.0,
            max_budget=110.0,
            budget_duration="30d",
            budget_reset_at="2026-05-01T00:00:00Z",
        )

    @pytest.mark.asyncio
    async def test_recomputes_reset_when_duration_changes(self):
        service = _make_service()
        mock_session = AsyncMock()
        mock_provider = MagicMock()
        mock_provider.provider_name = "litellm"
        mock_provider.list_global_budget_states = AsyncMock(
            return_value=[
                SimpleNamespace(
                    budget_id="default",
                    soft_budget=50.0,
                    max_budget=100.0,
                    budget_duration="30d",
                    budget_reset_at="2026-05-01T00:00:00Z",
                )
            ]
        )
        mock_provider.update_global_budget = AsyncMock(
            return_value=SimpleNamespace(
                provider="litellm",
                provider_budget_ref="default",
                budget_reset_at="2026-05-08T00:00:00Z",
                sync_status="ok",
            )
        )
        budget_rows: list[dict] = []

        async def _capture_update(_session, _budget_id, fields):
            budget_rows.append(fields)
            return _make_budget(budget_id="default", budget_reset_at=fields.get("budget_reset_at"))

        with (
            patch(
                "codemie.service.budget.budget_service.budget_config.predefined_budgets",
                [
                    SimpleNamespace(
                        budget_id="default",
                        name="Default Budget",
                        description="Default platform budget for new LiteLLM customers.",
                        soft_budget=50.0,
                        max_budget=100.0,
                        budget_duration="7d",
                        budget_category="platform",
                    )
                ],
            ),
            patch("codemie.service.budget.budget_service.get_active_provider", return_value=mock_provider),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_id",
                new=AsyncMock(return_value=_make_budget(budget_id="default")),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.update",
                new=AsyncMock(side_effect=_capture_update),
            ),
        ):
            await service.ensure_predefined_budgets(mock_session)

        mock_provider.update_global_budget.assert_awaited_once_with(
            budget_id="default",
            soft_budget=50.0,
            max_budget=100.0,
            budget_duration="7d",
            budget_reset_at=None,
        )
        assert any(fields.get("budget_reset_at") == "2026-05-08T00:00:00Z" for fields in budget_rows)


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
            user_identifier="user@example.com",
            budget_id="cli-budget",
            budget_category=BudgetCategory.CLI,
            soft_budget=10,
            max_budget=100,
            budget_duration="30d",
            budget_reset_at=None,
        )

        with (
            patch(
                "codemie.service.budget.budget_service.get_active_provider",
            ) as mock_get_active_provider,
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
            mock_provider = MagicMock()
            mock_provider.list_personal_budget_assignments = AsyncMock(
                return_value=[PersonalBudgetEntry(**entry.__dict__)]
            )
            mock_get_active_provider.return_value = mock_provider
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
            user_identifier="user@example.com",
            budget_id="platform-budget",
            budget_category=BudgetCategory.PLATFORM,
            soft_budget=10,
            max_budget=100,
            budget_duration="30d",
            budget_reset_at=None,
        )

        with (
            patch(
                "codemie.service.budget.budget_service.get_active_provider",
            ) as mock_get_active_provider,
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
            mock_provider = MagicMock()
            mock_provider.list_personal_budget_assignments = AsyncMock(
                return_value=[PersonalBudgetEntry(**entry.__dict__)]
            )
            mock_get_active_provider.return_value = mock_provider
            result = await service.backfill_user_budget_assignments_from_litellm(mock_session, actor_id="admin")

        assert result.imported == 0
        assert result.skipped_existing == 1
        mock_upsert_assignment.assert_not_awaited()


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


def test_clear_budget_assignment_cache_empties_cache():
    """clear_budget_assignment_cache() removes all entries."""
    from codemie.service.budget.budget_service import _budget_assignment_cache, clear_budget_assignment_cache

    _budget_assignment_cache[("u1", "platform")] = "budget-1"
    assert len(_budget_assignment_cache) >= 1
    clear_budget_assignment_cache()
    assert len(_budget_assignment_cache) == 0


@pytest.mark.asyncio
async def test_get_user_category_budget_id_for_request_caches_result():
    """get_user_category_budget_id_for_request() populates cache and returns value."""
    from unittest.mock import AsyncMock, patch

    from codemie.enterprise.litellm.budget_categories import BudgetCategory
    from codemie.service.budget.budget_service import (
        BudgetService,
        _budget_assignment_cache,
        clear_budget_assignment_cache,
    )

    clear_budget_assignment_cache()
    svc = BudgetService()

    mock_session = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "codemie.clients.postgres.get_async_session",
            return_value=mock_cm,
        ),
        patch(
            "codemie.service.budget.budget_service.budget_repository.get_user_category_budget_id",
            new=AsyncMock(return_value="budget-42"),
        ),
    ):
        result = await svc.get_user_category_budget_id_for_request("u1", BudgetCategory.PLATFORM)

    assert result == "budget-42"
    assert _budget_assignment_cache[("u1", BudgetCategory.PLATFORM.value)] == "budget-42"


@pytest.mark.asyncio
async def test_get_user_category_budget_id_for_request_uses_cache():
    """get_user_category_budget_id_for_request() skips DB on warm cache hit."""
    from unittest.mock import patch

    from codemie.enterprise.litellm.budget_categories import BudgetCategory
    from codemie.service.budget.budget_service import (
        BudgetService,
        _budget_assignment_cache,
        clear_budget_assignment_cache,
    )

    clear_budget_assignment_cache()
    _budget_assignment_cache[("u1", BudgetCategory.PLATFORM.value)] = "cached-budget"
    svc = BudgetService()

    with patch("codemie.clients.postgres.get_async_session") as mock_session_ctx:
        result = await svc.get_user_category_budget_id_for_request("u1", BudgetCategory.PLATFORM)
        mock_session_ctx.assert_not_called()

    assert result == "cached-budget"


def test_get_all_category_budget_ids_for_request_sync_uses_cache():
    """Sync batch lookup skips DB when all category assignments are already cached."""
    from unittest.mock import patch

    from codemie.service.budget.budget_service import (
        BudgetService,
        _budget_assignment_cache,
        clear_budget_assignment_cache,
    )

    clear_budget_assignment_cache()
    _budget_assignment_cache[("u1", BudgetCategory.PLATFORM.value)] = "platform-budget"
    _budget_assignment_cache[("u1", BudgetCategory.CLI.value)] = "cli-budget"
    _budget_assignment_cache[("u1", BudgetCategory.PREMIUM_MODELS.value)] = "premium-budget"
    svc = BudgetService()

    with patch("sqlmodel.Session") as mock_session_cls:
        result = svc.get_all_category_budget_ids_for_request_sync("u1")

    assert result == {
        BudgetCategory.PLATFORM.value: "platform-budget",
        BudgetCategory.CLI.value: "cli-budget",
        BudgetCategory.PREMIUM_MODELS.value: "premium-budget",
    }
    mock_session_cls.assert_not_called()


@pytest.mark.asyncio
async def test_load_bulk_budget_users_raises_when_any_user_missing():
    service = _make_service()
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [SimpleNamespace(id="user-1", email="user1@example.com")]
    session.execute = AsyncMock(return_value=result_mock)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await service._load_bulk_budget_users(
            session=session,
            user_ids=["user-1", "missing-user"],
            select=lambda model: MagicMock(where=lambda *args, **kwargs: MagicMock()),
            user_model=SimpleNamespace(id=MagicMock(in_=MagicMock(return_value=True))),
        )

    assert "Users not found" in exc_info.value.message


@pytest.mark.asyncio
async def test_propagate_bulk_budget_assignments_calls_assign_and_clear():
    service = _make_service()
    db_users = {"user-1": SimpleNamespace(email="user1@example.com")}
    assignments = {
        BudgetCategory.CLI: "cli-budget",
        BudgetCategory.PLATFORM: None,
    }

    with patch("codemie.service.budget.budget_service.get_active_provider") as mock_get_provider:
        mock_provider = MagicMock()
        mock_provider.assign_user_budget = AsyncMock()
        mock_provider.clear_user_budget = AsyncMock()
        mock_get_provider.return_value = mock_provider

        await service._propagate_bulk_budget_assignments(db_users, assignments)

    mock_provider.assign_user_budget.assert_awaited_once_with(
        user_email="user1@example.com",
        budget_category=BudgetCategory.CLI,
        budget_id="cli-budget",
    )
    mock_provider.clear_user_budget.assert_awaited_once_with(
        user_email="user1@example.com",
        budget_category=BudgetCategory.PLATFORM,
    )


@pytest.mark.asyncio
async def test_propagate_bulk_budget_assignments_continues_when_clear_fails():
    service = _make_service()
    db_users = {"user-1": SimpleNamespace(email="user1@example.com")}
    assignments = {BudgetCategory.PLATFORM: None}

    with (
        patch("codemie.service.budget.budget_service.get_active_provider") as mock_get_provider,
        patch("codemie.service.budget.budget_service.logger") as mock_logger,
    ):
        mock_provider = MagicMock()
        mock_provider.clear_user_budget = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        mock_get_provider.return_value = mock_provider

        await service._propagate_bulk_budget_assignments(db_users, assignments)

    mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_backfill_category_uses_prefetched_provider_metadata_when_noncanonical():
    service = _make_service()
    prefetched_state = SimpleNamespace(metadata={"budget_category": "cli"})
    provider = SimpleNamespace(get_project_budget_state_by_ref=AsyncMock(return_value=prefetched_state))

    category, fetched, state = await service._resolve_backfill_category(provider, "legacy-alias")

    assert category == "cli"
    assert fetched is True
    assert state is prefetched_state
