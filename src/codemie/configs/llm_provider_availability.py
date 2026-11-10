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

"""Whether the LLM-backed core (Chats, Assistants, Workflows, ...) can run in standalone mode.

Settings-only check: no network call, no boto3 credential lookup, never raises. ``missing`` names
settings, never values. "Set" means a non-empty string (no strip, no placeholder detection). Stored
per-project or per-user credentials are never read. The provider comes from the default GLOBAL model
in the static ``MODELS_ENV`` profile, read directly (no ``llm_service`` init, no call).

Note: ``/v1/healthcheck/model`` (``model_provider_readiness``) uses different rules and can report
NOT_CONFIGURED while core is on. A single rule table for both is a later follow-up.
"""

from functools import cache

from codemie.configs.logger import logger

LITELLM_PROXY = "litellm_proxy"

# provider value -> settings that must be non-empty
_REQUIRED = {
    "azure_openai": ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_URL"),
    "google_vertexai": ("GOOGLE_PROJECT_ID", "GOOGLE_VERTEXAI_REGION"),
    "vertex_ai-anthropic_models": ("GOOGLE_PROJECT_ID", "GOOGLE_CLAUDE_VERTEXAI_REGION"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    LITELLM_PROXY: ("LITE_LLM_URL", "LITE_LLM_APP_KEY"),
}


def _default_provider() -> str | None:
    from codemie.configs.llm_config import ModelCategory, llm_config  # noqa: PLC0415

    for model in llm_config.llm_models:
        if model.enabled and model.is_default_for(ModelCategory.GLOBAL):
            provider = model.provider
            return getattr(provider, "value", provider)
    return None


def _bedrock_missing(cfg) -> list[str]:
    if cfg.AWS_BEDROCK_REGION:
        return []
    import boto3  # noqa: PLC0415

    # Region lookup reads config/env only; credentials are never resolved.
    return [] if boto3.Session().region_name else ["AWS_BEDROCK_REGION"]


class _ById:
    """Hashable handle: the settings object is unhashable, so the cache keys on its identity."""

    def __init__(self, obj):
        self.obj = obj

    def __hash__(self):
        return id(self.obj)

    def __eq__(self, other):
        return isinstance(other, _ById) and other.obj is self.obj


def core_availability(config) -> tuple[bool, str | None, list[str]]:
    """Return ``(available, provider, missing)``. Available and unavailable results are cached per
    settings object until restart; exceptions are not cached. Settings changes require a restart.

    Tests reset the cache with ``core_availability.cache_clear()``.
    """
    try:
        return _compute(_ById(config))
    except Exception as e:
        logger.error(f"Core gating: could not determine LLM provider availability, treating core as off: {e!r}")
        return False, None, ["provider availability check failed; see logs"]


@cache
def _compute(ref: _ById) -> tuple[bool, str | None, list[str]]:
    config = ref.obj
    from codemie.enterprise.litellm import is_litellm_enabled  # noqa: PLC0415

    provider = LITELLM_PROXY if is_litellm_enabled() and config.LLM_PROXY_MODE == "lite_llm" else _default_provider()
    if provider == "aws_bedrock":
        missing = _bedrock_missing(config)
    elif provider in _REQUIRED:
        missing = [name for name in _REQUIRED[provider] if not getattr(config, name)]
    else:
        return False, provider, ["MODELS_ENV (no default global model, or its provider is unknown)"]
    return not missing, provider, missing


core_availability.cache_clear = _compute.cache_clear
