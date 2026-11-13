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

"""Direct Bedrock chat model that requests langchain-aws cache points when prompt caching is enabled."""

from __future__ import annotations

from typing import Any

from langchain_aws import ChatBedrockConverse

from codemie.core.prompt_caching import CACHE_CONTROL


class PromptCachingChatBedrockConverse(ChatBedrockConverse):
    """langchain-aws reads ``cache_control`` only from per-call arguments, so this supplies it on every call.

    ``_apply_cache_points`` then adds a ``cachePoint`` after the system prompt, the last message and the tools,
    on both the converse and converse-stream paths.
    """

    prompt_caching: bool = False

    def _apply_cache_points(
        self,
        cache_control: dict[str, Any] | None,
        system: list[dict[str, Any]],
        bedrock_messages: list[dict[str, Any]],
        params: dict[str, Any] | None = None,
    ) -> None:
        if self.prompt_caching and not cache_control:
            cache_control = dict(CACHE_CONTROL)
        super()._apply_cache_points(cache_control, system, bedrock_messages, params)
