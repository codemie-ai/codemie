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
from datetime import date
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


def _prev_row(key_hash: str, cumulative: Decimal) -> ProjectCostTracking:
    return ProjectCostTracking(
        id=uuid4(),
        project_name="foo-bar",
        key_hash=key_hash,
        spend_date=date(2026, 3, 16),
        daily_spend=cumulative,
        cumulative_spend=cumulative,
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
    """Tests for _compute_delta: budget-reset-aware daily spend calculation."""

    def test_no_prior_row_returns_current_spend(self):
        """First-run bootstrap: when there is no prior row, delta equals current spend."""
        service = _make_service()
        current = Decimal("5.25")

        result = service._compute_delta(current, prev_row=None)

        assert result == current

    def test_normal_delta_current_greater_than_prev(self):
        """Normal case: current >= prev → delta = current - prev."""
        service = _make_service()
        prev = _prev_row("abc123", Decimal("3.00"))
        current = Decimal("5.50")

        result = service._compute_delta(current, prev_row=prev)

        assert result == Decimal("2.50")

    def test_normal_delta_current_equals_prev(self):
        """Edge case: current == prev → delta = 0 (no new spend today)."""
        service = _make_service()
        prev = _prev_row("abc123", Decimal("3.00"))
        current = Decimal("3.00")

        result = service._compute_delta(current, prev_row=prev)

        assert result == Decimal("0")

    def test_budget_reset_current_less_than_prev(self):
        """Budget reset: current < prev → daily_spend = current (not zero, not negative)."""
        service = _make_service()
        prev = _prev_row("abc123", Decimal("10.00"))
        current = Decimal("0.75")

        result = service._compute_delta(current, prev_row=prev)

        assert result == current

    def test_budget_reset_logs_warning(self):
        """Budget reset should emit a WARNING log."""
        service = _make_service()
        prev = _prev_row("abc123", Decimal("10.00"))
        current = Decimal("0.75")

        with patch("codemie.service.chargeback.spend_collector_service.logger") as mock_logger:
            service._compute_delta(current, prev_row=prev)

        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "reset" in warning_msg.lower() or "budget" in warning_msg.lower()


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
        service._tracking_repository.get_latest_by_key_hashes = AsyncMock(return_value={})
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
                    return [{"total_spend": 2.5}]
                return None

            mock_to_thread.side_effect = spending_side_effect

            count = await service.collect(target_date=date(2026, 3, 17))

        # Only good_key should produce a row
        assert count == 1
        inserted_rows = service._tracking_repository.insert_entries.call_args[0][1]
        assert len(inserted_rows) == 1
        assert inserted_rows[0].key_hash == good_hash
        assert inserted_rows[0].project_name == "foo-bar"

    @pytest.mark.asyncio
    async def test_collect_normal_delta(
        self,
        mock_session,
        async_session_ctx,
    ):
        """Normal delta: current > prev → daily_spend = current - prev."""
        service = _make_service()
        api_key = "sk-normal-key"
        key_hash = _sha256(api_key)

        prev_cumulative = Decimal("3.00")
        current_spend = 5.50

        setting = MagicMock()
        setting.id = "s1"
        setting.project_name = "foo-bar"

        cred = MagicMock()
        cred.api_key = api_key

        service._app_repository.aget_all_non_deleted = AsyncMock(return_value=[_make_app("foo-bar")])
        prev_row = _prev_row(key_hash, prev_cumulative)
        service._tracking_repository.get_latest_by_key_hashes = AsyncMock(return_value={key_hash: prev_row})
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
                return_value=[{"total_spend": current_spend}],
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
        assert rows[0].daily_spend == Decimal("5.50") - prev_cumulative
        assert rows[0].cumulative_spend == Decimal("5.50")

    @pytest.mark.asyncio
    async def test_collect_first_run_bootstrap(
        self,
        mock_session,
        async_session_ctx,
    ):
        """First-run bootstrap: no prior row → daily_spend equals current_spend."""
        service = _make_service()
        api_key = "sk-bootstrap-key"

        setting = MagicMock()
        setting.id = "s1"
        setting.project_name = "alpha-beta"

        cred = MagicMock()
        cred.api_key = api_key

        service._app_repository.aget_all_non_deleted = AsyncMock(return_value=[_make_app("alpha-beta")])
        # No prior rows
        service._tracking_repository.get_latest_by_key_hashes = AsyncMock(return_value={})
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
                return_value=[{"total_spend": 7.0}],
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
