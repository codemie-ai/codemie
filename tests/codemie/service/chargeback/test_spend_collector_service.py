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
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from codemie.repository.application_repository import ApplicationRepository
from codemie.repository.project_cost_tracking_repository import ProjectCostTrackingRepository
from codemie.service.chargeback.spend_collector_service import LiteLLMSpendCollectorService
from codemie.service.chargeback.spend_models import ProjectCostTracking


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service() -> LiteLLMSpendCollectorService:
    return LiteLLMSpendCollectorService(
        app_repository=MagicMock(spec=ApplicationRepository),
        tracking_repository=MagicMock(spec=ProjectCostTrackingRepository),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _prev_row(
    key_hash: str,
    cumulative: Decimal,
    budget_period_spend: Decimal | None = None,
    budget_reset_at: datetime | None = None,
    spend_date: datetime | None = None,
) -> ProjectCostTracking:
    return ProjectCostTracking(
        id=uuid4(),
        project_name="foo-bar",
        key_hash=key_hash,
        spend_date=spend_date or datetime(2026, 3, 16, 0, 0, tzinfo=timezone.utc),
        daily_spend=budget_period_spend if budget_period_spend is not None else cumulative,
        cumulative_spend=cumulative,
        budget_period_spend=budget_period_spend if budget_period_spend is not None else cumulative,
        budget_reset_at=budget_reset_at,
    )


# ---------------------------------------------------------------------------
# TestFilterProjectNames — pure logic, no I/O
# ---------------------------------------------------------------------------


class TestFilterProjectNames:
    """Tests for _filter_project_names: configurable include/exclude logic."""

    def _filter(
        self,
        names: list[str],
        include_pattern: str = "",
        exclude_pattern: str = "",
        exclude_list: list[str] | None = None,
    ) -> list[str]:
        with patch("codemie.service.chargeback.spend_collector_service.config") as mock_cfg:
            mock_cfg.LITELLM_SPEND_COLLECTOR_PROJECT_INCLUDE_PATTERN = include_pattern
            mock_cfg.LITELLM_SPEND_COLLECTOR_PROJECT_EXCLUDE_PATTERN = exclude_pattern
            mock_cfg.LITELLM_SPEND_COLLECTOR_PROJECT_EXCLUDE_LIST = exclude_list or []
            return LiteLLMSpendCollectorService._filter_project_names(names)

    def test_empty_include_pattern_passes_all(self):
        """Empty include pattern → no filtering; all names are kept."""
        names = ["foo-bar", "UPPERCASE", "has-123", "any"]
        result = self._filter(names, include_pattern="")
        assert result == names

    def test_include_pattern_keeps_only_matching(self):
        """Only names matching the include regex are kept."""
        names = ["foo-bar", "UPPERCASE", "has-123", "alpha-beta"]
        result = self._filter(names, include_pattern=r"^[a-z]+-[a-z]+$")
        assert result == ["foo-bar", "alpha-beta"]

    def test_exclude_pattern_removes_matching(self):
        """Names matching the exclude regex are removed."""
        names = ["foo-bar", "foo-internal", "bar-baz"]
        result = self._filter(names, exclude_pattern=r"^foo-.*$")
        assert result == ["bar-baz"]

    def test_exclude_list_removes_exact_names(self):
        """Names in the exclude list are removed regardless of patterns."""
        names = ["foo-bar", "baz-qux", "skip-me"]
        result = self._filter(names, exclude_list=["skip-me", "baz-qux"])
        assert result == ["foo-bar"]

    def test_include_and_exclude_pattern_combined(self):
        """Include pattern applied first, then exclude pattern on the result."""
        names = ["foo-bar", "foo-internal", "bar-baz", "UPPERCASE"]
        result = self._filter(
            names,
            include_pattern=r"^[a-z]+-[a-z]+$",
            exclude_pattern=r"^foo-.*$",
        )
        assert result == ["bar-baz"]

    def test_exclude_list_applied_after_include_pattern(self):
        """Include pattern keeps candidates; exclude list then removes specific names."""
        names = ["foo-bar", "bar-baz", "skip-me", "UPPERCASE"]
        result = self._filter(
            names,
            include_pattern=r"^[a-z]+-[a-z]+$",
            exclude_list=["skip-me"],
        )
        assert result == ["foo-bar", "bar-baz"]

    def test_all_filters_combined(self):
        """Include pattern + exclude pattern + exclude list all applied together."""
        names = ["foo-bar", "foo-internal", "bar-baz", "bar-skip", "UPPERCASE"]
        result = self._filter(
            names,
            include_pattern=r"^[a-z]+-[a-z]+$",
            exclude_pattern=r"^foo-.*$",
            exclude_list=["bar-skip"],
        )
        assert result == ["bar-baz"]

    def test_empty_names_list(self):
        """Empty input → empty output regardless of filters."""
        result = self._filter([], include_pattern=r"^[a-z]+-[a-z]+$", exclude_list=["x"])
        assert result == []

    def test_default_include_pattern_matches_two_word_lowercase(self):
        """Default ^[a-z]+-[a-z]+$ matches exactly two lowercase words separated by a hyphen."""
        names = ["foo-bar", "alpha-beta", "has-123", "three-word-app", "UPPER", "foo-bar-baz"]
        result = self._filter(names, include_pattern=r"^[a-z]+-[a-z]+$")
        assert result == ["foo-bar", "alpha-beta"]


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
            current_budget_reset_at=datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc),
            prev_row=None,
            snapshot_at=datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc),
        )

        assert result == (current, current)

    def test_no_prior_row_with_zero_spend_returns_zeroes(self):
        """Bootstrap with zero spend should keep both daily and cumulative values at zero."""
        service = _make_service()

        result = service._compute_spend_snapshot(
            current_budget_period_spend=Decimal("0"),
            current_budget_reset_at=datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc),
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
            current_budget_reset_at=datetime(2026, 3, 18, 0, 0, tzinfo=timezone.utc),
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
            current_budget_reset_at=datetime(2026, 3, 18, 0, 0, tzinfo=timezone.utc),
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
            current_budget_reset_at=datetime(2026, 3, 18, 0, 0, tzinfo=timezone.utc),
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
            current_budget_reset_at=datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc),
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
            current_budget_reset_at=None,
            prev_row=prev,
            snapshot_at=datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc),
        )

        assert result == (Decimal("1.25"), Decimal("9.25"))

    def test_budget_reset_fallback_logs_warning_when_metadata_missing(self):
        """Decreasing budget-period spend without reset metadata falls back to reset semantics."""
        service = _make_service()
        prev = _prev_row("abc123", cumulative=Decimal("10.00"), budget_period_spend=Decimal("9.00"))
        current = Decimal("0.75")

        with patch("codemie.service.chargeback.spend_collector_service.logger") as mock_logger:
            result = service._compute_spend_snapshot(
                current_budget_period_spend=current,
                current_budget_reset_at=None,
                prev_row=prev,
                snapshot_at=datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc),
            )

        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "reset" in warning_msg.lower() or "budget" in warning_msg.lower()
        assert result == (current, Decimal("10.75"))

    def test_extractors_support_raw_litellm_key_info_payload(self):
        """Raw /key/info responses with nested info fields should be parsed correctly."""
        payload = _raw_litellm_key_info_payload(
            spend=0.0024948,
            budget_reset_at="2026-03-24T00:00:00+00:00",
            max_budget=1.0,
            budget_duration="24h",
        )[0]

        assert LiteLLMSpendCollectorService._extract_budget_period_spend(payload) == Decimal("0.0024948")
        assert LiteLLMSpendCollectorService._extract_budget_reset_at(payload) == datetime(
            2026,
            3,
            24,
            0,
            0,
            tzinfo=timezone.utc,
        )


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
        service._tracking_repository.insert_entries = AsyncMock()

        # _build_credential_result returns good_cred for good_setting, bad_cred for bad_setting
        def build_cred(setting, fields, cred_class):
            if setting is good_setting:
                return good_cred
            return bad_cred

        with (
            patch("codemie.service.chargeback.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.chargeback.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.chargeback.spend_collector_service.SettingsService._build_credential_result",
                side_effect=build_cred,
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.asyncio.to_thread",
            ) as mock_to_thread,
            patch(
                "codemie.service.chargeback.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [good_setting, bad_setting]

            # good_key returns valid data; bad_key returns None (API error / 404)
            async def spending_side_effect(fn, keys):
                assert len(keys) == 1
                if keys[0] == good_key:
                    return _spending_payload(2.5)
                return None

            mock_to_thread.side_effect = spending_side_effect

            count = await service.collect(target_date=date(2026, 3, 17))

        # Only good_key should produce a row
        assert count == 1
        inserted_rows = service._tracking_repository.insert_entries.call_args[0][1]
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
        service._tracking_repository.insert_entries = AsyncMock()

        with (
            patch("codemie.service.chargeback.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.chargeback.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.chargeback.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.asyncio.to_thread",
                return_value=_spending_payload(current_spend, "2026-03-18T00:00:00+00:00"),
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 1
        rows = service._tracking_repository.insert_entries.call_args[0][1]
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
        service._tracking_repository.insert_entries = AsyncMock()

        with (
            patch("codemie.service.chargeback.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.chargeback.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.chargeback.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.asyncio.to_thread",
                return_value=_spending_payload(7.0, "2026-03-18T00:00:00+00:00"),
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 1
        rows = service._tracking_repository.insert_entries.call_args[0][1]
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
        service._tracking_repository.insert_entries = AsyncMock()

        with (
            patch("codemie.service.chargeback.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.chargeback.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.chargeback.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.asyncio.to_thread",
                return_value=_spending_payload(3.0, "2026-03-18T00:00:00+00:00"),
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 0
        service._tracking_repository.insert_entries.assert_called_once_with(mock_session, [])

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
        service._tracking_repository.insert_entries = AsyncMock()

        with (
            patch("codemie.service.chargeback.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.chargeback.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.chargeback.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.asyncio.to_thread",
                return_value=_spending_payload(0.0, "2026-03-18T00:00:00+00:00"),
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 0
        service._tracking_repository.insert_entries.assert_called_once_with(mock_session, [])

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
        service._tracking_repository.insert_entries = AsyncMock()

        with (
            patch("codemie.service.chargeback.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.chargeback.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.chargeback.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.asyncio.to_thread",
                return_value=_spending_payload(0.75, "2026-03-18T00:00:00+00:00"),
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=datetime(2026, 3, 17, 0, 5, tzinfo=timezone.utc))

        assert count == 1
        rows = service._tracking_repository.insert_entries.call_args[0][1]
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
        service._tracking_repository.insert_entries = AsyncMock()

        with (
            patch("codemie.service.chargeback.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.chargeback.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.chargeback.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.asyncio.to_thread",
                return_value=_spending_payload(1.0, "2026-03-18T00:00:00+00:00"),
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.get_async_session",
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
        service._tracking_repository.insert_entries = AsyncMock()

        with (
            patch("codemie.service.chargeback.spend_collector_service.Settings") as mock_settings,
            patch("codemie.service.chargeback.spend_collector_service.SettingsService._decrypt_credentials"),
            patch(
                "codemie.service.chargeback.spend_collector_service.SettingsService._build_credential_result",
                return_value=cred,
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.asyncio.to_thread",
                return_value=_raw_litellm_key_info_payload(
                    spend=0.0024948,
                    budget_reset_at="2026-03-24T00:00:00+00:00",
                    max_budget=1.0,
                    budget_duration="24h",
                ),
            ),
            patch(
                "codemie.service.chargeback.spend_collector_service.get_async_session",
                return_value=async_session_ctx(),
            ),
        ):
            mock_settings.get_by_project_names.return_value = [setting]
            count = await service.collect(target_date=date(2026, 3, 23))

        assert count == 1
        rows = service._tracking_repository.insert_entries.call_args[0][1]
        assert rows[0].daily_spend == Decimal("0.0024948")
        assert rows[0].cumulative_spend == Decimal("0.0024948")
        assert rows[0].budget_period_spend == Decimal("0.0024948")
        assert rows[0].budget_reset_at == datetime(2026, 3, 24, 0, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_collect_no_matching_projects(
        self,
        mock_session,
        async_session_ctx,
    ):
        """No projects matching naming pattern → returns 0 with no DB writes."""
        service = _make_service()
        service._app_repository.aget_all_non_deleted = AsyncMock(
            return_value=[_make_app("UPPERCASE"), _make_app("has-123"), _make_app("three-word-app")]
        )
        service._tracking_repository.insert_entries = AsyncMock()

        with patch(
            "codemie.service.chargeback.spend_collector_service.get_async_session",
            return_value=async_session_ctx(),
        ):
            count = await service.collect(target_date=date(2026, 3, 17))

        assert count == 0
        service._tracking_repository.insert_entries.assert_not_called()


# ---------------------------------------------------------------------------
# TestSchedulerJobRegistration
# ---------------------------------------------------------------------------


class TestSchedulerJobRegistration:
    """Tests for ChargebackScheduler job registration."""

    def test_spend_collector_disabled_skips_job_registration(self):
        """LITELLM_SPEND_COLLECTOR_ENABLED=False → spend collector job is not added."""
        from codemie.service.chargeback.scheduler import ChargebackScheduler

        mock_scheduler = MagicMock()
        mock_scheduler.running = False

        with patch("codemie.service.chargeback.scheduler.config") as mock_config:
            mock_config.LITELLM_SPEND_COLLECTOR_ENABLED = False
            mock_config.LITELLM_SPEND_COLLECTOR_SCHEDULE = "30 0 * * *"

            scheduler = ChargebackScheduler(scheduler=mock_scheduler)
            scheduler.start()

        mock_scheduler.add_job.assert_not_called()

    def test_spend_collector_enabled_registers_job(self):
        """LITELLM_SPEND_COLLECTOR_ENABLED=True → spend collector job is registered."""
        from codemie.service.chargeback.scheduler import ChargebackScheduler

        mock_scheduler = MagicMock()
        mock_scheduler.running = False

        with (
            patch("codemie.service.chargeback.scheduler.config") as mock_config,
            patch("codemie.service.chargeback.scheduler.ApplicationRepository"),
            patch("codemie.service.chargeback.scheduler.ProjectCostTrackingRepository"),
            patch("codemie.service.chargeback.scheduler.LiteLLMSpendCollectorService"),
        ):
            mock_config.LITELLM_SPEND_COLLECTOR_ENABLED = True
            mock_config.LITELLM_SPEND_COLLECTOR_SCHEDULE = "30 0 * * *"

            scheduler = ChargebackScheduler(scheduler=mock_scheduler)
            scheduler.start()

        mock_scheduler.add_job.assert_called_once()
        job_kwargs = mock_scheduler.add_job.call_args[1]
        assert job_kwargs["id"] == "litellm_spend_collector"
        assert job_kwargs["replace_existing"] is True

    def test_invalid_cron_expression_skips_registration(self):
        """Invalid LITELLM_SPEND_COLLECTOR_SCHEDULE → job is not registered, error is logged."""
        from codemie.service.chargeback.scheduler import ChargebackScheduler

        mock_scheduler = MagicMock()
        mock_scheduler.running = False

        with (
            patch("codemie.service.chargeback.scheduler.config") as mock_config,
            patch("codemie.service.chargeback.scheduler.logger") as mock_logger,
            patch("codemie.service.chargeback.scheduler.ApplicationRepository"),
            patch("codemie.service.chargeback.scheduler.ProjectCostTrackingRepository"),
            patch("codemie.service.chargeback.scheduler.LiteLLMSpendCollectorService"),
        ):
            mock_config.LITELLM_SPEND_COLLECTOR_ENABLED = True
            mock_config.LITELLM_SPEND_COLLECTOR_SCHEDULE = "not a valid cron"

            scheduler = ChargebackScheduler(scheduler=mock_scheduler)
            scheduler.start()

        mock_scheduler.add_job.assert_not_called()
        mock_logger.error.assert_called_once()
