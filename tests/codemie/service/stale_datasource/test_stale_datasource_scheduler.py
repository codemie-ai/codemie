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

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.stale_datasource.scheduler import StaleDatasourceScheduler

_MODULE = "codemie.service.stale_datasource.scheduler"


@asynccontextmanager
async def _acquired_lock(_lock_id):
    yield True


@asynccontextmanager
async def _session_cm():
    yield MagicMock()


def _stats(**overrides) -> dict:
    stats = {
        "total_evaluated": 4,
        "newly_marked_stale": 1,
        "errors": 0,
        "stale_rows_swept": 5,
        "indexes_deleted": 2,
        "datasources_archived": 3,
        "skipped_shared_index": 1,
        "deletion_errors": 0,
        "deletion_capped": False,
        "reactivated": 7,
        "recheck_errors": 2,
    }
    stats.update(overrides)
    return stats


async def _run_and_capture_summary(stats: dict) -> str:
    scheduler = StaleDatasourceScheduler(MagicMock())
    service = MagicMock()
    service.detect_and_mark_stale_datasources = AsyncMock(return_value=stats)

    with (
        patch(f"{_MODULE}.async_leader_lock", _acquired_lock),
        patch(f"{_MODULE}.get_async_session", _session_cm),
        patch(f"{_MODULE}.MetricsElasticRepository"),
        patch(f"{_MODULE}.StaleDatasourceService", return_value=service),
        patch(f"{_MODULE}.logger") as mock_logger,
    ):
        await scheduler._run_stale_detection()

    mock_logger.error.assert_not_called()
    return mock_logger.info.call_args.args[0]


class TestRunStaleDetectionSummary:
    @pytest.mark.asyncio
    async def test_summary_logs_reactivated_and_recheck_errors(self):
        message = await _run_and_capture_summary(_stats())

        assert "reactivated=7" in message
        assert "recheck_errors=2" in message
        assert "deleted=2" in message

    @pytest.mark.asyncio
    async def test_summary_with_early_return_stats_does_not_raise(self):
        message = await _run_and_capture_summary(
            _stats(
                stale_rows_swept=0,
                indexes_deleted=0,
                datasources_archived=0,
                skipped_shared_index=0,
                reactivated=0,
                recheck_errors=0,
            )
        )

        assert "reactivated=0" in message
        assert "recheck_errors=0" in message
