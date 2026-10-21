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


"""LiteLLM model load detection and background recovery (EPMCDME-12423)."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI

from codemie.configs.llm_config import LiteLLMModels, LLMModel
from codemie.rest_api import litellm_models_recovery as recovery
from codemie.rest_api import main

GET_MODELS = "codemie.enterprise.litellm.get_available_models"
PROVIDER = "codemie.service.llm_proxy.provider_registry.get_active_llm_proxy_provider"
LLM_SERVICE = "codemie.service.llm_service.llm_service.llm_service"


def _models(chat_count: int = 1) -> LiteLLMModels:
    return LiteLLMModels(
        chat_models=[LLMModel(base_name=f"m{i}", deployment_name=f"m{i}", enabled=True) for i in range(chat_count)],
        embedding_models=[],
    )


class TestInitializeLiteLLMModels:
    def test_returns_false_and_logs_error_when_fetch_fails(self):
        with (
            patch(GET_MODELS, side_effect=ConnectionError("down")),
            patch(PROVIDER),
            patch(LLM_SERVICE) as svc,
            patch.object(recovery.logger, "error") as log_error,
        ):
            assert recovery.initialize_litellm_models() is False

        svc.initialize_default_litellm_models.assert_not_called()
        assert "NOT active" in log_error.call_args.args[0]

    def test_returns_false_when_no_chat_models(self):
        with (
            patch(GET_MODELS, return_value=_models(0)),
            patch(PROVIDER),
            patch(LLM_SERVICE) as svc,
            patch.object(recovery.logger, "error") as log_error,
        ):
            assert recovery.initialize_litellm_models() is False

        svc.initialize_default_litellm_models.assert_not_called()
        assert log_error.called

    def test_returns_true_and_initializes_when_models_available(self):
        models = _models(2)
        with patch(GET_MODELS, return_value=models), patch(PROVIDER), patch(LLM_SERVICE) as svc:
            assert recovery.initialize_litellm_models() is True

        svc.initialize_default_litellm_models.assert_called_once_with(models)


class TestSetupLiteLLMFeatures:
    def test_status_unavailable_when_initialization_fails(self):
        app = FastAPI()
        with (
            patch.object(main, "is_litellm_enabled", return_value=True),
            patch.object(main, "_initialize_litellm_models", return_value=False),
            patch.object(main.config, "LLM_PROXY_BUDGET_CHECK_ENABLED", False),
        ):
            main._setup_litellm_features(app)

        assert app.state.litellm_models_status == "unavailable"

    def test_status_loaded_when_initialization_succeeds(self):
        app = FastAPI()
        with (
            patch.object(main, "is_litellm_enabled", return_value=True),
            patch.object(main, "_initialize_litellm_models", return_value=True),
            patch.object(main.config, "LLM_PROXY_BUDGET_CHECK_ENABLED", False),
        ):
            main._setup_litellm_features(app)

        assert app.state.litellm_models_status == "loaded"

    def test_status_not_applicable_when_litellm_disabled(self):
        app = FastAPI()
        with patch.object(main, "is_litellm_enabled", return_value=False):
            main._setup_litellm_features(app)

        assert app.state.litellm_models_status == "not_applicable"


@pytest.mark.asyncio
class TestScheduleRecovery:
    async def test_schedules_task_only_when_unavailable(self):
        app = FastAPI()
        app.state.litellm_models_status = "unavailable"
        tasks: list[asyncio.Task] = []
        with patch.object(recovery, "is_litellm_enabled", return_value=True):
            recovery.schedule_litellm_models_recovery(app, tasks)
        try:
            assert [t.get_name() for t in tasks] == ["litellm_models_recovery"]
        finally:
            for t in tasks:
                t.cancel()

    @pytest.mark.parametrize("status", ["loaded", "not_applicable"])
    async def test_no_task_when_not_unavailable(self, status):
        app = FastAPI()
        app.state.litellm_models_status = status
        tasks: list[asyncio.Task] = []
        with patch.object(recovery, "is_litellm_enabled", return_value=True):
            recovery.schedule_litellm_models_recovery(app, tasks)
        assert tasks == []

    async def test_no_task_when_litellm_disabled(self):
        app = FastAPI()
        app.state.litellm_models_status = "unavailable"
        tasks: list[asyncio.Task] = []
        with patch.object(recovery, "is_litellm_enabled", return_value=False):
            recovery.schedule_litellm_models_recovery(app, tasks)
        assert tasks == []

    async def test_recovery_retries_with_backoff_then_loads_models(self):
        app = FastAPI()
        app.state.litellm_models_status = "unavailable"
        models = _models(1)
        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        fetch = MagicMock(side_effect=[ConnectionError("down"), ConnectionError("down"), models])
        with (
            patch.object(recovery, "is_litellm_enabled", return_value=True),
            patch.object(recovery.config, "LITELLM_MODELS_RETRY_INTERVAL_SECONDS", 5),
            patch.object(recovery.asyncio, "sleep", fake_sleep),
            patch(GET_MODELS, fetch),
            patch(PROVIDER),
            patch(LLM_SERVICE) as svc,
        ):
            tasks: list[asyncio.Task] = []
            recovery.schedule_litellm_models_recovery(app, tasks)
            await asyncio.wait_for(tasks[0], timeout=5)

        assert sleeps == [5, 10, 20]
        svc.initialize_default_litellm_models.assert_called_once_with(models)
        assert app.state.litellm_models_status == "loaded"

    async def test_backoff_is_capped_at_ten_times_interval(self):
        app = FastAPI()
        app.state.litellm_models_status = "unavailable"
        sleeps: list[float] = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        side_effect = [ConnectionError("down")] * 6 + [_models(1)]
        with (
            patch.object(recovery, "is_litellm_enabled", return_value=True),
            patch.object(recovery.config, "LITELLM_MODELS_RETRY_INTERVAL_SECONDS", 5),
            patch.object(recovery.asyncio, "sleep", fake_sleep),
            patch(GET_MODELS, MagicMock(side_effect=side_effect)),
            patch(PROVIDER),
            patch(LLM_SERVICE),
        ):
            tasks: list[asyncio.Task] = []
            recovery.schedule_litellm_models_recovery(app, tasks)
            await asyncio.wait_for(tasks[0], timeout=5)

        assert max(sleeps) == 50
