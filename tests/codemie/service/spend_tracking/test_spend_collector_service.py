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

"""Unit tests for LiteLLMSpendCollectorService."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from codemie.repository.application_repository import ApplicationRepository
from codemie.repository.project_budget_repository import ResetWindowMemberAllocationRow
from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.provider import MemberBudgetSpendSnapshot, PersonalSpendEntry, ProjectBudgetSpendSnapshot
from codemie.service.spend_tracking.spend_collector_service import (
    InvalidSpendSnapshotError,
    LiteLLMSpendCollectorService,
)
from codemie.service.spend_tracking.config import SPEND_TRACKING_LOCK_ID
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service() -> LiteLLMSpendCollectorService:
    return LiteLLMSpendCollectorService(
        app_repository=MagicMock(spec=ApplicationRepository),
        tracking_repository=MagicMock(spec=ProjectSpendTrackingRepository),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _prev_row(
    key_hash: str,
    cumulative: Decimal,
    budget_period_spend: Decimal | None = None,
    budget_reset_at: datetime | None = None,
    spend_date: datetime | None = None,
) -> ProjectSpendTracking:
    return ProjectSpendTracking(
        id=uuid4(),
        project_name="foo-bar",
        key_hash=key_hash,
        spend_subject_type="key",
        spend_date=spend_date or datetime(2026, 3, 16, 0, 0, tzinfo=timezone.utc),
        daily_spend=budget_period_spend if budget_period_spend is not None else cumulative,
        cumulative_spend=cumulative,
        budget_period_spend=budget_period_spend if budget_period_spend is not None else cumulative,
        budget_reset_at=budget_reset_at,
    )


# ---------------------------------------------------------------------------
# TestComputeDelta — pure logic, no I/O
# ---------------------------------------------------------------------------


class TestComputeDelta:
    """Tests for _compute_spend_snapshot: reset-aware delta and cumulative logic."""

    def test_no_prior_row_returns_current_spend(self):
        """First-run bootstrap: budget-period spend seeds both delta and lifetime cumulative."""
        service = _make_service()
        current = Decimal("5.25")

        result = service._compute_spend_snapshot(
            current_budget_period_spend=current,
            prev_row=None,
            snapshot_at=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
        )

        assert result == (current, current)

    def test_no_prior_row_with_zero_spend_returns_zeroes(self):
        """Bootstrap with zero spend should keep both daily and cumulative values at zero."""
        service = _make_service()

        result = service._compute_spend_snapshot(
            current_budget_period_spend=Decimal("0"),
            prev_row=None,
            snapshot_at=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
        )

        assert result == (Decimal("0"), Decimal("0"))

    def test_normal_delta_current_greater_than_prev(self):
        """Before the reset moment, delta is derived from budget-period spend difference."""
        service = _make_service()
        prev = _prev_row(
            "abc123",
            cumulative=Decimal("10.00"),
            budget_period_spend=Decimal("3.00"),
            budget_reset_at=datetime(2026, 3, 18, 0, 0, tzinfo=timezone.utc),
        )
        current = Decimal("5.50")

        result = service._compute_spend_snapshot(
            current_budget_period_spend=current,
            prev_row=prev,
            snapshot_at=datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc),
        )

        assert result == (Decimal("2.50"), Decimal("12.50"))

    def test_normal_delta_current_equals_prev(self):
        """Unchanged budget-period spend produces zero delta."""
        service = _make_service()
        prev = _prev_row(
            "abc123",
            cumulative=Decimal("10.00"),
            budget_period_spend=Decimal("3.00"),
            budget_reset_at=datetime(2026, 3, 18, 0, 0, tzinfo=timezone.utc),
        )
        current = Decimal("3.00")

        result = service._compute_spend_snapshot(
            current_budget_period_spend=current,
            prev_row=prev,
            snapshot_at=datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc),
        )

        assert result == (Decimal("0"), Decimal("10.00"))

    def test_budget_reset_detected_by_reset_timestamp(self):
        """Crossing the previous reset boundary seeds delta from current budget-period spend."""
        service = _make_service()
        prev = _prev_row(
            "abc123",
            cumulative=Decimal("10.00"),
            budget_period_spend=Decimal("9.00"),
            budget_reset_at=datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc),
        )
        current = Decimal("0.75")

        result = service._compute_spend_snapshot(
            current_budget_period_spend=current,
            prev_row=prev,
            snapshot_at=datetime(2026, 3, 17, 0, 5, tzinfo=timezone.utc),
        )

        assert result == (current, Decimal("10.75"))

    def test_same_reset_timestamp_after_boundary_is_treated_as_reset(self):
        """Passing the previous reset time should start a new period even if the API repeats the same timestamp."""
        service = _make_service()
        prev = _prev_row(
            "abc123",
            cumulative=Decimal("10.00"),
            budget_period_spend=Decimal("9.00"),
            budget_reset_at=datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc),
        )

        result = service._compute_spend_snapshot(
            current_budget_period_spend=Decimal("0.50"),
            prev_row=prev,
            snapshot_at=datetime(2026, 3, 17, 0, 1, tzinfo=timezone.utc),
        )

        assert result == (Decimal("0.50"), Decimal("10.50"))

    def test_missing_reset_metadata_with_increasing_spend_uses_difference(self):
        """Without reset metadata, increasing budget-period spend should still use the normal delta path."""
        service = _make_service()
        prev = _prev_row("abc123", cumulative=Decimal("8.00"), budget_period_spend=Decimal("2.00"))

        result = service._compute_spend_snapshot(
            current_budget_period_spend=Decimal("3.25"),
            prev_row=prev,
            snapshot_at=datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc),
        )

        assert result == (Decimal("1.25"), Decimal("9.25"))

    def test_budget_reset_fallback_logs_warning_when_metadata_missing(self):
        """Decreasing budget-period spend without reset metadata falls back to reset semantics."""
        service = _make_service()
        prev = _prev_row("abc123", cumulative=Decimal("10.00"), budget_period_spend=Decimal("9.00"))
        current = Decimal("0.75")

        with patch("codemie.service.spend_tracking.spend_collector_service.logger") as mock_logger:
            result = service._compute_spend_snapshot(
                current_budget_period_spend=current,
                prev_row=prev,
                snapshot_at=datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc),
            )

        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "reset" in warning_msg.lower() or "budget" in warning_msg.lower()
        assert result == (current, Decimal("10.75"))

    def test_rounding_noise_does_not_trigger_false_reset(self):
        """Float artifacts should be quantized before comparison so equal values produce zero delta."""
        service = _make_service()
        prev = _prev_row("abc123", cumulative=Decimal("0.052368750"), budget_period_spend=Decimal("0.052368750"))

        result = service._compute_spend_snapshot(
            current_budget_period_spend=Decimal("0.05236874999999999"),
            prev_row=prev,
            snapshot_at=datetime(2026, 3, 23, 17, 30, tzinfo=timezone.utc),
        )

        assert result == (Decimal("0"), Decimal("0.052368750"))

    def test_raises_when_cumulative_spend_would_decrease(self):
        """Cumulative spend is a hard invariant and must never move backward."""
        service = _make_service()
        prev = _prev_row("epmedec", cumulative=Decimal("10.000000000"), budget_period_spend=Decimal("5.000000000"))

        with patch.object(
            service,
            "_quantize_spend",
            side_effect=[
                Decimal("6.000000000"),
                Decimal("5.000000000"),
                Decimal("10.000000000"),
                Decimal("1.000000000"),
                Decimal("9.000000000"),
            ],
        ):
            with pytest.raises(InvalidSpendSnapshotError, match="cumulative spend decreased"):
                service._compute_spend_snapshot(
                    current_budget_period_spend=Decimal("6.000000000"),
                    prev_row=prev,
                    snapshot_at=datetime(2026, 3, 23, 18, 0, tzinfo=timezone.utc),
                )

    def test_extractors_support_raw_litellm_key_info_payload(self):
        """Raw /key/info responses with nested info fields should be parsed correctly."""
        payload = _raw_litellm_key_info_payload(
            spend=0.0024948,
            budget_reset_at="2026-03-24T00:00:00+00:00",
            max_budget=1.0,
            budget_duration="24h",
        )[0]

        assert LiteLLMSpendCollectorService._extract_budget_period_spend(payload) == Decimal("0.0024948")


# ---------------------------------------------------------------------------
# TestDidBudgetReset — calendar-aware reset detection
# ---------------------------------------------------------------------------


class TestDidBudgetReset:
    """Tests for _did_budget_reset: calendar-aware reset detection."""

    def _prev(self, spend_date: datetime) -> ProjectSpendTracking:
        return _prev_row("h", Decimal("5.00"), spend_date=spend_date)

    def _budget(self, budget_duration: str, budget_reset_at: str) -> object:
        return _make_budget("b1", "cat", budget_duration=budget_duration, budget_reset_at=budget_reset_at)

    def test_monthly_reset_detected_for_31_day_month(self):
        """Collector running on Jan 1 (31-day month) must detect the reset.

        January has 31 days. next_reset = Feb 1. The 30-day approximation places
        last_reset on Jan 2, which is after the snapshot (Jan 1 00:01) → False negative.
        The calendar-aware fix places last_reset on Jan 1 → True.
        """
        prev = self._prev(datetime(2025, 12, 31, 23, 59, tzinfo=timezone.utc))
        budget = self._budget("monthly", "2026-02-01T00:00:00+00:00")
        snapshot = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)

        assert LiteLLMSpendCollectorService._did_budget_reset(prev, budget, snapshot) is True

    def test_monthly_reset_detected_for_28_day_february(self):
        """Collector running after Feb 1 (28-day Feb) must detect the reset.

        February has 28 days. next_reset = Mar 1. The 30-day approximation places
        last_reset on Jan 30, which is before prev_spend_date (Jan 31) → False negative.
        The calendar-aware fix places last_reset on Feb 1 → True.
        """
        prev = self._prev(datetime(2026, 1, 31, 0, 0, tzinfo=timezone.utc))
        budget = self._budget("monthly", "2026-03-01T00:00:00+00:00")
        snapshot = datetime(2026, 2, 2, 0, 0, tzinfo=timezone.utc)

        assert LiteLLMSpendCollectorService._did_budget_reset(prev, budget, snapshot) is True


# ---------------------------------------------------------------------------
# TestHashKey — static method
# ---------------------------------------------------------------------------


class TestHashKey:
    def test_returns_sha256_hex_digest(self):
        key = "sk-test-key-12345"
        expected = hashlib.sha256(key.encode()).hexdigest()
        assert LiteLLMSpendCollectorService._hash_key(key) == expected


def _raw_litellm_key_info_payload(
    spend: float,
    budget_reset_at: str | None = None,
    max_budget: float | None = None,
    budget_duration: str | None = None,
) -> list[dict]:
    return [
        {
            "key": "sk-1",
            "info": {
                "key_alias": "test@example.com",
                "spend": spend,
                "max_budget": max_budget,
                "budget_duration": budget_duration,
                "budget_reset_at": budget_reset_at,
            },
        }
    ]


def _make_budget(
    budget_id: str,
    budget_category: str,
    budget_duration: str = "30d",
    budget_reset_at: str | None = None,
) -> object:
    """Build a minimal Budget-like object for tests (avoids DB-required fields)."""
    return SimpleNamespace(
        budget_id=budget_id,
        budget_category=budget_category,
        budget_duration=budget_duration,
        budget_reset_at=budget_reset_at,
        max_budget=10.0,
        soft_budget=0.0,
    )


def _make_personal_entry(
    user_identifier: str, budget_id: str, budget_category: str, spend: Decimal
) -> PersonalSpendEntry:
    return PersonalSpendEntry(
        user_identifier=user_identifier,
        budget_id=budget_id,
        budget_category=budget_category,
        spend=spend,
    )


def _make_budget_prev_row(
    project_name: str,
    budget_id: str,
    spend: Decimal,
    spend_subject_type: str,
    user_id: str | None = None,
) -> ProjectSpendTracking:
    """Build a ProjectSpendTracking prev-row for budget/member_budget zero-delta tests."""
    return ProjectSpendTracking(
        id=uuid4(),
        project_name=project_name,
        budget_id=budget_id,
        user_id=user_id,
        spend_subject_type=spend_subject_type,
        spend_date=datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc),
        daily_spend=spend,
        cumulative_spend=spend,
        budget_period_spend=spend,
    )


@pytest.fixture
def mock_session():
    """Async mock for the database session."""
    return AsyncMock()


@pytest.fixture
def async_session_ctx(mock_session):
    """Async context manager that yields mock_session."""

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    return _ctx


def _budget_only_service(
    get_latest_prev: dict | None = None,
) -> LiteLLMSpendCollectorService:
    """Service with mocked repositories preset for budget-path-only tests."""
    service = _make_service()
    service._tracking_repository.get_latest_before_by_budget_category_ids = AsyncMock(
        return_value=get_latest_prev or {}
    )
    service._tracking_repository.insert_budget_entries = AsyncMock()
    return service


class TestCollectBudgetBased:
    """Tests for _collect_budget_based: personal spend scanning with DB budget meta."""

    @pytest.fixture(autouse=True)
    def mock_budget_repo(self):
        with patch("codemie.service.spend_tracking.spend_collector_service.budget_repository") as mock_repo:
            mock_repo.get_all_keyed_by_id = AsyncMock(return_value={})
            yield mock_repo

    @pytest.fixture(autouse=True)
    def mock_provider_base(self):
        """Patch provider with async-safe defaults; individual tests override collect_personal_spend."""
        with patch("codemie.service.spend_tracking.spend_collector_service.get_active_provider") as mock_provider:
            mock_provider.return_value.collect_project_budget_spend = AsyncMock(return_value=[])
            mock_provider.return_value.collect_member_budget_spend = AsyncMock(return_value=[])
            mock_provider.return_value.collect_personal_spend = AsyncMock(return_value=[])
            self._mock_provider = mock_provider
            yield mock_provider

    @pytest.mark.asyncio
    async def test_first_run_bootstrap_seeds_daily_and_cumulative(
        self, mock_session, async_session_ctx, mock_budget_repo, mock_provider_base
    ):
        """First run (no prior row): current spend seeds both daily and cumulative.
        budget_category is taken from the DB Budget row, not derived.
        """
        service = _budget_only_service()
        mock_budget_repo.get_all_keyed_by_id = AsyncMock(return_value={"cli": _make_budget("cli", "cli")})
        mock_provider_base.return_value.collect_personal_spend = AsyncMock(
            return_value=[_make_personal_entry("alice@example.com", "cli", "cli", Decimal("5.0"))]
        )

        with patch(
            "codemie.service.spend_tracking.spend_collector_service.get_async_session",
            side_effect=lambda: async_session_ctx(),
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 1
        rows = service._tracking_repository.insert_budget_entries.call_args[0][1]
        assert len(rows) == 1
        row = rows[0]
        assert row.project_name == "alice@example.com"
        assert row.budget_id == "cli"
        assert row.budget_category == "cli"
        assert row.spend_subject_type == "budget"
        assert row.daily_spend == Decimal("5.0")
        assert row.cumulative_spend == Decimal("5.0")
        assert row.budget_period_spend == Decimal("5.0")
        assert row.key_hash is None

    @pytest.mark.asyncio
    async def test_delta_computed_against_prev_budget_row(
        self, mock_session, async_session_ctx, mock_budget_repo, mock_provider_base
    ):
        """Normal delta: lifetime cumulative grows by the period-spend difference."""
        prev = _prev_row("unused", cumulative=Decimal("10.00"), budget_period_spend=Decimal("3.00"))
        service = _budget_only_service(get_latest_prev={("alice@example.com", "cli"): prev})
        mock_budget_repo.get_all_keyed_by_id = AsyncMock(return_value={"cli": _make_budget("cli", "cli")})
        mock_provider_base.return_value.collect_personal_spend = AsyncMock(
            return_value=[_make_personal_entry("alice@example.com", "cli", "cli", Decimal("5.50"))]
        )

        with patch(
            "codemie.service.spend_tracking.spend_collector_service.get_async_session",
            side_effect=lambda: async_session_ctx(),
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 1
        row = service._tracking_repository.insert_budget_entries.call_args[0][1][0]
        assert row.daily_spend == Decimal("2.50")
        assert row.cumulative_spend == Decimal("12.50")
        assert row.budget_period_spend == Decimal("5.50")

    @pytest.mark.asyncio
    async def test_zero_delta_budget_row_not_persisted(
        self, mock_session, async_session_ctx, mock_budget_repo, mock_provider_base
    ):
        """Unchanged spend (zero delta) produces no row."""
        prev = _prev_row("unused", cumulative=Decimal("10.00"), budget_period_spend=Decimal("3.00"))
        service = _budget_only_service(get_latest_prev={("alice@example.com", "cli"): prev})
        mock_budget_repo.get_all_keyed_by_id = AsyncMock(return_value={"cli": _make_budget("cli", "cli")})
        mock_provider_base.return_value.collect_personal_spend = AsyncMock(
            return_value=[_make_personal_entry("alice@example.com", "cli", "cli", Decimal("3.00"))]
        )

        with patch(
            "codemie.service.spend_tracking.spend_collector_service.get_async_session",
            side_effect=lambda: async_session_ctx(),
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 0
        service._tracking_repository.insert_budget_entries.assert_called_once_with(mock_session, [])

    @pytest.mark.asyncio
    async def test_budget_category_from_entry_when_budget_not_in_db(
        self, mock_session, async_session_ctx, mock_provider_base
    ):
        """When budget_id is absent from DB, budget_category comes from PersonalSpendEntry."""
        service = _budget_only_service()
        mock_provider_base.return_value.collect_personal_spend = AsyncMock(
            return_value=[_make_personal_entry("bob@example.com", "unknown_budget", "premium_models", Decimal("2.0"))]
        )

        with patch(
            "codemie.service.spend_tracking.spend_collector_service.get_async_session",
            side_effect=lambda: async_session_ctx(),
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 1
        row = service._tracking_repository.insert_budget_entries.call_args[0][1][0]
        assert row.budget_category == "premium_models"
        assert row.project_name == "bob@example.com"

    @pytest.mark.asyncio
    async def test_entry_without_budget_id_skipped(self, mock_session, async_session_ctx, mock_provider_base):
        """Entries without budget_id are skipped (project-scoped are pre-filtered by provider)."""
        service = _budget_only_service()
        mock_provider_base.return_value.collect_personal_spend = AsyncMock(
            return_value=[
                PersonalSpendEntry(
                    user_identifier="alice@example.com",
                    budget_id="",
                    budget_category="platform",
                    spend=Decimal("2.0"),
                )
            ]
        )

        with patch(
            "codemie.service.spend_tracking.spend_collector_service.get_async_session",
            side_effect=lambda: async_session_ctx(),
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 0
        service._tracking_repository.get_latest_before_by_budget_category_ids.assert_not_awaited()
        service._tracking_repository.insert_budget_entries.assert_not_called()

    @pytest.mark.asyncio
    async def test_reset_detected_via_db_budget_meta(
        self, mock_session, async_session_ctx, mock_budget_repo, mock_provider_base
    ):
        """Budget reset is detected using budget_reset_at + budget_duration from DB.
        After reset, current period spend becomes the daily delta.
        """
        prev = _prev_row(
            "unused",
            cumulative=Decimal("10.00"),
            budget_period_spend=Decimal("9.00"),
            spend_date=datetime(2026, 3, 16, 23, 55, tzinfo=timezone.utc),
        )
        service = _budget_only_service(get_latest_prev={("alice@example.com", "cli"): prev})
        mock_budget_repo.get_all_keyed_by_id = AsyncMock(
            return_value={
                "cli": _make_budget(
                    "cli",
                    "cli",
                    budget_duration="1d",
                    budget_reset_at="2026-03-18T00:00:00+00:00",
                )
            }
        )
        mock_provider_base.return_value.collect_personal_spend = AsyncMock(
            return_value=[_make_personal_entry("alice@example.com", "cli", "cli", Decimal("0.75"))]
        )
        snapshot_at = datetime(2026, 3, 17, 0, 5, tzinfo=timezone.utc)

        with patch(
            "codemie.service.spend_tracking.spend_collector_service.get_async_session",
            side_effect=lambda: async_session_ctx(),
        ):
            count = await service.collect(target_date=snapshot_at)

        assert count == 1
        row = service._tracking_repository.insert_budget_entries.call_args[0][1][0]
        # Reset detected: daily = current period spend (not diff from prev)
        assert row.daily_spend == Decimal("0.75")
        assert row.cumulative_spend == Decimal("10.75")

    @pytest.mark.asyncio
    async def test_multiple_entries_produce_independent_rows(
        self, mock_session, async_session_ctx, mock_budget_repo, mock_provider_base
    ):
        """Multiple personal spend entries → independent budget rows with correct project names."""
        service = _budget_only_service()
        mock_budget_repo.get_all_keyed_by_id = AsyncMock(
            return_value={
                "cli": _make_budget("cli", "cli"),
                "pm": _make_budget("pm", "premium_models"),
            }
        )
        mock_provider_base.return_value.collect_personal_spend = AsyncMock(
            return_value=[
                _make_personal_entry("alice@example.com", "cli", "cli", Decimal("3.0")),
                _make_personal_entry("bob@example.com", "pm", "premium_models", Decimal("7.5")),
            ]
        )

        with patch(
            "codemie.service.spend_tracking.spend_collector_service.get_async_session",
            side_effect=lambda: async_session_ctx(),
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 2
        rows = service._tracking_repository.insert_budget_entries.call_args[0][1]
        rows_by_project = {r.project_name: r for r in rows}
        assert "alice@example.com" in rows_by_project
        assert "bob@example.com" in rows_by_project
        assert rows_by_project["alice@example.com"].budget_id == "cli"
        assert rows_by_project["alice@example.com"].budget_category == "cli"
        assert rows_by_project["bob@example.com"].budget_id == "pm"
        assert rows_by_project["bob@example.com"].budget_category == "premium_models"

    @pytest.mark.asyncio
    async def test_warns_when_same_budget_id_arrives_with_multiple_categories(
        self, mock_session, async_session_ctx, mock_budget_repo, mock_provider_base
    ):
        """Provider category collisions for the same user/budget_id are logged and deduped."""
        service = _budget_only_service()
        mock_budget_repo.get_all_keyed_by_id = AsyncMock(return_value={"cli": _make_budget("cli", "cli")})
        mock_provider_base.return_value.collect_personal_spend = AsyncMock(
            return_value=[
                _make_personal_entry("alice@example.com", "cli", "cli", Decimal("3.0")),
                _make_personal_entry("alice@example.com", "cli", "platform", Decimal("5.0")),
            ]
        )

        with (
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                side_effect=lambda: async_session_ctx(),
            ),
            patch("codemie.service.spend_tracking.spend_collector_service.logger") as mock_logger,
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 1
        rows = service._tracking_repository.insert_budget_entries.call_args[0][1]
        assert len(rows) == 1
        row = rows[0]
        assert row.project_name == "alice@example.com"
        assert row.budget_id == "cli"
        assert row.budget_category == "cli"
        assert row.budget_period_spend == Decimal("5.0")
        warning_messages = [call.args[0] for call in mock_logger.warning.call_args_list]
        assert any("multiple categories" in message for message in warning_messages)

    @pytest.mark.asyncio
    async def test_no_personal_entries_skips_budget_path(self, mock_session, async_session_ctx, mock_provider_base):
        """None from collect_personal_spend → budget path skipped, 0 rows."""
        service = _budget_only_service()
        mock_provider_base.return_value.collect_personal_spend = AsyncMock(return_value=None)

        with patch(
            "codemie.service.spend_tracking.spend_collector_service.get_async_session",
            side_effect=lambda: async_session_ctx(),
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 0
        service._tracking_repository.insert_budget_entries.assert_not_called()


class TestCollectMemberBudgetResetWindow:
    @pytest.mark.asyncio
    async def test_collect_member_budget_reset_window_returns_zero_when_no_allocations(self, async_session_ctx):
        service = _make_service()
        service._tracking_repository.insert_member_budget_entries = AsyncMock()

        with (
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                side_effect=lambda: async_session_ctx(),
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.project_member_budget_assignment_repository"
            ) as mock_alloc_repo,
        ):
            mock_alloc_repo.get_allocations_resetting_within_window = AsyncMock(return_value=[])

            count = await service.collect_member_budget_reset_window(
                snapshot_at=datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
            )

        assert count == 0
        service._tracking_repository.insert_member_budget_entries.assert_not_called()

    @pytest.mark.asyncio
    async def test_collect_member_budget_reset_window_inserts_only_member_rows(
        self,
        mock_session,
        async_session_ctx,
    ):
        service = _make_service()
        allocation = ResetWindowMemberAllocationRow(
            allocation_id="alloc-1",
            project_name="proj-a",
            budget_id="budget-1",
            budget_category="cli",
            user_id="user-1",
            provider_metadata={"provider_member_ref": "member-ref-1"},
            budget_reset_at="2026-04-23T10:10:00Z",
        )
        snapshot = MemberBudgetSpendSnapshot(
            project_name="proj-a",
            budget_category=BudgetCategory.CLI,
            budget_id="budget-1",
            user_id="user-1",
            spend=Decimal("2.50"),
            budget_reset_at="2026-04-23T10:10:00Z",
            provider_subject_id="member-ref-1",
        )

        service._tracking_repository.get_latest_before_by_member_budget_ids = AsyncMock(return_value={})
        service._tracking_repository.insert_member_budget_entries = AsyncMock()

        with (
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                side_effect=lambda: async_session_ctx(),
            ),
            patch("codemie.service.spend_tracking.spend_collector_service.budget_repository") as mock_budget_repo,
            patch(
                "codemie.service.spend_tracking.spend_collector_service.project_member_budget_assignment_repository"
            ) as mock_alloc_repo,
            patch("codemie.service.spend_tracking.spend_collector_service.get_active_provider") as mock_provider,
        ):
            mock_alloc_repo.get_allocations_resetting_within_window = AsyncMock(return_value=[allocation])
            mock_budget_repo.get_all_keyed_by_id = AsyncMock(
                return_value={"budget-1": _make_budget("budget-1", "cli", budget_reset_at="2026-04-23T10:10:00Z")}
            )
            mock_provider.return_value.collect_member_budget_spend_for_refs = AsyncMock(return_value=[snapshot])

            count = await service.collect_member_budget_reset_window(
                snapshot_at=datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
            )

        assert count == 1
        rows = service._tracking_repository.insert_member_budget_entries.call_args.args[1]
        assert len(rows) == 1
        assert rows[0].spend_subject_type == "member_budget"
        assert rows[0].project_name == "proj-a"
        assert rows[0].user_id == "user-1"

    @pytest.mark.asyncio
    async def test_zero_delta_reset_window_member_row_not_persisted(
        self,
        mock_session,
        async_session_ctx,
    ):
        """Zero daily_spend in reset-window path produces no row."""
        service = _make_service()
        allocation = ResetWindowMemberAllocationRow(
            allocation_id="alloc-1",
            project_name="proj-a",
            budget_id="budget-1",
            budget_category="cli",
            user_id="user-1",
            provider_metadata={"provider_member_ref": "member-ref-1"},
            budget_reset_at="2026-04-23T10:10:00Z",
        )
        prev = _make_budget_prev_row("proj-a", "budget-1", Decimal("2.50"), "member_budget", user_id="user-1")
        snapshot = MemberBudgetSpendSnapshot(
            project_name="proj-a",
            budget_category=BudgetCategory.CLI,
            budget_id="budget-1",
            user_id="user-1",
            spend=Decimal("2.50"),  # same as prev → delta == 0
            provider_subject_id="member-ref-1",
        )

        service._tracking_repository.get_latest_before_by_member_budget_ids = AsyncMock(
            return_value={("proj-a", "budget-1", "user-1"): prev}
        )
        service._tracking_repository.insert_member_budget_entries = AsyncMock()

        with (
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                side_effect=lambda: async_session_ctx(),
            ),
            patch("codemie.service.spend_tracking.spend_collector_service.budget_repository") as mock_budget_repo,
            patch(
                "codemie.service.spend_tracking.spend_collector_service.project_member_budget_assignment_repository"
            ) as mock_alloc_repo,
            patch("codemie.service.spend_tracking.spend_collector_service.get_active_provider") as mock_provider,
        ):
            mock_alloc_repo.get_allocations_resetting_within_window = AsyncMock(return_value=[allocation])
            mock_budget_repo.get_all_keyed_by_id = AsyncMock(return_value={"budget-1": _make_budget("budget-1", "cli")})
            mock_provider.return_value.collect_member_budget_spend_for_refs = AsyncMock(return_value=[snapshot])

            count = await service.collect_member_budget_reset_window(
                snapshot_at=datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
            )

        assert count == 0
        service._tracking_repository.insert_member_budget_entries.assert_called_once_with(mock_session, [])


# ---------------------------------------------------------------------------
# TestCollectProviderProjectBudgets
# ---------------------------------------------------------------------------


class TestCollectProviderProjectBudgets:
    """Tests for _collect_provider_project_budgets: project_budget and member_budget paths."""

    @pytest.fixture(autouse=True)
    def mock_budget_repo(self):
        with patch("codemie.service.spend_tracking.spend_collector_service.budget_repository") as mock_repo:
            mock_repo.get_all_keyed_by_id = AsyncMock(return_value={})
            yield mock_repo

    @pytest.fixture(autouse=True)
    def mock_provider(self):
        with patch("codemie.service.spend_tracking.spend_collector_service.get_active_provider") as mock:
            mock.return_value.collect_project_budget_spend = AsyncMock(return_value=[])
            mock.return_value.collect_member_budget_spend = AsyncMock(return_value=[])
            yield mock

    @pytest.mark.asyncio
    async def test_zero_delta_project_budget_row_not_persisted(
        self,
        mock_session,
        async_session_ctx,
        mock_budget_repo,
        mock_provider,
    ):
        """Zero daily_spend in project_budget path produces no row."""
        prev = _make_budget_prev_row("proj-x", "budget-1", Decimal("5.00"), "project_budget")
        snapshot = ProjectBudgetSpendSnapshot(
            project_name="proj-x",
            budget_category=BudgetCategory.CLI,
            budget_id="budget-1",
            spend=Decimal("5.00"),  # same as prev → delta == 0
        )

        service = _make_service()
        service._tracking_repository.get_latest_before_by_project_budget_ids = AsyncMock(
            return_value={("proj-x", "budget-1"): prev}
        )
        service._tracking_repository.get_latest_before_by_member_budget_ids = AsyncMock(return_value={})
        service._tracking_repository.insert_project_budget_entries = AsyncMock()
        service._tracking_repository.insert_member_budget_entries = AsyncMock()

        mock_budget_repo.get_all_keyed_by_id = AsyncMock(return_value={"budget-1": _make_budget("budget-1", "cli")})
        mock_provider.return_value.collect_project_budget_spend = AsyncMock(return_value=[snapshot])

        with patch(
            "codemie.service.spend_tracking.spend_collector_service.get_async_session",
            side_effect=lambda: async_session_ctx(),
        ):
            count = await service._collect_provider_project_budgets(
                target_snapshot_at=datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
            )

        assert count == 0
        service._tracking_repository.insert_project_budget_entries.assert_called_once_with(mock_session, [])

    @pytest.mark.asyncio
    async def test_zero_delta_member_budget_row_not_persisted(
        self,
        mock_session,
        async_session_ctx,
        mock_budget_repo,
        mock_provider,
    ):
        """Zero daily_spend in member_budget path produces no row."""
        prev = _make_budget_prev_row("proj-y", "budget-2", Decimal("3.00"), "member_budget", user_id="user-42")
        snapshot = MemberBudgetSpendSnapshot(
            project_name="proj-y",
            budget_category=BudgetCategory.CLI,
            budget_id="budget-2",
            user_id="user-42",
            spend=Decimal("3.00"),  # same as prev → delta == 0
            provider_subject_id="subj-42",
        )

        service = _make_service()
        service._tracking_repository.get_latest_before_by_project_budget_ids = AsyncMock(return_value={})
        service._tracking_repository.get_latest_before_by_member_budget_ids = AsyncMock(
            return_value={("proj-y", "budget-2", "user-42"): prev}
        )
        service._tracking_repository.insert_project_budget_entries = AsyncMock()
        service._tracking_repository.insert_member_budget_entries = AsyncMock()

        mock_budget_repo.get_all_keyed_by_id = AsyncMock(return_value={"budget-2": _make_budget("budget-2", "cli")})
        mock_provider.return_value.collect_member_budget_spend = AsyncMock(return_value=[snapshot])

        with patch(
            "codemie.service.spend_tracking.spend_collector_service.get_async_session",
            side_effect=lambda: async_session_ctx(),
        ):
            count = await service._collect_provider_project_budgets(
                target_snapshot_at=datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
            )

        assert count == 0
        service._tracking_repository.insert_member_budget_entries.assert_called_once_with(mock_session, [])


# ---------------------------------------------------------------------------
# TestSchedulerJobRegistration
# ---------------------------------------------------------------------------


class TestSchedulerJobRegistration:
    """Tests for SpendTrackingScheduler job registration."""

    def test_spend_collector_disabled_skips_job_registration(self):
        """LITELLM_SPEND_COLLECTOR_ENABLED=False → spend collector job is not added."""
        from codemie.service.spend_tracking.scheduler import SpendTrackingScheduler

        mock_scheduler = MagicMock()
        mock_scheduler.running = False

        with patch("codemie.service.spend_tracking.scheduler.config") as mock_config:
            mock_config.LITELLM_SPEND_COLLECTOR_ENABLED = False
            mock_config.LITELLM_SPEND_COLLECTOR_SCHEDULE = "30 0 * * *"
            mock_config.LITELLM_BUDGET_RESET_RECONCILIATION_ENABLED = False
            mock_config.LITELLM_SPEND_UPLOADER_ENABLED = False
            mock_config.LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE = "10 0 * * *"

            scheduler = SpendTrackingScheduler(scheduler=mock_scheduler)
            scheduler.start()

        mock_scheduler.add_job.assert_not_called()

    def test_spend_collector_enabled_registers_job(self):
        """LITELLM_SPEND_COLLECTOR_ENABLED=True → spend collector job is registered."""
        from codemie.service.spend_tracking.scheduler import SpendTrackingScheduler

        mock_scheduler = MagicMock()
        mock_scheduler.running = False

        with (
            patch("codemie.service.spend_tracking.scheduler.config") as mock_config,
            patch("codemie.service.spend_tracking.scheduler.ApplicationRepository"),
            patch("codemie.service.spend_tracking.scheduler.ProjectSpendTrackingRepository"),
            patch("codemie.service.spend_tracking.scheduler.LiteLLMSpendCollectorService"),
        ):
            mock_config.LITELLM_SPEND_COLLECTOR_ENABLED = True
            mock_config.LITELLM_SPEND_COLLECTOR_SCHEDULE = "30 0 * * *"
            mock_config.LITELLM_BUDGET_RESET_RECONCILIATION_ENABLED = False
            mock_config.LITELLM_SPEND_UPLOADER_ENABLED = False
            mock_config.LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE = "10 0 * * *"

            scheduler = SpendTrackingScheduler(scheduler=mock_scheduler)
            scheduler.start()

        mock_scheduler.add_job.assert_called_once()
        job_kwargs = mock_scheduler.add_job.call_args[1]
        assert job_kwargs["id"] == "litellm_spend_collector"
        assert job_kwargs["replace_existing"] is True

    def test_invalid_cron_expression_skips_registration(self):
        """Invalid LITELLM_SPEND_COLLECTOR_SCHEDULE → job is not registered, error is logged."""
        from codemie.service.spend_tracking.scheduler import SpendTrackingScheduler

        mock_scheduler = MagicMock()
        mock_scheduler.running = False

        with (
            patch("codemie.service.spend_tracking.scheduler.config") as mock_config,
            patch("codemie.service.spend_tracking.scheduler.logger") as mock_logger,
            patch("codemie.service.spend_tracking.scheduler.ApplicationRepository"),
            patch("codemie.service.spend_tracking.scheduler.ProjectSpendTrackingRepository"),
            patch("codemie.service.spend_tracking.scheduler.LiteLLMSpendCollectorService"),
        ):
            mock_config.LITELLM_SPEND_COLLECTOR_ENABLED = True
            mock_config.LITELLM_SPEND_COLLECTOR_SCHEDULE = "not a valid cron"
            mock_config.LITELLM_BUDGET_RESET_TRACKER_ENABLED = False
            mock_config.LITELLM_BUDGET_RESET_RECONCILIATION_ENABLED = False
            mock_config.LITELLM_SPEND_UPLOADER_ENABLED = False
            mock_config.LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE = "10 0 * * *"

            scheduler = SpendTrackingScheduler(scheduler=mock_scheduler)
            scheduler.start()

        mock_scheduler.add_job.assert_not_called()
        mock_logger.error.assert_called_once()

    def test_start_registers_full_and_reset_window_jobs_when_both_enabled(self):
        """When both spend-tracking jobs are enabled, both scheduler jobs are registered."""
        from codemie.service.spend_tracking.scheduler import SpendTrackingScheduler

        mock_scheduler = MagicMock()
        mock_scheduler.running = False

        with (
            patch("codemie.service.spend_tracking.scheduler.config") as mock_config,
            patch("codemie.service.spend_tracking.scheduler.ApplicationRepository"),
            patch("codemie.service.spend_tracking.scheduler.ProjectSpendTrackingRepository"),
            patch("codemie.service.spend_tracking.scheduler.LiteLLMSpendCollectorService"),
        ):
            mock_config.LITELLM_SPEND_COLLECTOR_ENABLED = True
            mock_config.LITELLM_SPEND_COLLECTOR_SCHEDULE = "0 23 * * *"
            mock_config.LITELLM_BUDGET_RESET_TRACKER_ENABLED = True
            mock_config.LITELLM_BUDGET_RESET_TRACKER_SCHEDULE = "*/10 * * * *"
            mock_config.LITELLM_BUDGET_RESET_RECONCILIATION_ENABLED = False
            mock_config.LITELLM_SPEND_UPLOADER_ENABLED = False
            mock_config.LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE = "10 0 * * *"

            scheduler = SpendTrackingScheduler(scheduler=mock_scheduler)
            scheduler.start()

        assert mock_scheduler.add_job.call_count == 2
        job_ids = [call.kwargs["id"] for call in mock_scheduler.add_job.call_args_list]
        assert job_ids == ["litellm_spend_collector", "litellm_budget_reset_tracker"]

    def test_invalid_reset_tracker_cron_skips_only_reset_tracker_registration(self):
        """Invalid reset-tracker cron leaves the full spend collector registration intact."""
        from codemie.service.spend_tracking.scheduler import SpendTrackingScheduler

        mock_scheduler = MagicMock()
        mock_scheduler.running = False

        with (
            patch("codemie.service.spend_tracking.scheduler.config") as mock_config,
            patch("codemie.service.spend_tracking.scheduler.logger") as mock_logger,
            patch("codemie.service.spend_tracking.scheduler.ApplicationRepository"),
            patch("codemie.service.spend_tracking.scheduler.ProjectSpendTrackingRepository"),
            patch("codemie.service.spend_tracking.scheduler.LiteLLMSpendCollectorService"),
        ):
            mock_config.LITELLM_SPEND_COLLECTOR_ENABLED = True
            mock_config.LITELLM_SPEND_COLLECTOR_SCHEDULE = "0 23 * * *"
            mock_config.LITELLM_BUDGET_RESET_TRACKER_ENABLED = True
            mock_config.LITELLM_BUDGET_RESET_TRACKER_SCHEDULE = "bad cron"
            mock_config.LITELLM_BUDGET_RESET_RECONCILIATION_ENABLED = False
            mock_config.LITELLM_SPEND_UPLOADER_ENABLED = False
            mock_config.LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE = "10 0 * * *"

            scheduler = SpendTrackingScheduler(scheduler=mock_scheduler)
            scheduler.start()

        assert mock_scheduler.add_job.call_count == 1
        assert mock_scheduler.add_job.call_args.kwargs["id"] == "litellm_spend_collector"
        mock_logger.error.assert_called_once()


# ── config module ─────────────────────────────────────────────────────


def test_spend_tracking_lock_id_value():
    assert SPEND_TRACKING_LOCK_ID == 987654322


class TestIsResetTransition:
    """The predicate that separates 'nothing changed' from 'the counter dropped'."""

    @staticmethod
    def _prev(period_spend: str):
        return SimpleNamespace(
            budget_period_spend=Decimal(period_spend),
            spend_date=datetime(2026, 7, 31, tzinfo=timezone.utc),
            project_name="epm-aisa",
            budget_id="epm-aisa-platform-008b4886",
        )

    def test_drop_to_zero_is_a_reset_transition(self):
        got = LiteLLMSpendCollectorService.is_reset_transition(
            self._prev("3221.459246160"), None, Decimal("0"), datetime(2026, 8, 26, tzinfo=timezone.utc)
        )
        assert got is True

    def test_partial_drop_is_a_reset_transition(self):
        got = LiteLLMSpendCollectorService.is_reset_transition(
            self._prev("3221.459246160"), None, Decimal("38.132188600"), datetime(2026, 8, 26, tzinfo=timezone.utc)
        )
        assert got is True

    def test_unchanged_spend_is_not_a_transition(self):
        got = LiteLLMSpendCollectorService.is_reset_transition(
            self._prev("3221.46"), None, Decimal("3221.46"), datetime(2026, 8, 26, tzinfo=timezone.utc)
        )
        assert got is False

    def test_increase_is_not_a_transition(self):
        got = LiteLLMSpendCollectorService.is_reset_transition(
            self._prev("3221.46"), None, Decimal("3300.00"), datetime(2026, 8, 26, tzinfo=timezone.utc)
        )
        assert got is False

    def test_no_previous_row_is_not_a_transition(self):
        got = LiteLLMSpendCollectorService.is_reset_transition(
            None, None, Decimal("0"), datetime(2026, 8, 26, tzinfo=timezone.utc)
        )
        assert got is False

    def test_previous_zero_is_not_a_transition(self):
        """Zero to zero is 'still nothing', not a reset worth recording."""
        got = LiteLLMSpendCollectorService.is_reset_transition(
            self._prev("0"), None, Decimal("0"), datetime(2026, 8, 26, tzinfo=timezone.utc)
        )
        assert got is False
