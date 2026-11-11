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

"""LiteLLM client for speech-to-text (audio transcription) calls."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from .llm_factory import _configure_direct_runtime_overrides, _generate_litellm_headers, _spend_attribution_values
from codemie.service.spend.spend_logs_metadata import spend_logs_metadata_header

if TYPE_CHECKING:
    from openai import OpenAI

    from codemie.configs.llm_config import LLMModel
    from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials


_STT_REQUEST_TIMEOUT_SECONDS = 300.0


def get_litellm_stt_client(
    llm_model_details: "LLMModel",
    litellm_context: "LiteLLMContext | None",
    username: str | None,
    user_id: str | None = None,
) -> "OpenAI | None":
    """Return an OpenAI client for the LiteLLM proxy, for audio transcription.

    Mirrors get_litellm_embedding_model: same credentials, tagging headers and budget
    attribution. The LiteLLM customer id travels in the default headers.
    Returns None when LiteLLM is not the active proxy. The request timeout comes from the
    model's stt_config, or defaults to a value above the usual server limit for long audio.
    """
    from openai import OpenAI

    from .dependencies import is_litellm_enabled
    from codemie.configs import config
    from codemie.core.constants import LLMProxyMode

    if not is_litellm_enabled() or LLMProxyMode.lite_llm != config.LLM_PROXY_MODE:
        return None

    creds: "LiteLLMCredentials | None" = litellm_context.credentials if litellm_context else None
    merged_headers = _generate_litellm_headers(llm_model_details, litellm_context)
    request_params: dict[str, Any] = {
        "openai_api_key": creds.api_key if creds and creds.api_key else config.LITE_LLM_APP_KEY,
        "azure_endpoint": config.LITE_LLM_URL,
    }
    resolved_category = _configure_direct_runtime_overrides(
        llm_model_details=llm_model_details,
        litellm_context=litellm_context,
        username=username,
        user_id=user_id,
        creds=creds,
        merged_headers=merged_headers,
        request_params=request_params,
    )

    # One client per transcribe call (see SttService._build_client), so a run id minted here, like
    # the embedding path and unlike the chat path's per-call model reuse, already names this call.
    attribution = _spend_attribution_values(
        request_id=None,
        user_id=user_id,
        category=resolved_category,
        litellm_context=litellm_context,
        username=username,
    )
    merged_headers.update(spend_logs_metadata_header(**attribution, run_id=str(uuid.uuid4())))

    stt_config = llm_model_details.stt_config
    timeout = stt_config.request_timeout_seconds if stt_config and stt_config.request_timeout_seconds else None
    client = OpenAI(
        base_url=request_params["azure_endpoint"],
        api_key=request_params["openai_api_key"],
        default_headers=merged_headers or None,
        max_retries=2,
        timeout=timeout or _STT_REQUEST_TIMEOUT_SECONDS,
    )
    return client
