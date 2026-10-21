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

"""Startup load of LiteLLM models and background recovery when LiteLLM is unavailable (EPMCDME-12423)."""

import asyncio

from fastapi import FastAPI

from codemie.configs import config
from codemie.configs.llm_config import LiteLLMModels
from codemie.configs.logger import logger
from codemie.enterprise.litellm import is_litellm_enabled

LITELLM_MODELS_LOADED = "loaded"
LITELLM_MODELS_UNAVAILABLE = "unavailable"
LITELLM_MODELS_NOT_APPLICABLE = "not_applicable"
_BACKOFF_CAP_FACTOR = 10


def _fetch_litellm_models() -> LiteLLMModels | None:
    """
    Clear the models cache and fetch models from the LiteLLM proxy.

    Returns None when the proxy answered without chat models. Raises when the fetch fails.
    """
    from codemie.enterprise.litellm import get_available_models
    from codemie.service.llm_proxy.provider_registry import get_active_llm_proxy_provider

    # Clear the cache so a previously cached empty result cannot mask recovery
    get_active_llm_proxy_provider().reload_models_cache()

    models = get_available_models(raise_on_error=True)
    return models if models.chat_models else None


def _apply_litellm_models(models: LiteLLMModels) -> None:
    from codemie.service.llm_service.llm_service import llm_service

    llm_service.initialize_default_litellm_models(models)
    logger.info(
        f"Initialized {len(models.chat_models)} chat models, and  {len(models.embedding_models)} embedding models."
    )


def initialize_litellm_models() -> bool:
    """
    Initialize default LiteLLM models from proxy if enabled.

    Never raises and never blocks startup. Returns False when LiteLLM models could not be
    loaded, in which case the YAML defaults stay active.
    """
    try:
        models = _fetch_litellm_models()
        if models is not None:
            _apply_litellm_models(models)
            return True
        reason = "LiteLLM returned no chat models"
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"

    logger.error(
        "LiteLLM models are NOT active, serving YAML defaults; retrying in background. "
        f"Preconfigured content created now uses the YAML fallback. reason={reason}"
    )
    return False


def schedule_litellm_models_recovery(app: FastAPI, tasks: list[asyncio.Task]) -> None:
    """Retry loading LiteLLM models in the background until the proxy becomes reachable."""
    if not is_litellm_enabled() or getattr(app.state, "litellm_models_status", None) != LITELLM_MODELS_UNAVAILABLE:
        return

    async def _loop() -> None:
        initial = max(config.LITELLM_MODELS_RETRY_INTERVAL_SECONDS, 1)
        cap = initial * _BACKOFF_CAP_FACTOR
        delay = initial
        while True:
            await asyncio.sleep(delay)
            try:
                # Fetch off the event loop, apply on it so both model lists are swapped without a worker thread
                models = await asyncio.to_thread(_fetch_litellm_models)
                if models is not None:
                    _apply_litellm_models(models)
                    app.state.litellm_models_status = LITELLM_MODELS_LOADED
                    logger.info(
                        "LiteLLM models recovered. Preconfigured content created during the outage "
                        "still uses the YAML fallback model."
                    )
                    return
                reason = "LiteLLM returned no chat models"
            except Exception as error:
                reason = f"{type(error).__name__}: {error}"
            delay = min(delay * 2, cap)
            logger.warning(f"LiteLLM models still unavailable, retrying in {delay}s. {reason=}")

    tasks.append(asyncio.create_task(_loop(), name="litellm_models_recovery"))
