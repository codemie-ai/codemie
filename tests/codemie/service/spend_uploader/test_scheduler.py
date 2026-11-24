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

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.spend_uploader import scheduler as scheduler_module
from codemie.service.spend_uploader.scheduler import SpendUploaderScheduler, _CompletedFeed

UNTIL_NAIVE = datetime(2026, 11, 20, 12, 0)
END_MARGIN = timedelta(seconds=180)


def _summary() -> MagicMock:
    summary = MagicMock()
    summary.rows_read = 3
    summary.rows_changed = 2
    summary.pages = 1
    summary.elapsed_s = 0.5
    summary.until = UNTIL_NAIVE
    return summary


@pytest.fixture
def apscheduler() -> MagicMock:
    return MagicMock()


@pytest.fixture
def uploader(apscheduler) -> SpendUploaderScheduler:
    return SpendUploaderScheduler(apscheduler)


@pytest.fixture
def backfill_config():
    with patch.object(scheduler_module, "config") as cfg:
        cfg.SPEND_BACKFILL_ENABLED = True
        yield cfg


class TestBuildAndRunSpendUploader:
    def test_returns_summary_and_end_margin(self):
        summary = _summary()
        service = MagicMock()
        service.run_feed.return_value = summary
        service.config.end_margin = END_MARGIN

        with patch.object(scheduler_module, "build_spend_uploader_service", return_value=service):
            result = SpendUploaderScheduler._build_and_run_spend_uploader()

        assert result == _CompletedFeed(summary, END_MARGIN)

    def test_returns_none_when_feed_has_nothing_to_do(self):
        service = MagicMock()
        service.run_feed.return_value = None

        with patch.object(scheduler_module, "build_spend_uploader_service", return_value=service):
            assert SpendUploaderScheduler._build_and_run_spend_uploader() is None

    def test_returns_none_when_service_unavailable(self):
        with patch.object(scheduler_module, "build_spend_uploader_service", return_value=None):
            assert SpendUploaderScheduler._build_and_run_spend_uploader() is None


@pytest.mark.asyncio
class TestRunSpendUploaderBackfillTrigger:
    async def test_schedules_one_shot_backfill_with_utc_feed_end_and_end_margin(
        self, uploader, apscheduler, backfill_config
    ):
        with patch.object(
            SpendUploaderScheduler, "_build_and_run_spend_uploader", return_value=_CompletedFeed(_summary(), END_MARGIN)
        ):
            await uploader._run_spend_uploader()

        apscheduler.add_job.assert_called_once_with(
            uploader._run_spend_backfill,
            trigger="date",
            args=[UNTIL_NAIVE.replace(tzinfo=UTC), END_MARGIN],
            id="spend_backfill",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
        scheduled_feed_until, _ = apscheduler.add_job.call_args.kwargs["args"]
        assert scheduled_feed_until.utcoffset() == timedelta(0)

    async def test_schedules_nothing_when_feed_has_nothing_to_do(self, uploader, apscheduler, backfill_config):
        with patch.object(SpendUploaderScheduler, "_build_and_run_spend_uploader", return_value=None):
            await uploader._run_spend_uploader()

        apscheduler.add_job.assert_not_called()

    async def test_schedules_nothing_when_backfill_disabled(self, uploader, apscheduler, backfill_config):
        backfill_config.SPEND_BACKFILL_ENABLED = False

        with patch.object(
            SpendUploaderScheduler, "_build_and_run_spend_uploader", return_value=_CompletedFeed(_summary(), END_MARGIN)
        ):
            await uploader._run_spend_uploader()

        apscheduler.add_job.assert_not_called()

    async def test_schedules_nothing_when_feed_fails(self, uploader, apscheduler, backfill_config):
        with patch.object(SpendUploaderScheduler, "_build_and_run_spend_uploader", side_effect=RuntimeError("boom")):
            await uploader._run_spend_uploader()

        apscheduler.add_job.assert_not_called()

    async def test_returns_without_awaiting_backfill(self, uploader, apscheduler, backfill_config):
        backfill = AsyncMock()
        with (
            patch.object(uploader._spend_backfill_service, "backfill", backfill),
            patch.object(
                SpendUploaderScheduler,
                "_build_and_run_spend_uploader",
                return_value=_CompletedFeed(_summary(), END_MARGIN),
            ),
        ):
            await asyncio.wait_for(uploader._run_spend_uploader(), timeout=1)

        backfill.assert_not_awaited()
        backfill.assert_not_called()


@pytest.mark.asyncio
class TestRunSpendBackfill:
    async def test_runs_the_backfill_with_the_feed_end_and_end_margin(self, uploader):
        feed_until = UNTIL_NAIVE.replace(tzinfo=UTC)
        with patch.object(uploader._spend_backfill_service, "backfill", AsyncMock()) as backfill:
            await uploader._run_spend_backfill(feed_until, END_MARGIN)

        backfill.assert_awaited_once_with(feed_until, END_MARGIN)

    async def test_logs_and_swallows_any_failure(self, uploader):
        with (
            patch.object(uploader._spend_backfill_service, "backfill", AsyncMock(side_effect=RuntimeError("boom"))),
            patch.object(scheduler_module, "logger") as logger,
        ):
            await uploader._run_spend_backfill(UNTIL_NAIVE.replace(tzinfo=UTC), END_MARGIN)

        [error] = logger.error.call_args_list
        assert error.args[0] == "Spend backfill failed: RuntimeError"
        assert error.kwargs["exc_info"] is True

    async def test_reuses_one_service_across_runs(self, apscheduler):
        with patch.object(scheduler_module, "SpendBackfillService") as service_class:
            service_class.return_value.backfill = AsyncMock()
            uploader = SpendUploaderScheduler(apscheduler)

            await uploader._run_spend_backfill(UNTIL_NAIVE.replace(tzinfo=UTC), END_MARGIN)
            await uploader._run_spend_backfill(UNTIL_NAIVE.replace(tzinfo=UTC), END_MARGIN)

        service_class.assert_called_once_with()
        assert service_class.return_value.backfill.await_count == 2


class TestRegisterSpendUploaderJob:
    @pytest.fixture
    def registrable(self):
        with (
            patch.object(scheduler_module, "is_spend_uploader_available", return_value=True),
            patch.object(scheduler_module, "is_litellm_db_configured", return_value=True),
            patch.object(scheduler_module, "verify_litellm_db_connection"),
            patch.object(scheduler_module, "_build_cron_trigger", return_value=MagicMock()),
            patch.object(scheduler_module, "logger") as logger,
        ):
            yield logger

    def test_logs_the_backfill_settings_once_when_the_backfill_is_enabled(
        self, uploader, registrable, backfill_config, monkeypatch
    ):
        from codemie.configs.config import config

        monkeypatch.setattr(config, "SPEND_BACKFILL_DRY_RUN", True)
        monkeypatch.setattr(config, "SPEND_BACKFILL_MAX_AGE_HOURS", 48)
        monkeypatch.setattr(config, "SPEND_BACKFILL_BATCH", 1000)
        monkeypatch.setattr(config, "SPEND_BACKFILL_TIMEOUT_SECONDS", 60)
        monkeypatch.setattr(config, "SPEND_BACKFILL_HISTORY_START", None)

        uploader._register_spend_uploader_job()

        [settings_line] = [
            call.args[0] for call in registrable.info.call_args_list if call.args[0].startswith("Spend backfill")
        ]
        assert settings_line == (
            "Spend backfill runs after each spend uploader feed: dry_run=true, recent_window=48h, "
            "history_start=off, timeout=60s, batch_size=1000"
        )

    def test_logs_no_backfill_settings_when_the_backfill_is_disabled(self, uploader, registrable, backfill_config):
        backfill_config.SPEND_BACKFILL_ENABLED = False

        uploader._register_spend_uploader_job()

        assert not [call for call in registrable.info.call_args_list if call.args[0].startswith("Spend backfill")]
