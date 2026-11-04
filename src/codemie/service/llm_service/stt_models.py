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

"""Rules that choose the speech-to-text model: the env model, LiteLLM models, name clashes and defaults."""

from __future__ import annotations

from codemie.configs import config
from codemie.configs.llm_config import LLMModel


class LegacySttModel(LLMModel):
    """STT model from the STT_API_* variables; its type tells client routing that it is not a LiteLLM model."""


def build_legacy_stt_model() -> LegacySttModel | None:
    """The single STT model configured through the STT_API_* environment variables, if any."""
    if not config.STT_API_KEY:
        return None
    name = config.STT_MODEL_NAME or config.STT_API_DEPLOYMENT_NAME or "default"
    return LegacySttModel(
        base_name=name,
        deployment_name=config.STT_API_DEPLOYMENT_NAME or name,
        label=name,
        enabled=True,
    )


def merge_stt_models(legacy: LegacySttModel | None, litellm_models: list[LLMModel]) -> list[LLMModel]:
    """Env-configured model first, then the LiteLLM ones.

    The env-configured model stays the default so deployments that only had STT_API_*
    keep their behavior when LiteLLM audio models are added. On a name clash the
    LiteLLM model wins and takes the first place, so it stays the default.
    """
    if legacy is None:
        return litellm_models
    clashing = [m for m in litellm_models if m.base_name == legacy.base_name]
    if clashing:
        return [*clashing, *(m for m in litellm_models if m.base_name != legacy.base_name)]
    return [legacy, *litellm_models]


def pick_stt_model(models: list[LLMModel], model_name: str | None = None) -> LLMModel | None:
    """Return the named model, or the first one when no name is given (None if there are no models).

    Raises:
        UnknownSttModelError: if a name is given but is not among the models.
    """
    from codemie.core.exceptions import UnknownSttModelError

    if not model_name:
        return models[0] if models else None
    for model in models:
        if model.base_name == model_name:
            return model
    raise UnknownSttModelError(f"Unknown or not allowed STT model: {model_name}")
