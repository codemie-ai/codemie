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
from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
from codemie.service.spend_tracking.spend_collector_service import (
    InvalidSpendSnapshotError,
    LiteLLMSpendCollectorService,
)
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
# TestNormalizeProjectName — pure logic, no I/O
# ---------------------------------------------------------------------------


class TestNormalizeProjectName:
    """Tests for _normalize_project_name: project name extraction from user_id + budget_id."""

    def test_default_budget_id_returns_user_id_unchanged(self):
        """budget_id='default' → project_name equals user_id as-is."""
        result = LiteLLMSpendCollectorService._normalize_project_name("alice@example.com", "default")
        assert result == "alice@example.com"

    def test_non_default_budget_id_strips_underscore_suffix(self):
        """user_id ending with '_<budget_id>' → prefix becomes project_name."""
        result = LiteLLMSpendCollectorService._normalize_project_name("alice@example.com_codemie_cli", "codemie_cli")
        assert result == "alice@example.com"

    def test_user_id_without_matching_suffix_returned_unchanged(self):
        """user_id that doesn't end with '_<budget_id>' is returned as-is."""
        result = LiteLLMSpendCollectorService._normalize_project_name("alice@example.com", "codemie_cli")
        assert result == "alice@example.com"

    def test_multi_word_budget_id_stripped_correctly(self):
        """Multi-underscore category suffix is stripped correctly."""
        result = LiteLLMSpendCollectorService._normalize_project_name(
            "user@org.com_codemie_premium_models", "premium_models"
        )
        assert result == "user@org.com"

    def test_suffix_only_stripped_from_end(self):
        """budget_id appearing in the middle of user_id is NOT stripped."""
        result = LiteLLMSpendCollectorService._normalize_project_name("alice_codemie_cli_extra", "codemie_cli")
        assert result == "alice_codemie_cli_extra"

    def test_user_id_equals_budget_id_suffix_alone(self):
        """user_id that is exactly the category suffix returns empty string (edge case)."""
        result = LiteLLMSpendCollectorService._normalize_project_name("_codemie_cli", "cli")
        assert result == ""

    def test_plain_email_default_budget(self):
        """Standard email address with 'default' budget_id is unchanged."""
        result = LiteLLMSpendCollectorService._normalize_project_name("user@example.com", "default")
        assert result == "user@example.com"


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
# TestHashKey — static method
# ---------------------------------------------------------------------------


class TestHashKey:
    def test_returns_sha256_hex_digest(self):
        key = "sk-test-key-12345"
        expected = hashlib.sha256(key.encode()).hexdigest()
        assert LiteLLMSpendCollectorService._hash_key(key) == expected


# ---------------------------------------------------------------------------
# TestCollect — async integration with mocked I/O
# ---------------------------------------------------------------------------


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


def _make_app(name: str):
    app = MagicMock()
    app.name = name
    return app


def _make_setting(project_name: str, api_key: str):
    setting = MagicMock()
    setting.id = str(uuid4())
    setting.project_name = project_name
    return setting, api_key


def _spending_payload(total_spend: float, budget_reset_at: str | None = None) -> list[dict[str, float | str | None]]:
    payload: dict[str, float | str | None] = {"total_spend": total_spend}
    if budget_reset_at is not None:
        payload["budget_reset_at"] = budget_reset_at
    return [payload]


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


class TestCollect:
    """Tests for LiteLLMSpendCollectorService.collect()."""

    @pytest.fixture(autouse=True)
    def mock_budget_repo(self):
        with patch("codemie.service.spend_tracking.spend_collector_service.budget_repository") as mock_repo:
            mock_repo.get_all_keyed_by_id = AsyncMock(return_value={})
            yield mock_repo

    @pytest.mark.asyncio
    async def test_key_with_api_error_is_skipped_without_blocking_others(
        self,
        mock_session,
        async_session_ctx,
    ):
        """A key that returns None from get_all_keys_spending is skipped; others proceed."""
        service = _make_service()

        good_key = "sk-good-key"
        bad_key = "sk-bad-key"
        good_hash = _sha256(good_key)

        # Two apps, two LiteLLM settings
        good_setting = MagicMock()
        good_setting.id = "s1"
        good_setting.project_name = "foo-bar"

        bad_setting = MagicMock()
        bad_setting.id = "s2"
        bad_setting.project_name = "baz-qux"

        good_cred = MagicMock()
        good_cred.api_key = good_key

        bad_cred = MagicMock()
        bad_cred.api_key = bad_key

        service._app_repository.aget_all_non_deleted = AsyncMock(
            return_value=[_make_app("foo-bar"), _make_app("baz-qux")]
        )
        service._tracking_repository.get_latest_before_by_key_hashes = AsyncMock(return_value={})
        service._tracking_repository.insert_key_entries = AsyncMock()
        service._tracking_repository.insert_budget_entries = AsyncMock()

        # _build_credential_result returns good_cred for good_setting, bad_cred for bad_setting
        def build_cred(setting, fields, cred_class):
            if setting is good_setting:
                return good_cred
            return bad_cred

        with (
            patch("codemie.service.spend_tracking.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.spend_tracking.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.SettingsService._build_credential_result",
                side_effect=build_cred,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
            ) as mock_to_thread,
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [good_setting, bad_setting]

            # good_key returns valid data; bad_key returns None (API error / 404)
            # Budget path (no extra args) also returns None
            async def spending_side_effect(fn, *args):
                if not args:
                    return None  # budget path
                keys = args[0]
                assert len(keys) == 1
                if keys[0] == good_key:
                    return _spending_payload(2.5)
                return None

            mock_to_thread.side_effect = spending_side_effect

            count = await service.collect(target_date=date(2026, 3, 17))

        # Only good_key should produce a row
        assert count == 1
        inserted_rows = service._tracking_repository.insert_key_entries.call_args[0][1]
        assert len(inserted_rows) == 1
        assert inserted_rows[0].key_hash == good_hash
        assert inserted_rows[0].project_name == "foo-bar"
        assert inserted_rows[0].budget_period_spend == Decimal("2.5")
        assert inserted_rows[0].cumulative_spend == Decimal("2.5")

    @pytest.mark.asyncio
    async def test_collect_normal_delta(
        self,
        mock_session,
        async_session_ctx,
    ):
        """Normal delta: lifetime cumulative grows by the period-spend delta."""
        service = _make_service()
        api_key = "sk-normal-key"
        key_hash = _sha256(api_key)

        prev_cumulative = Decimal("10.00")
        current_spend = 5.50

        setting = MagicMock()
        setting.id = "s1"
        setting.project_name = "foo-bar"

        cred = MagicMock()
        cred.api_key = api_key

        service._app_repository.aget_all_non_deleted = AsyncMock(return_value=[_make_app("foo-bar")])
        prev_row = _prev_row(
            key_hash,
            cumulative=prev_cumulative,
            budget_period_spend=Decimal("3.00"),
            budget_reset_at=datetime(2026, 3, 18, 0, 0, tzinfo=timezone.utc),
        )
        service._tracking_repository.get_latest_before_by_key_hashes = AsyncMock(return_value={key_hash: prev_row})
        service._tracking_repository.insert_key_entries = AsyncMock()
        service._tracking_repository.insert_budget_entries = AsyncMock()

        spending = _spending_payload(current_spend, "2026-03-18T00:00:00+00:00")

        with (
            patch("codemie.service.spend_tracking.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.spend_tracking.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: spending if a else None,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 1
        rows = service._tracking_repository.insert_key_entries.call_args[0][1]
        assert rows[0].daily_spend == Decimal("2.50")
        assert rows[0].cumulative_spend == Decimal("12.50")
        assert rows[0].budget_period_spend == Decimal("5.50")
        assert rows[0].spend_date == datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_collect_first_run_bootstrap(
        self,
        mock_session,
        async_session_ctx,
    ):
        """First-run bootstrap: current budget-period spend seeds both tracked values."""
        service = _make_service()
        api_key = "sk-bootstrap-key"

        setting = MagicMock()
        setting.id = "s1"
        setting.project_name = "alpha-beta"

        cred = MagicMock()
        cred.api_key = api_key

        service._app_repository.aget_all_non_deleted = AsyncMock(return_value=[_make_app("alpha-beta")])
        # No prior rows
        service._tracking_repository.get_latest_before_by_key_hashes = AsyncMock(return_value={})
        service._tracking_repository.insert_key_entries = AsyncMock()
        service._tracking_repository.insert_budget_entries = AsyncMock()

        spending = _spending_payload(7.0, "2026-03-18T00:00:00+00:00")

        with (
            patch("codemie.service.spend_tracking.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.spend_tracking.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: spending if a else None,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 1
        rows = service._tracking_repository.insert_key_entries.call_args[0][1]
        assert rows[0].daily_spend == Decimal("7.0")
        assert rows[0].cumulative_spend == Decimal("7.0")
        assert rows[0].budget_period_spend == Decimal("7.0")

    @pytest.mark.asyncio
    async def test_collect_skips_zero_delta_snapshots(
        self,
        mock_session,
        async_session_ctx,
    ):
        """Zero-delta snapshots are not persisted."""
        service = _make_service()
        api_key = "sk-zero-key"
        key_hash = _sha256(api_key)

        setting = MagicMock()
        setting.id = "s1"
        setting.project_name = "zero-app"

        cred = MagicMock()
        cred.api_key = api_key

        service._app_repository.aget_all_non_deleted = AsyncMock(return_value=[_make_app("zero-app")])
        prev_row = _prev_row(
            key_hash,
            cumulative=Decimal("10.00"),
            budget_period_spend=Decimal("3.00"),
            budget_reset_at=datetime(2026, 3, 18, 0, 0, tzinfo=timezone.utc),
        )
        service._tracking_repository.get_latest_before_by_key_hashes = AsyncMock(return_value={key_hash: prev_row})
        service._tracking_repository.insert_key_entries = AsyncMock()
        service._tracking_repository.insert_budget_entries = AsyncMock()

        spending = _spending_payload(3.0, "2026-03-18T00:00:00+00:00")

        with (
            patch("codemie.service.spend_tracking.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.spend_tracking.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: spending if a else None,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 0
        service._tracking_repository.insert_key_entries.assert_called_once_with(mock_session, [])

    @pytest.mark.asyncio
    async def test_collect_skips_first_snapshot_when_spend_is_zero(
        self,
        mock_session,
        async_session_ctx,
    ):
        """A brand-new key with zero spend should not create a snapshot row."""
        service = _make_service()
        api_key = "sk-zero-bootstrap"

        setting = MagicMock()
        setting.id = "s1"
        setting.project_name = "zero-bootstrap"

        cred = MagicMock()
        cred.api_key = api_key

        service._app_repository.aget_all_non_deleted = AsyncMock(return_value=[_make_app("zero-bootstrap")])
        service._tracking_repository.get_latest_before_by_key_hashes = AsyncMock(return_value={})
        service._tracking_repository.insert_key_entries = AsyncMock()
        service._tracking_repository.insert_budget_entries = AsyncMock()

        spending = _spending_payload(0.0, "2026-03-18T00:00:00+00:00")

        with (
            patch("codemie.service.spend_tracking.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.spend_tracking.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: spending if a else None,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 0
        service._tracking_repository.insert_key_entries.assert_called_once_with(mock_session, [])

    @pytest.mark.asyncio
    async def test_collect_after_budget_reset_uses_current_budget_period_spend(
        self,
        mock_session,
        async_session_ctx,
    ):
        """After a reset boundary, the current budget-period spend becomes the delta."""
        service = _make_service()
        api_key = "sk-reset-key"
        key_hash = _sha256(api_key)

        setting = MagicMock()
        setting.id = "s1"
        setting.project_name = "reset-app"

        cred = MagicMock()
        cred.api_key = api_key

        service._app_repository.aget_all_non_deleted = AsyncMock(return_value=[_make_app("reset-app")])
        prev_row = _prev_row(
            key_hash,
            cumulative=Decimal("10.00"),
            budget_period_spend=Decimal("9.00"),
            budget_reset_at=datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc),
            spend_date=datetime(2026, 3, 16, 23, 55, tzinfo=timezone.utc),
        )
        service._tracking_repository.get_latest_before_by_key_hashes = AsyncMock(return_value={key_hash: prev_row})
        service._tracking_repository.insert_key_entries = AsyncMock()
        service._tracking_repository.insert_budget_entries = AsyncMock()

        spending = _spending_payload(0.75, "2026-03-18T00:00:00+00:00")

        with (
            patch("codemie.service.spend_tracking.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.spend_tracking.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: spending if a else None,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=datetime(2026, 3, 17, 0, 5, tzinfo=timezone.utc))

        assert count == 1
        rows = service._tracking_repository.insert_key_entries.call_args[0][1]
        assert rows[0].daily_spend == Decimal("0.75")
        assert rows[0].cumulative_spend == Decimal("10.75")
        assert rows[0].budget_period_spend == Decimal("0.75")

    @pytest.mark.asyncio
    async def test_collect_uses_exact_snapshot_timestamp_for_baseline_lookup(
        self,
        mock_session,
        async_session_ctx,
    ):
        """Baseline lookup should use the exact snapshot timestamp, including milliseconds."""
        service = _make_service()
        api_key = "sk-boundary-key"
        key_hash = _sha256(api_key)
        snapshot_at = datetime(2026, 3, 17, 12, 34, 56, 789000, tzinfo=timezone.utc)

        setting = MagicMock()
        setting.id = "s1"
        setting.project_name = "boundary-app"

        cred = MagicMock()
        cred.api_key = api_key

        service._app_repository.aget_all_non_deleted = AsyncMock(return_value=[_make_app("boundary-app")])
        service._tracking_repository.get_latest_before_by_key_hashes = AsyncMock(return_value={})
        service._tracking_repository.insert_key_entries = AsyncMock()
        service._tracking_repository.insert_budget_entries = AsyncMock()

        spending = _spending_payload(1.0, "2026-03-18T00:00:00+00:00")

        with (
            patch("codemie.service.spend_tracking.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.spend_tracking.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: spending if a else None,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            await service.collect(target_date=snapshot_at)

        service._tracking_repository.get_latest_before_by_key_hashes.assert_awaited_once_with(
            mock_session,
            [key_hash],
            snapshot_at,
        )

    @pytest.mark.asyncio
    async def test_collect_accepts_raw_litellm_key_info_response(
        self,
        mock_session,
        async_session_ctx,
    ):
        """Collector should support raw /key/info payloads with spend nested under info."""
        service = _make_service()
        api_key = "sk-raw-shape"

        setting = MagicMock()
        setting.id = "s1"
        setting.project_name = "raw-shape"

        cred = MagicMock()
        cred.api_key = api_key

        service._app_repository.aget_all_non_deleted = AsyncMock(return_value=[_make_app("raw-shape")])
        service._tracking_repository.get_latest_before_by_key_hashes = AsyncMock(return_value={})
        service._tracking_repository.insert_key_entries = AsyncMock()
        service._tracking_repository.insert_budget_entries = AsyncMock()

        key_info = _raw_litellm_key_info_payload(
            spend=0.0024948,
            budget_reset_at="2026-03-24T00:00:00+00:00",
            max_budget=1.0,
            budget_duration="24h",
        )

        with (
            patch("codemie.service.spend_tracking.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.spend_tracking.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: key_info if a else None,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=date(2026, 3, 23))

        assert count == 1
        rows = service._tracking_repository.insert_key_entries.call_args[0][1]
        assert rows[0].daily_spend == Decimal("0.0024948")
        assert rows[0].cumulative_spend == Decimal("0.0024948")
        assert rows[0].budget_period_spend == Decimal("0.0024948")

    @pytest.mark.asyncio
    async def test_collect_skips_invalid_snapshot_when_cumulative_would_decrease(
        self,
        mock_session,
        async_session_ctx,
    ):
        """Invalid snapshot calculations should be logged and skipped without breaking other processing."""
        service = _make_service()
        api_key = "sk-invalid-cumulative"
        key_hash = _sha256(api_key)

        setting = MagicMock()
        setting.id = "s1"
        setting.project_name = "epm-edec"

        cred = MagicMock()
        cred.api_key = api_key

        prev_row = _prev_row(
            key_hash,
            cumulative=Decimal("10.000000000"),
            budget_period_spend=Decimal("5.000000000"),
        )

        service._app_repository.aget_all_non_deleted = AsyncMock(return_value=[_make_app("epm-edec")])
        service._tracking_repository.get_latest_before_by_key_hashes = AsyncMock(return_value={key_hash: prev_row})
        service._tracking_repository.insert_key_entries = AsyncMock()
        service._tracking_repository.insert_budget_entries = AsyncMock()

        spending = _spending_payload(5.0)

        with (
            patch("codemie.service.spend_tracking.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.spend_tracking.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: spending if a else None,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
            patch.object(
                service,
                "_compute_spend_snapshot",
                side_effect=InvalidSpendSnapshotError("cumulative spend decreased: computed=9 < prev=10"),
            ),
            patch("codemie.service.spend_tracking.spend_collector_service.logger") as mock_logger,
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=date(2026, 3, 23))

        assert count == 0
        service._tracking_repository.insert_key_entries.assert_called_once_with(mock_session, [])
        mock_logger.warning.assert_any_call(
            "Skipping invalid spend snapshot for project 'epm-edec': cumulative spend decreased: computed=9 < prev=10"
        )

    @pytest.mark.asyncio
    async def test_collect_no_litellm_settings_returns_zero(
        self,
        mock_session,
        async_session_ctx,
    ):
        """No LiteLLM settings found for any project → returns 0 with no key rows."""
        service = _make_service()
        service._app_repository.aget_all_non_deleted = AsyncMock(
            return_value=[_make_app("foo-bar"), _make_app("bar-baz")]
        )
        service._tracking_repository.insert_key_entries = AsyncMock()
        service._tracking_repository.insert_budget_entries = AsyncMock()

        with (
            patch("codemie.service.spend_tracking.spend_collector_service.Settings") as mock_settings,
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: None,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = []
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 0
        service._tracking_repository.insert_key_entries.assert_not_called()

    @pytest.mark.asyncio
    async def test_collect_persists_cost_center_fields(
        self,
        mock_session,
        async_session_ctx,
    ):
        service = _make_service()
        api_key = "sk-cost-center-key"
        cost_center_id = uuid4()

        app = _make_app("foo-bar")
        app.cost_center_id = cost_center_id

        setting = MagicMock()
        setting.id = "s1"
        setting.project_name = "foo-bar"

        cred = MagicMock()
        cred.api_key = api_key

        service._app_repository.aget_all_non_deleted = AsyncMock(return_value=[app])
        service._tracking_repository.get_latest_before_by_key_hashes = AsyncMock(return_value={})
        service._tracking_repository.insert_key_entries = AsyncMock()
        service._tracking_repository.insert_budget_entries = AsyncMock()

        spending = [{"total_spend": 1.25}]

        with (
            patch("codemie.service.spend_tracking.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.spend_tracking.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: spending if a else None,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.cost_center_repository.aget_by_ids",
                new=AsyncMock(return_value={cost_center_id: SimpleNamespace(id=cost_center_id, name="epm-cdme")}),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            await service.collect(target_date=date(2026, 3, 17))

        inserted_rows = service._tracking_repository.insert_key_entries.call_args[0][1]
        assert inserted_rows[0].cost_center_id == cost_center_id
        assert inserted_rows[0].cost_center_name == "epm-cdme"


# ---------------------------------------------------------------------------
# TestCollectBudgetBased — /customer/list spend path
# ---------------------------------------------------------------------------


def _make_budget(
    budget_id: str,
    budget_category: str,
    budget_duration: str = "30d",
    budget_reset_at: str | None = None,
) -> object:
    """Build a minimal Budget-like object for tests (avoids DB-required fields)."""
    budget = SimpleNamespace(
        budget_id=budget_id,
        budget_category=budget_category,
        budget_duration=budget_duration,
        budget_reset_at=budget_reset_at,
        max_budget=10.0,
        soft_budget=0.0,
    )
    return budget


def _make_customer_entry(user_id: str, budget_id: str, spend: Decimal) -> object:
    return SimpleNamespace(user_id=user_id, budget_id=budget_id, spend=spend)


def _budget_only_service(
    get_latest_prev: dict | None = None,
) -> LiteLLMSpendCollectorService:
    """Service with mocked repositories preset for budget-path-only tests."""
    service = _make_service()
    service._app_repository.aget_all_non_deleted = AsyncMock(return_value=[])
    service._tracking_repository.get_latest_before_by_key_hashes = AsyncMock(return_value={})
    service._tracking_repository.get_latest_before_by_project_budget_ids = AsyncMock(return_value=get_latest_prev or {})
    service._tracking_repository.insert_key_entries = AsyncMock()
    service._tracking_repository.insert_budget_entries = AsyncMock()
    return service


class TestCollectBudgetBased:
    """Tests for _collect_budget_based: /customer/list scanning with DB budget meta."""

    @pytest.fixture(autouse=True)
    def mock_budget_repo(self):
        with patch("codemie.service.spend_tracking.spend_collector_service.budget_repository") as mock_repo:
            mock_repo.get_all_keyed_by_id = AsyncMock(return_value={})
            yield mock_repo

    @pytest.mark.asyncio
    async def test_first_run_bootstrap_seeds_daily_and_cumulative(
        self, mock_session, async_session_ctx, mock_budget_repo
    ):
        """First run (no prior row): current spend seeds both daily and cumulative.
        budget_category is taken from the DB Budget row, not derived.
        """
        service = _budget_only_service()
        mock_budget_repo.get_all_keyed_by_id = AsyncMock(return_value={"cli": _make_budget("cli", "cli")})

        customer_entries = [_make_customer_entry("alice@example.com_codemie_cli", "cli", Decimal("5.0"))]

        with (
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: None if a else customer_entries,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                side_effect=lambda: async_session_ctx(),
            ),
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
    async def test_delta_computed_against_prev_budget_row(self, mock_session, async_session_ctx, mock_budget_repo):
        """Normal delta: lifetime cumulative grows by the period-spend difference."""
        prev = _prev_row("unused", cumulative=Decimal("10.00"), budget_period_spend=Decimal("3.00"))
        service = _budget_only_service(get_latest_prev={("alice@example.com", "cli"): prev})
        mock_budget_repo.get_all_keyed_by_id = AsyncMock(return_value={"cli": _make_budget("cli", "cli")})

        customer_entries = [_make_customer_entry("alice@example.com_codemie_cli", "cli", Decimal("5.50"))]

        with (
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: None if a else customer_entries,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                side_effect=lambda: async_session_ctx(),
            ),
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 1
        row = service._tracking_repository.insert_budget_entries.call_args[0][1][0]
        assert row.daily_spend == Decimal("2.50")
        assert row.cumulative_spend == Decimal("12.50")
        assert row.budget_period_spend == Decimal("5.50")

    @pytest.mark.asyncio
    async def test_zero_delta_budget_row_not_persisted(self, mock_session, async_session_ctx, mock_budget_repo):
        """Unchanged spend (zero delta) produces no row."""
        prev = _prev_row("unused", cumulative=Decimal("10.00"), budget_period_spend=Decimal("3.00"))
        service = _budget_only_service(get_latest_prev={("alice@example.com", "cli"): prev})
        mock_budget_repo.get_all_keyed_by_id = AsyncMock(return_value={"cli": _make_budget("cli", "cli")})

        customer_entries = [_make_customer_entry("alice@example.com_codemie_cli", "cli", Decimal("3.00"))]

        with (
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: None if a else customer_entries,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                side_effect=lambda: async_session_ctx(),
            ),
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 0
        service._tracking_repository.insert_budget_entries.assert_called_once_with(mock_session, [])

    @pytest.mark.asyncio
    async def test_budget_category_derived_from_user_id_when_budget_not_in_db(self, mock_session, async_session_ctx):
        """When budget_id is absent from DB, budget_category falls back to derive_category_from_user_id."""
        service = _budget_only_service()

        customer_entries = [
            _make_customer_entry("bob@example.com_codemie_premium_models", "unknown_budget", Decimal("2.0"))
        ]

        with (
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: None if a else customer_entries,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                side_effect=lambda: async_session_ctx(),
            ),
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 1
        row = service._tracking_repository.insert_budget_entries.call_args[0][1][0]
        assert row.budget_category == "premium_models"
        assert row.project_name == "bob@example.com"

    @pytest.mark.asyncio
    async def test_reset_detected_via_db_budget_meta(self, mock_session, async_session_ctx, mock_budget_repo):
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

        customer_entries = [_make_customer_entry("alice@example.com_codemie_cli", "cli", Decimal("0.75"))]
        snapshot_at = datetime(2026, 3, 17, 0, 5, tzinfo=timezone.utc)

        with (
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: None if a else customer_entries,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                side_effect=lambda: async_session_ctx(),
            ),
        ):
            count = await service.collect(target_date=snapshot_at)

        assert count == 1
        row = service._tracking_repository.insert_budget_entries.call_args[0][1][0]
        # Reset detected: daily = current period spend (not diff from prev)
        assert row.daily_spend == Decimal("0.75")
        assert row.cumulative_spend == Decimal("10.75")

    @pytest.mark.asyncio
    async def test_multiple_customers_produce_independent_rows(self, mock_session, async_session_ctx, mock_budget_repo):
        """Multiple customer entries → independent budget rows with correct project names."""
        service = _budget_only_service()
        mock_budget_repo.get_all_keyed_by_id = AsyncMock(
            return_value={
                "cli": _make_budget("cli", "cli"),
                "pm": _make_budget("pm", "premium_models"),
            }
        )

        customer_entries = [
            _make_customer_entry("alice@example.com_codemie_cli", "cli", Decimal("3.0")),
            _make_customer_entry("bob@example.com_codemie_premium_models", "pm", Decimal("7.5")),
        ]

        with (
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: None if a else customer_entries,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                side_effect=lambda: async_session_ctx(),
            ),
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
    async def test_no_customer_entries_skips_budget_path(self, mock_session, async_session_ctx):
        """None from get_customer_list_spending → budget path skipped, 0 rows."""
        service = _budget_only_service()

        with (
            patch(
                "codemie.service.spend_tracking.spend_collector_service.asyncio.to_thread",
                side_effect=lambda fn, *a: None,
            ),
            patch(
                "codemie.service.spend_tracking.spend_collector_service.get_async_session",
                side_effect=lambda: async_session_ctx(),
            ),
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 0
        service._tracking_repository.insert_budget_entries.assert_not_called()


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

            scheduler = SpendTrackingScheduler(scheduler=mock_scheduler)
            scheduler.start()

        mock_scheduler.add_job.assert_not_called()
        mock_logger.error.assert_called_once()
