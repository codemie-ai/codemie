# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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
from unittest.mock import AsyncMock, Mock

import pytest

from codemie.service.conversation_analysis.scheduler import ConversationAnalysisScheduler


def _build_scheduler_instance(monkeypatch):
    mock_service = Mock()
    monkeypatch.setattr(
        "codemie.service.conversation_analysis.scheduler.ConversationAnalysisService",
        lambda: mock_service,
    )
    scheduler_backend = Mock()
    scheduler_backend.running = False
    return ConversationAnalysisScheduler(scheduler_backend), scheduler_backend, mock_service


def test_start_skips_setup_when_feature_disabled(monkeypatch):
    scheduler, backend, _service = _build_scheduler_instance(monkeypatch)
    start_background = Mock()
    monkeypatch.setattr("codemie.service.conversation_analysis.scheduler.config.CONVERSATION_ANALYSIS_ENABLED", False)
    monkeypatch.setattr(scheduler, "_start_background_processor", start_background)

    scheduler.start()

    backend.add_job.assert_not_called()
    backend.start.assert_not_called()
    start_background.assert_not_called()


def test_start_logs_and_returns_for_invalid_cron(monkeypatch):
    scheduler, backend, _service = _build_scheduler_instance(monkeypatch)
    start_background = Mock()
    logger = Mock()
    monkeypatch.setattr("codemie.service.conversation_analysis.scheduler.config.CONVERSATION_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(
        "codemie.service.conversation_analysis.scheduler.config.CONVERSATION_ANALYSIS_SCHEDULE", "* * *"
    )
    monkeypatch.setattr("codemie.service.conversation_analysis.scheduler.logger", logger)
    monkeypatch.setattr(scheduler, "_start_background_processor", start_background)

    scheduler.start()

    backend.add_job.assert_not_called()
    backend.start.assert_not_called()
    start_background.assert_not_called()
    logger.error.assert_called_once()


def test_start_registers_job_and_starts_background_processor(monkeypatch):
    scheduler, backend, _service = _build_scheduler_instance(monkeypatch)
    start_background = Mock()
    monkeypatch.setattr("codemie.service.conversation_analysis.scheduler.config.CONVERSATION_ANALYSIS_ENABLED", True)
    monkeypatch.setattr(
        "codemie.service.conversation_analysis.scheduler.config.CONVERSATION_ANALYSIS_SCHEDULE",
        "15 4 * * 1-5",
    )
    monkeypatch.setattr(
        "codemie.service.conversation_analysis.scheduler.config.CONVERSATION_ANALYSIS_START_DATE",
        "2026-01-01",
    )
    monkeypatch.setattr(scheduler, "_start_background_processor", start_background)

    scheduler.start()

    backend.add_job.assert_called_once()
    _, kwargs = backend.add_job.call_args
    assert kwargs["id"] == "conversation_analysis_job"
    assert kwargs["replace_existing"] is True
    assert kwargs["name"] == "Conversation Analysis - Queue Population"
    assert kwargs["trigger"].fields[0].expressions[0].step is None
    backend.start.assert_called_once()
    start_background.assert_called_once()


@pytest.mark.asyncio
async def test_run_analysis_job_uses_projects_filter(monkeypatch):
    scheduler, _backend, service = _build_scheduler_instance(monkeypatch)
    service.schedule_analysis_job = AsyncMock(return_value={"queued": 2})
    logger = Mock()
    monkeypatch.setattr(
        "codemie.service.conversation_analysis.scheduler.config.CONVERSATION_ANALYSIS_PROJECTS_FILTER",
        ["alpha", "beta"],
    )
    monkeypatch.setattr("codemie.service.conversation_analysis.scheduler.logger", logger)

    await scheduler._run_analysis_job()

    service.schedule_analysis_job.assert_awaited_once_with(projects=["alpha", "beta"])
    logger.info.assert_called_once()


@pytest.mark.asyncio
async def test_run_analysis_job_logs_failures(monkeypatch):
    scheduler, _backend, service = _build_scheduler_instance(monkeypatch)
    service.schedule_analysis_job = AsyncMock(side_effect=RuntimeError("boom"))
    logger = Mock()
    monkeypatch.setattr("codemie.service.conversation_analysis.scheduler.logger", logger)
    monkeypatch.setattr(
        "codemie.service.conversation_analysis.scheduler.config.CONVERSATION_ANALYSIS_PROJECTS_FILTER", []
    )

    await scheduler._run_analysis_job()

    logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_background_processor_waits_longer_when_no_work(monkeypatch):
    scheduler, _backend, service = _build_scheduler_instance(monkeypatch)
    service.process_batch = AsyncMock(return_value={"status": "no_work"})
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr("codemie.service.conversation_analysis.scheduler.asyncio.sleep", fake_sleep)

    scheduler._start_background_processor()

    with pytest.raises(asyncio.CancelledError):
        await scheduler.background_processor_task

    assert sleep_calls == [60]


@pytest.mark.asyncio
async def test_background_processor_retries_after_error(monkeypatch):
    scheduler, _backend, service = _build_scheduler_instance(monkeypatch)
    service.process_batch = AsyncMock(side_effect=RuntimeError("queue failure"))
    sleep_calls = []
    logger = Mock()

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr("codemie.service.conversation_analysis.scheduler.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("codemie.service.conversation_analysis.scheduler.logger", logger)

    scheduler._start_background_processor()

    with pytest.raises(asyncio.CancelledError):
        await scheduler.background_processor_task

    assert sleep_calls == [60]
    logger.error.assert_called_once()


def test_stop_shuts_down_scheduler_and_background_task(monkeypatch):
    scheduler, backend, _service = _build_scheduler_instance(monkeypatch)
    backend.running = True
    task = Mock()
    scheduler.background_processor_task = task
    logger = Mock()
    monkeypatch.setattr("codemie.service.conversation_analysis.scheduler.logger", logger)

    scheduler.stop()

    backend.shutdown.assert_called_once_with(wait=False)
    task.cancel.assert_called_once()
    assert logger.info.call_count == 2
