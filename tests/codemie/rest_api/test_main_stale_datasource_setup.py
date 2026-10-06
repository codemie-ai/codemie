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

from unittest.mock import MagicMock, patch


def test_setup_stale_datasource_scheduler_skips_when_disabled():
    """T5: _setup_stale_datasource_scheduler must not register when STALE_DATASOURCE_ENABLED=False."""
    from codemie.rest_api.main import _setup_stale_datasource_scheduler

    class _State:
        pass

    class _App:
        state = _State()

    app = _App()

    with patch("codemie.rest_api.main.config") as mock_config:
        mock_config.STALE_DATASOURCE_ENABLED = False
        _setup_stale_datasource_scheduler(app)

    assert not hasattr(app.state, "stale_datasource_scheduler")


def test_setup_stale_datasource_scheduler_registers_when_enabled():
    """T5: _setup_stale_datasource_scheduler registers and starts when STALE_DATASOURCE_ENABLED=True."""
    from codemie.rest_api.main import _setup_stale_datasource_scheduler

    app = MagicMock()

    with (
        patch("codemie.rest_api.main.config") as mock_config,
        patch("apscheduler.schedulers.asyncio.AsyncIOScheduler", return_value=MagicMock()),
        patch("codemie.service.stale_datasource.scheduler.StaleDatasourceScheduler") as mock_scheduler_cls,
    ):
        mock_config.STALE_DATASOURCE_ENABLED = True
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler

        _setup_stale_datasource_scheduler(app)

    mock_scheduler.start.assert_called_once()
    assert app.state.stale_datasource_scheduler is mock_scheduler
