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

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException, ValidationException
from codemie.service.budget import budget_service as budget_service_module
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
# TestAResetCodeMieSetIsRecorded (EPMCDME-15204)
# ---------------------------------------------------------------------------

#: A 30d budget created 2099-10-09 15:05, whose first reset CodeMie set to the next midnight after 30 days:
#: it prolongs the provider's October period.
_CREATED = datetime(2099, 10, 9, 15, 5, 4, tzinfo=timezone.utc)
_PINNED = "2099-11-09T00:00:00Z"
_OCTOBER = datetime(2099, 10, 1, tzinfo=timezone.utc)


def _this_and_next_month() -> tuple[datetime, str]:
    """The provider's current `30d` period start and its reset: a change reads the running period at now."""
    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    reset = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, reset.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ok_state(budget_reset_at: str) -> SimpleNamespace:
    return SimpleNamespace(
        provider="litellm", provider_budget_ref="default", budget_reset_at=budget_reset_at, sync_status="ok"
    )


def _recorded(written: list[dict]) -> list[tuple]:
    """Every (pinned_reset_at, pinned_period_start) pair written, in order."""
    return [(f["pinned_reset_at"], f["pinned_period_start"]) for f in written if "pinned_reset_at" in f]


def _predefined_config(budget_duration: str, max_budget: float = 100.0) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            budget_id="default",
            name="Default Budget",
            description="Default platform budget for new LiteLLM customers.",
            soft_budget=50.0,
            max_budget=max_budget,
            budget_duration=budget_duration,
            budget_category="platform",
        )
    ]


def _provider_budget(budget_reset_at: str = "2099-11-01T00:00:00Z") -> SimpleNamespace:
    return SimpleNamespace(
        budget_id="default", soft_budget=50.0, max_budget=100.0, budget_duration="30d", budget_reset_at=budget_reset_at
    )


class TestAResetCodeMieSetIsRecorded:
    """Setting a reset resets no counter, so where the running period began is recorded with it."""

    @staticmethod
    def _capture(written: list[dict], row: Budget | None = None):
        async def update(_session, budget_id, fields):
            written.append(fields)
            return row or _make_budget(budget_id=budget_id)

        return update

    @pytest.mark.asyncio
    async def test_a_new_budget_records_the_provider_s_period_its_creation_falls_in(self):
        service = _make_service()
        provider = MagicMock()
        provider.ensure_global_budget = AsyncMock(return_value=_ok_state(_PINNED))
        written: list[dict] = []

        with (
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_id", new=AsyncMock(return_value=None)
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_name", new=AsyncMock(return_value=None)
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.insert",
                new=AsyncMock(return_value=_make_budget(budget_id="new-budget", created_at=_CREATED)),
            ),
            patch("codemie.service.budget.budget_service.budget_repository.update", new=self._capture(written)),
            patch("codemie.service.budget.budget_service.get_active_provider", return_value=provider),
            patch("codemie.service.budget.budget_service.activity_event_repository.async_insert", new=AsyncMock()),
        ):
            await service.create_budget(AsyncMock(), _make_create_request(), actor_id="admin")

        assert written[-1]["budget_reset_at"] == _PINNED
        assert _recorded(written) == [(datetime(2099, 11, 9, tzinfo=timezone.utc), _OCTOBER)]

    @pytest.mark.asyncio
    async def test_a_change_records_the_period_running_on_the_old_schedule(self):
        """Read before the row takes the new duration: on 7d the period would seem to start a week back."""
        service = _make_service()
        period_start, reset = _this_and_next_month()
        existing = _make_budget(budget_reset_at=reset, budget_duration="30d", created_at=_CREATED)
        provider = MagicMock()
        provider.provider_name = "litellm"
        provider.update_global_budget = AsyncMock(return_value=_ok_state("2099-10-28T00:00:00Z"))
        written: list[dict] = []

        async def update(_session, _budget_id, fields):
            written.append(fields)
            for key, value in fields.items():
                setattr(existing, key, value)
            return existing

        with (
            patch("codemie.service.budget.budget_service.budget_config.predefined_budgets", []),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_id",
                new=AsyncMock(return_value=existing),
            ),
            patch.object(
                service,
                "_validate_budget_update_request",
                new=AsyncMock(return_value=(50.0, 100.0, "7d", "platform", {"budget_duration"})),
            ),
            patch("codemie.service.budget.budget_service.budget_repository.update", new=AsyncMock(side_effect=update)),
            patch("codemie.service.budget.budget_service.get_active_provider", return_value=provider),
            patch("codemie.service.budget.budget_service.activity_event_repository.async_insert", new=AsyncMock()),
        ):
            from codemie.rest_api.routers.budget_router import BudgetUpdateRequest

            await service.update_budget(AsyncMock(), "test-budget", BudgetUpdateRequest(budget_duration="7d"), "admin")

        assert _recorded(written) == [(datetime(2099, 10, 28, tzinfo=timezone.utc), period_start)]

    @pytest.mark.asyncio
    async def test_a_predefined_budget_created_in_the_provider_records_its_first_period(self):
        service = _make_service()
        provider = MagicMock()
        provider.provider_name = "litellm"
        provider.list_global_budget_states = AsyncMock(return_value=[])
        provider.ensure_global_budget = AsyncMock(return_value=_ok_state(_PINNED))
        written: list[dict] = []

        with (
            patch("codemie.service.budget.budget_service.budget_config.predefined_budgets", _predefined_config("30d")),
            patch("codemie.service.budget.budget_service.get_active_provider", return_value=provider),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_id", new=AsyncMock(return_value=None)
            ),
            patch(
                "codemie.service.budget.budget_service.budget_repository.insert",
                new=AsyncMock(return_value=_make_budget(budget_id="default", created_at=_CREATED)),
            ),
            patch("codemie.service.budget.budget_service.budget_repository.update", new=self._capture(written)),
        ):
            await service.ensure_predefined_budgets(AsyncMock())

        assert _recorded(written) == [(datetime(2099, 11, 9, tzinfo=timezone.utc), _OCTOBER)]

    @pytest.mark.asyncio
    async def test_a_predefined_duration_change_records_the_period_running_on_the_old_schedule(self):
        service = _make_service()
        period_start, reset = _this_and_next_month()
        existing = _make_budget(budget_id="default", budget_reset_at=reset, created_at=_CREATED)
        provider = MagicMock()
        provider.provider_name = "litellm"
        provider.list_global_budget_states = AsyncMock(return_value=[_provider_budget(budget_reset_at=reset)])
        provider.update_global_budget = AsyncMock(return_value=_ok_state("2099-10-28T00:00:00Z"))
        written: list[dict] = []

        async def update(_session, _budget_id, fields):
            written.append(fields)
            for key, value in fields.items():
                setattr(existing, key, value)
            return existing

        with (
            patch("codemie.service.budget.budget_service.budget_config.predefined_budgets", _predefined_config("7d")),
            patch("codemie.service.budget.budget_service.get_active_provider", return_value=provider),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_id",
                new=AsyncMock(return_value=existing),
            ),
            patch("codemie.service.budget.budget_service.budget_repository.update", new=AsyncMock(side_effect=update)),
        ):
            await service.ensure_predefined_budgets(AsyncMock())

        assert _recorded(written) == [(datetime(2099, 10, 28, tzinfo=timezone.utc), period_start)]

    @pytest.mark.asyncio
    async def test_a_predefined_budget_without_a_provider_reset_records_the_one_the_client_set(self):
        """Sent back empty, the provider's reset is computed by the client - CodeMie set it."""
        service = _make_service()
        existing = _make_budget(budget_id="default", created_at=_CREATED)
        provider = MagicMock()
        provider.provider_name = "litellm"
        provider.list_global_budget_states = AsyncMock(return_value=[_provider_budget(budget_reset_at=None)])
        provider.update_global_budget = AsyncMock(return_value=_ok_state(_PINNED))
        written: list[dict] = []

        with (
            patch(
                "codemie.service.budget.budget_service.budget_config.predefined_budgets",
                _predefined_config("30d", max_budget=110.0),
            ),
            patch("codemie.service.budget.budget_service.get_active_provider", return_value=provider),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_id",
                new=AsyncMock(return_value=existing),
            ),
            patch("codemie.service.budget.budget_service.budget_repository.update", new=self._capture(written)),
        ):
            await service.ensure_predefined_budgets(AsyncMock())

        assert _recorded(written) == [(datetime(2099, 11, 9, tzinfo=timezone.utc), _OCTOBER)]

    @pytest.mark.asyncio
    async def test_a_predefined_reset_the_provider_set_is_not_recorded(self):
        """Only the limits change, so the provider's own reset comes back and the record is left alone."""
        service = _make_service()
        provider = MagicMock()
        provider.provider_name = "litellm"
        provider.list_global_budget_states = AsyncMock(return_value=[_provider_budget()])
        provider.update_global_budget = AsyncMock(return_value=_ok_state("2099-11-01T00:00:00Z"))
        written: list[dict] = []

        with (
            patch(
                "codemie.service.budget.budget_service.budget_config.predefined_budgets",
                _predefined_config("30d", max_budget=110.0),
            ),
            patch("codemie.service.budget.budget_service.get_active_provider", return_value=provider),
            patch(
                "codemie.service.budget.budget_service.budget_repository.get_by_id",
                new=AsyncMock(return_value=_make_budget(budget_id="default", budget_reset_at="2099-11-01T00:00:00Z")),
            ),
            patch("codemie.service.budget.budget_service.budget_repository.update", new=self._capture(written)),
        ):
            await service.ensure_predefined_budgets(AsyncMock())

        assert any(fields.get("budget_reset_at") == "2099-11-01T00:00:00Z" for fields in written)
        assert _recorded(written) == []


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
    db_users = {"user-1": SimpleNamespace(username="john_doe")}
    assignments = {
        BudgetCategory.CLI: "cli-budget",
        BudgetCategory.PLATFORM: None,
    }

    with (
        patch("codemie.service.budget.budget_service.get_active_provider") as mock_get_provider,
        patch("codemie.service.budget.budget_service.budget_config") as mock_budget_config,
    ):
        mock_budget_config.predefined_budgets = []
        mock_provider = MagicMock()
        mock_provider.assign_user_budget = AsyncMock()
        mock_provider.clear_user_budget = AsyncMock()
        mock_get_provider.return_value = mock_provider

        await service._propagate_bulk_budget_assignments(db_users, assignments)

    mock_provider.assign_user_budget.assert_awaited_once_with(
        username="john_doe",
        budget_category=BudgetCategory.CLI,
        budget_id="cli-budget",
    )
    mock_provider.clear_user_budget.assert_awaited_once_with(
        username="john_doe",
        budget_category=BudgetCategory.PLATFORM,
    )


@pytest.mark.asyncio
async def test_propagate_bulk_budget_assignments_continues_when_clear_fails():
    service = _make_service()
    db_users = {"user-1": SimpleNamespace(username="john_doe")}
    assignments = {BudgetCategory.PLATFORM: None}

    with (
        patch("codemie.service.budget.budget_service.get_active_provider") as mock_get_provider,
        patch("codemie.service.budget.budget_service.budget_config") as mock_budget_config,
        patch("codemie.service.budget.budget_service.logger") as mock_logger,
    ):
        mock_budget_config.predefined_budgets = []
        mock_provider = MagicMock()
        mock_provider.clear_user_budget = AsyncMock(side_effect=RuntimeError("provider unavailable"))
        mock_get_provider.return_value = mock_provider

        await service._propagate_bulk_budget_assignments(db_users, assignments)

    mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_propagate_bulk_budget_assignments_reassigns_default_when_none_and_default_exists():
    """When a budget_id is None and a predefined default exists, assign_user_budget is called."""
    from codemie.configs.budget_config import PredefinedBudgetConfig

    service = _make_service()
    db_users = {"user-1": SimpleNamespace(username="john_doe")}
    assignments = {BudgetCategory.PLATFORM: None}
    predefined = PredefinedBudgetConfig(
        budget_id="platform-default",
        name="Platform Default",
        budget_category=BudgetCategory.PLATFORM.value,
        soft_budget=10.0,
        max_budget=100.0,
        budget_duration="30d",
    )

    with (
        patch("codemie.service.budget.budget_service.get_active_provider") as mock_get_provider,
        patch("codemie.service.budget.budget_service.budget_config") as mock_budget_config,
    ):
        mock_budget_config.predefined_budgets = [predefined]
        mock_provider = MagicMock()
        mock_provider.assign_user_budget = AsyncMock()
        mock_provider.clear_user_budget = AsyncMock()
        mock_get_provider.return_value = mock_provider

        await service._propagate_bulk_budget_assignments(db_users, assignments)

    mock_provider.assign_user_budget.assert_awaited_once_with(
        username="john_doe",
        budget_category=BudgetCategory.PLATFORM,
        budget_id="platform-default",
    )
    mock_provider.clear_user_budget.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_backfill_category_uses_prefetched_provider_metadata_when_noncanonical():
    service = _make_service()
    prefetched_state = SimpleNamespace(metadata={"budget_category": "cli"})
    provider = SimpleNamespace(get_project_budget_state_by_ref=AsyncMock(return_value=prefetched_state))

    category, fetched, state = await service._resolve_backfill_category(provider, "legacy-alias")

    assert category == "cli"
    assert fetched is True
    assert state is prefetched_state


class TestBudgetModelNotificationFields:
    def test_budget_carries_notification_fields(self):
        now = datetime(2026, 8, 14, tzinfo=timezone.utc)
        b = Budget(
            budget_id="b1",
            name="B1",
            soft_budget=100.0,
            max_budget=200.0,
            budget_duration="30d",
            budget_category=BudgetCategory.PLATFORM.value,
            created_by="alice",
            notification_owner_email="owner@example.com",
            soft_limit_notified_at=now,
        )
        assert b.notification_owner_email == "owner@example.com"
        assert b.soft_limit_notified_at == now

    def test_budget_notification_fields_default_none(self):
        b = Budget(
            budget_id="b2",
            name="B2",
            soft_budget=100.0,
            max_budget=200.0,
            budget_duration="30d",
            budget_category=BudgetCategory.PLATFORM.value,
            created_by="alice",
        )
        assert b.notification_owner_email is None
        assert b.soft_limit_notified_at is None


# ---------------------------------------------------------------------------
# TestNotificationOwnerPersistence + TestSoftLimitNotifiedAtReset (EPMCDME-13959)
# ---------------------------------------------------------------------------


class TestBuildUpdateFieldsNotificationOwner:
    """Focused unit tests for the _build_update_fields helper + dedup reset."""

    def test_build_update_fields_includes_notification_owner_email_when_provided(self):
        from codemie.rest_api.models.budget import BudgetUpdateRequest

        data = BudgetUpdateRequest(notification_owner_email="team@example.com")
        fields = BudgetService._build_update_fields(data, new_category=BudgetCategory.PLATFORM.value)
        assert fields.get("notification_owner_email") == "team@example.com"

    def test_build_update_fields_includes_explicit_null_notification_owner_email(self):
        from codemie.rest_api.models.budget import BudgetUpdateRequest

        data = BudgetUpdateRequest(notification_owner_email=None)
        # `notification_owner_email` was explicitly set (even to None) → in model_fields_set
        assert "notification_owner_email" in data.model_fields_set
        fields = BudgetService._build_update_fields(data, new_category=BudgetCategory.PLATFORM.value)
        assert "notification_owner_email" in fields
        assert fields["notification_owner_email"] is None

    def test_build_update_fields_omits_notification_owner_email_when_not_provided(self):
        from codemie.rest_api.models.budget import BudgetUpdateRequest

        data = BudgetUpdateRequest(name="new-name")
        fields = BudgetService._build_update_fields(data, new_category=BudgetCategory.PLATFORM.value)
        assert "notification_owner_email" not in fields


class TestCreateBudgetPersistsNotificationOwner:
    @pytest.mark.asyncio
    async def test_create_budget_passes_notification_owner_email_to_model(self):
        """The Budget row constructed in create_budget must carry notification_owner_email."""
        from codemie.rest_api.models.budget import BudgetCreateRequest

        service = _make_service()
        request = BudgetCreateRequest(
            budget_id="notify-b1",
            name="Notify B1",
            soft_budget=10.0,
            max_budget=100.0,
            budget_duration="30d",
            budget_category=BudgetCategory.PLATFORM,
            notification_owner_email="owner@example.com",
        )
        captured: dict[str, Budget] = {}

        async def _fake_insert(session, budget):
            captured["budget"] = budget
            return budget

        provider_state = SimpleNamespace(
            provider="litellm",
            provider_budget_ref="notify-b1",
            budget_reset_at=None,
            sync_status="ok",
        )
        fake_provider = MagicMock()
        fake_provider.provider_name = "litellm"
        fake_provider.ensure_global_budget = AsyncMock(return_value=provider_state)

        with (
            patch("codemie.service.budget.budget_service.budget_repository") as repo,
            patch(
                "codemie.service.budget.budget_service.get_active_provider",
                return_value=fake_provider,
            ),
            patch(
                "codemie.service.budget.budget_service.activity_event_repository.async_insert",
                new=AsyncMock(),
            ),
        ):
            repo.get_by_id = AsyncMock(return_value=None)
            repo.get_by_name = AsyncMock(return_value=None)
            repo.insert = AsyncMock(side_effect=_fake_insert)
            repo.update = AsyncMock(side_effect=lambda s, bid, fields: captured["budget"])

            await service.create_budget(session=AsyncMock(), data=request, actor_id="admin-1")

        assert captured["budget"].notification_owner_email == "owner@example.com"


class TestUpdateBudgetResetsSoftLimitNotifiedAt:
    """update_budget must clear soft_limit_notified_at when soft_budget or owner email change."""

    @pytest.mark.asyncio
    async def test_soft_budget_change_clears_notified_at(self):
        from codemie.rest_api.models.budget import BudgetUpdateRequest

        service = _make_service()
        existing = _make_budget(
            soft_budget=50.0,
            soft_limit_notified_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        captured: dict = {"calls": []}

        async def _fake_repo_update(session, budget_id, fields):
            captured["calls"].append(dict(fields))
            for k, v in fields.items():
                setattr(existing, k, v)
            return existing

        provider_state = SimpleNamespace(
            provider="litellm",
            provider_budget_ref=existing.budget_id,
            budget_reset_at=None,
            sync_status="ok",
        )
        fake_provider = MagicMock()
        fake_provider.provider_name = "litellm"
        fake_provider.ensure_global_budget = AsyncMock(return_value=provider_state)
        fake_provider.update_global_budget = AsyncMock(return_value=provider_state)

        with (
            patch("codemie.service.budget.budget_service.budget_repository") as repo,
            patch(
                "codemie.service.budget.budget_service.get_active_provider",
                return_value=fake_provider,
            ),
            patch(
                "codemie.service.budget.budget_service.activity_event_repository.async_insert",
                new=AsyncMock(),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_config.predefined_budgets",
                new=[],
            ),
        ):
            repo.get_by_id = AsyncMock(return_value=existing)
            repo.get_by_name = AsyncMock(return_value=None)
            repo.update = AsyncMock(side_effect=_fake_repo_update)
            repo.count_assignments = AsyncMock(return_value=0)

            await service.update_budget(
                session=AsyncMock(),
                budget_id=existing.budget_id,
                data=BudgetUpdateRequest(soft_budget=75.0),
                actor_id="admin-1",
            )

        # First repo.update call is the main update payload; second is provider_metadata sync.
        primary = captured["calls"][0]
        assert "soft_limit_notified_at" in primary
        assert primary["soft_limit_notified_at"] is None

    @pytest.mark.asyncio
    async def test_notification_owner_email_change_clears_notified_at(self):
        from codemie.rest_api.models.budget import BudgetUpdateRequest

        service = _make_service()
        existing = _make_budget(
            notification_owner_email="old@example.com",
            soft_limit_notified_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        captured: dict = {"calls": []}

        async def _fake_repo_update(session, budget_id, fields):
            captured["calls"].append(dict(fields))
            for k, v in fields.items():
                setattr(existing, k, v)
            return existing

        with (
            patch("codemie.service.budget.budget_service.budget_repository") as repo,
            patch(
                "codemie.service.budget.budget_service.activity_event_repository.async_insert",
                new=AsyncMock(),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_config.predefined_budgets",
                new=[],
            ),
        ):
            repo.get_by_id = AsyncMock(return_value=existing)
            repo.get_by_name = AsyncMock(return_value=None)
            repo.update = AsyncMock(side_effect=_fake_repo_update)
            repo.count_assignments = AsyncMock(return_value=0)

            await service.update_budget(
                session=AsyncMock(),
                budget_id=existing.budget_id,
                data=BudgetUpdateRequest(notification_owner_email="new@example.com"),
                actor_id="admin-1",
            )

        primary = captured["calls"][0]
        assert primary.get("notification_owner_email") == "new@example.com"
        assert "soft_limit_notified_at" in primary
        assert primary["soft_limit_notified_at"] is None

    @pytest.mark.asyncio
    async def test_unrelated_change_preserves_notified_at(self):
        from codemie.rest_api.models.budget import BudgetUpdateRequest

        service = _make_service()
        existing = _make_budget(
            soft_limit_notified_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        captured: dict = {"calls": []}

        async def _fake_repo_update(session, budget_id, fields):
            captured["calls"].append(dict(fields))
            for k, v in fields.items():
                setattr(existing, k, v)
            return existing

        with (
            patch("codemie.service.budget.budget_service.budget_repository") as repo,
            patch(
                "codemie.service.budget.budget_service.activity_event_repository.async_insert",
                new=AsyncMock(),
            ),
            patch(
                "codemie.service.budget.budget_service.budget_config.predefined_budgets",
                new=[],
            ),
        ):
            repo.get_by_id = AsyncMock(return_value=existing)
            repo.get_by_name = AsyncMock(return_value=None)
            repo.update = AsyncMock(side_effect=_fake_repo_update)
            repo.count_assignments = AsyncMock(return_value=0)

            await service.update_budget(
                session=AsyncMock(),
                budget_id=existing.budget_id,
                data=BudgetUpdateRequest(description="A new description"),
                actor_id="admin-1",
            )

        primary = captured["calls"][0]
        assert "soft_limit_notified_at" not in primary


# ---------------------------------------------------------------------------
# TestResetUserBudgetSpending
# ---------------------------------------------------------------------------


def _session_yielding_user(username: str | None):
    """Session double whose only SELECT yields a user row, or nothing.

    The service reads just ``db_user.username``, so a namespace is a faithful
    stand-in for the ORM row and keeps the test off the database. Pass None to
    model an unknown user.
    """
    db_user = SimpleNamespace(username=username) if username is not None else None
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=db_user))))
    )
    return session


def _predefined_budget(budget_id: str, category: BudgetCategory) -> SimpleNamespace:
    """Config entry as ``_default_budget_id_for_category`` reads it: category as a raw string."""
    return SimpleNamespace(budget_id=budget_id, budget_category=category.value)


def _provider_double(failing_categories: tuple[BudgetCategory, ...] = ()) -> MagicMock:
    async def _reset(*, username, budget_category, budget_id):
        if budget_category in failing_categories:
            raise RuntimeError("provider is down")

    provider = MagicMock()
    provider.reset_user_budget_spending = AsyncMock(side_effect=_reset)
    return provider


@contextmanager
def _reset_environment(provider, assigned: dict, predefined: tuple = ()):
    """Patch the module-level collaborators the reset path reaches through.

    ``assigned`` maps a category to the budget id stored for the user; a missing
    key means the user has no assignment for that category, which is what pushes
    the service onto the predefined-default fallback.
    """
    with (
        patch("codemie.service.budget.budget_service.budget_repository") as repo,
        patch("codemie.service.budget.budget_service.get_active_provider", return_value=provider),
        patch("codemie.service.budget.budget_service.budget_config") as mock_budget_config,
        patch("codemie.service.budget.budget_service.logger") as mock_logger,
    ):
        repo.get_user_category_budget_id = AsyncMock(side_effect=lambda s, u, category: assigned.get(category))
        repo.mark_user_category_spend_reset = AsyncMock()
        mock_budget_config.predefined_budgets = list(predefined)
        yield mock_logger


def _awaited_categories(provider) -> list:
    return [call.kwargs["budget_category"] for call in provider.reset_user_budget_spending.await_args_list]


class TestResetUserBudgetSpending:
    @pytest.mark.asyncio
    async def test_personal_reset_records_reset_time_for_each_category_that_succeeded(self):
        provider = _provider_double(failing_categories=(BudgetCategory.CLI,))
        assigned = {category: f"assigned-{category.value}" for category in BudgetCategory}
        session = _session_yielding_user("alice")

        with _reset_environment(provider, assigned=assigned):
            await _make_service().reset_user_budget_spending(
                session=session,
                user_id="u-1",
                actor_id="admin-1",
                categories=[BudgetCategory.PLATFORM, BudgetCategory.CLI],
            )
            mark = budget_service_module.budget_repository.mark_user_category_spend_reset

        mark.assert_awaited_once()
        args = mark.await_args.args
        assert args[:3] == (session, "u-1", BudgetCategory.PLATFORM)
        assert args[3].tzinfo is not None
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_reset_records_nothing_when_every_provider_reset_fails(self):
        provider = _provider_double(failing_categories=tuple(BudgetCategory))
        assigned = {category: f"assigned-{category.value}" for category in BudgetCategory}
        session = _session_yielding_user("alice")

        with _reset_environment(provider, assigned=assigned):
            await _make_service().reset_user_budget_spending(session=session, user_id="u-1", actor_id="admin-1")
            mark = budget_service_module.budget_repository.mark_user_category_spend_reset

        mark.assert_not_awaited()
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_every_category_is_reset_in_the_provider_before_any_reset_time_is_recorded(self):
        provider = _provider_double()
        assigned = {category: f"assigned-{category.value}" for category in BudgetCategory}
        events: list[tuple[str, BudgetCategory]] = []
        provider_reset = provider.reset_user_budget_spending.side_effect

        async def _record_provider_reset(*, username, budget_category, budget_id):
            events.append(("reset", budget_category))
            await provider_reset(username=username, budget_category=budget_category, budget_id=budget_id)

        async def _record_mark(session, user_id, category, reset_at):
            events.append(("mark", category))

        provider.reset_user_budget_spending.side_effect = _record_provider_reset

        with _reset_environment(provider, assigned=assigned):
            budget_service_module.budget_repository.mark_user_category_spend_reset.side_effect = _record_mark
            await _make_service().reset_user_budget_spending(
                session=_session_yielding_user("alice"), user_id="u-1", actor_id="admin-1"
            )

        categories = list(BudgetCategory)
        assert events == [("reset", c) for c in categories] + [("mark", c) for c in categories]

    @pytest.mark.asyncio
    async def test_a_failure_to_record_reset_times_is_logged_and_raised_after_every_provider_reset(self):
        provider = _provider_double()
        assigned = {category: f"assigned-{category.value}" for category in BudgetCategory}
        session = _session_yielding_user("alice")

        with _reset_environment(provider, assigned=assigned) as mock_logger:
            budget_service_module.budget_repository.mark_user_category_spend_reset.side_effect = RuntimeError(
                "db is down"
            )
            with pytest.raises(RuntimeError, match="db is down"):
                await _make_service().reset_user_budget_spending(session=session, user_id="u-1", actor_id="admin-1")

        assert _awaited_categories(provider) == list(BudgetCategory)
        mock_logger.error.assert_called_once()
        assert "u-1" in mock_logger.error.call_args.args[0]
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_user_raises_404_before_touching_provider(self):
        provider = _provider_double()

        with _reset_environment(provider, assigned={}):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await _make_service().reset_user_budget_spending(
                    session=_session_yielding_user(None),
                    user_id="ghost-user",
                    actor_id="admin-1",
                )

        assert exc_info.value.code == 404
        assert "ghost-user" in exc_info.value.message
        provider.reset_user_budget_spending.assert_not_awaited()

    @pytest.mark.parametrize("categories", [None, []])
    @pytest.mark.asyncio
    async def test_falsy_categories_reset_every_category_in_enum_order(self, categories):
        provider = _provider_double()
        assigned = {category: f"assigned-{category.value}" for category in BudgetCategory}

        with _reset_environment(provider, assigned=assigned):
            await _make_service().reset_user_budget_spending(
                session=_session_yielding_user("alice"),
                user_id="u-1",
                actor_id="admin-1",
                categories=categories,
            )

        assert _awaited_categories(provider) == list(BudgetCategory)

    @pytest.mark.asyncio
    async def test_explicit_categories_reset_only_those_in_the_given_order(self):
        provider = _provider_double()
        requested = [BudgetCategory.PREMIUM_MODELS, BudgetCategory.PLATFORM]
        assigned = {category: f"assigned-{category.value}" for category in BudgetCategory}

        with _reset_environment(provider, assigned=assigned):
            await _make_service().reset_user_budget_spending(
                session=_session_yielding_user("alice"),
                user_id="u-1",
                actor_id="admin-1",
                categories=requested,
            )

        assert _awaited_categories(provider) == requested

    @pytest.mark.asyncio
    async def test_assigned_budget_id_wins_over_the_predefined_default(self):
        provider = _provider_double()
        predefined = (_predefined_budget("default-platform", BudgetCategory.PLATFORM),)

        with _reset_environment(
            provider,
            assigned={BudgetCategory.PLATFORM: "assigned-platform"},
            predefined=predefined,
        ):
            await _make_service().reset_user_budget_spending(
                session=_session_yielding_user("alice"),
                user_id="u-1",
                actor_id="admin-1",
                categories=[BudgetCategory.PLATFORM],
            )

        provider.reset_user_budget_spending.assert_awaited_once_with(
            username="alice", budget_category=BudgetCategory.PLATFORM, budget_id="assigned-platform"
        )

    @pytest.mark.asyncio
    async def test_unassigned_category_falls_back_to_the_predefined_default(self):
        provider = _provider_double()
        predefined = (_predefined_budget("default-cli", BudgetCategory.CLI),)

        with _reset_environment(provider, assigned={}, predefined=predefined):
            await _make_service().reset_user_budget_spending(
                session=_session_yielding_user("alice"),
                user_id="u-1",
                actor_id="admin-1",
                categories=[BudgetCategory.CLI],
            )

        provider.reset_user_budget_spending.assert_awaited_once_with(
            username="alice", budget_category=BudgetCategory.CLI, budget_id="default-cli"
        )

    @pytest.mark.asyncio
    async def test_category_without_assignment_or_default_is_skipped(self):
        provider = _provider_double()
        predefined = (_predefined_budget("default-platform", BudgetCategory.PLATFORM),)

        with _reset_environment(provider, assigned={}, predefined=predefined):
            await _make_service().reset_user_budget_spending(
                session=_session_yielding_user("alice"),
                user_id="u-1",
                actor_id="admin-1",
                categories=[BudgetCategory.CLI, BudgetCategory.PLATFORM],
            )

        assert _awaited_categories(provider) == [BudgetCategory.PLATFORM]

    @pytest.mark.asyncio
    async def test_provider_failure_is_logged_and_the_remaining_categories_still_reset(self):
        provider = _provider_double(failing_categories=(BudgetCategory.CLI,))
        assigned = {category: f"assigned-{category.value}" for category in BudgetCategory}

        with _reset_environment(provider, assigned=assigned) as mock_logger:
            await _make_service().reset_user_budget_spending(
                session=_session_yielding_user("alice"),
                user_id="u-1",
                actor_id="admin-1",
                categories=[BudgetCategory.CLI, BudgetCategory.PLATFORM],
            )

        assert _awaited_categories(provider) == [BudgetCategory.CLI, BudgetCategory.PLATFORM]
        warning = mock_logger.warning.call_args.args[0]
        assert "'u-1'" in warning
        assert "'cli'" in warning

    @pytest.mark.asyncio
    async def test_info_log_names_the_scope_and_falls_back_to_actor_id(self):
        provider = _provider_double()
        assigned = {BudgetCategory.PLATFORM: "assigned-platform"}

        with _reset_environment(provider, assigned=assigned) as mock_logger:
            await _make_service().reset_user_budget_spending(
                session=_session_yielding_user("alice"),
                user_id="u-1",
                actor_id="admin-1",
                actor_name="Admin One",
                categories=[BudgetCategory.PLATFORM],
            )
            await _make_service().reset_user_budget_spending(
                session=_session_yielding_user("alice"),
                user_id="u-1",
                actor_id="admin-1",
                actor_name="",
                categories=[BudgetCategory.PLATFORM],
            )

        named, anonymous = (call.args[0] for call in mock_logger.info.call_args_list)
        assert "'u-1'" in named
        assert "['platform']" in named
        assert "'Admin One'" in named
        assert "'admin-1'" in anonymous


@pytest.mark.asyncio
async def test_sync_backfilled_member_allocations_keeps_override_cap_when_enforce_limit_false():
    svc = _make_service()
    allocation = SimpleNamespace(id="alloc-1", user_id="user-1", allocation_mode="fixed", allocated_max_budget=500.0)
    budget = SimpleNamespace(budget_id="proj-budget-1", max_budget=5000.0)
    member_state = SimpleNamespace(
        provider="litellm", provider_member_ref="ref-1", sync_status="ok", metadata={}, budget_reset_at=None
    )
    provider = SimpleNamespace(sync_member_allocation=AsyncMock(return_value=member_state))

    await svc._sync_backfilled_member_allocations(
        session=AsyncMock(),
        provider=provider,
        budget=budget,
        allocations=[allocation],
        project_name="proj-a",
        category="cli",
        enforce_limit=False,
        project_member_budget_assignment_repository=SimpleNamespace(update_provider_metadata=AsyncMock()),
        project_budget_service=SimpleNamespace(_build_provider_metadata=MagicMock(return_value={})),
    )

    assert provider.sync_member_allocation.await_args.kwargs["effective_max_budget"] == 500.0


# ---------------------------------------------------------------------------
# TestResolveCreatorNames (EPMCDME-15628)
# ---------------------------------------------------------------------------


def _user_row(user_id: str, name: str | None, username: str) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, email=f"{username}@example.com", username=username, name=name)


class TestResolveCreatorNames:
    @pytest.mark.asyncio
    async def test_maps_creator_id_to_display_name(self):
        svc = _make_service()
        budgets = [_make_budget(created_by="user-1")]
        with patch(
            "codemie.service.budget.budget_service.user_repository.aget_users_by_ids",
            new=AsyncMock(return_value={"user-1": _user_row("user-1", "Alice Smith", "alice")}),
        ):
            result = await svc.resolve_creator_names(AsyncMock(), budgets)

        assert result == {"user-1": "Alice Smith"}

    @pytest.mark.asyncio
    async def test_falls_back_to_username_when_name_missing(self):
        svc = _make_service()
        budgets = [_make_budget(created_by="user-1")]
        with patch(
            "codemie.service.budget.budget_service.user_repository.aget_users_by_ids",
            new=AsyncMock(return_value={"user-1": _user_row("user-1", None, "alice")}),
        ):
            result = await svc.resolve_creator_names(AsyncMock(), budgets)

        assert result == {"user-1": "alice"}

    @pytest.mark.asyncio
    async def test_skips_system_and_empty_created_by(self):
        svc = _make_service()
        budgets = [
            _make_budget(created_by="system"),
            _make_budget(budget_id="b2", created_by=""),
        ]
        query_mock = AsyncMock(return_value={})
        with patch(
            "codemie.service.budget.budget_service.user_repository.aget_users_by_ids",
            new=query_mock,
        ):
            result = await svc.resolve_creator_names(AsyncMock(), budgets)

        query_mock.assert_not_called()
        assert result == {}

    @pytest.mark.asyncio
    async def test_unknown_creator_id_is_omitted(self):
        svc = _make_service()
        budgets = [_make_budget(created_by="user-missing")]
        with patch(
            "codemie.service.budget.budget_service.user_repository.aget_users_by_ids",
            new=AsyncMock(return_value={}),
        ):
            result = await svc.resolve_creator_names(AsyncMock(), budgets)

        assert result == {}

    @pytest.mark.asyncio
    async def test_soft_deleted_creator_is_omitted(self):
        """aget_users_by_ids filters deleted_at IS NULL, so a soft-deleted creator
        never appears in the returned map and created_by_name resolves to null."""
        svc = _make_service()
        budgets = [_make_budget(created_by="deleted-user-1")]
        with patch(
            "codemie.service.budget.budget_service.user_repository.aget_users_by_ids",
            new=AsyncMock(return_value={}),
        ) as query_mock:
            result = await svc.resolve_creator_names(AsyncMock(), budgets)

        query_mock.assert_called_once()
        assert result == {}

    @pytest.mark.asyncio
    async def test_duplicate_creators_trigger_single_batched_lookup_with_one_id(self):
        svc = _make_service()
        budgets = [
            _make_budget(budget_id="b1", created_by="user-1"),
            _make_budget(budget_id="b2", created_by="user-1"),
        ]
        query_mock = AsyncMock(return_value={"user-1": _user_row("user-1", "Alice Smith", "alice")})
        with patch(
            "codemie.service.budget.budget_service.user_repository.aget_users_by_ids",
            new=query_mock,
        ):
            result = await svc.resolve_creator_names(AsyncMock(), budgets)

        query_mock.assert_called_once()
        _, call_args, call_kwargs = query_mock.mock_calls[0]
        ids_arg = call_args[1] if len(call_args) > 1 else call_kwargs["user_ids"]
        assert ids_arg == {"user-1"}
        assert result == {"user-1": "Alice Smith"}
