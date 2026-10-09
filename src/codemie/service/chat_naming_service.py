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

from langchain_core.messages import BaseMessage

from codemie.configs import config, logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.rest_api.models.conversation import Conversation
from codemie.service.constants import CHAT_CONTEXTUAL_NAMING_ENABLED_KEY
from codemie.service.dynamic_config_service import DynamicConfigService
from codemie.templates.chat_naming_prompt import chat_naming_prompt

MAX_NAME_LENGTH = 60


def _is_chat_contextual_naming_enabled() -> bool:
    return DynamicConfigService.get_bool_value_safe(
        CHAT_CONTEXTUAL_NAMING_ENABLED_KEY,
        default=config.CHAT_CONTEXTUAL_NAMING_ENABLED,
    )


class ChatNamingService:
    """Generates a concise LLM-based conversation name, with legacy-name fallback on any failure."""

    @classmethod
    def rename_conversation(
        cls,
        conversation_id: str,
        first_message: str,
        assistant_response: str,
        request_id: str | None = None,
    ) -> None:
        if not _is_chat_contextual_naming_enabled():
            return

        name = cls.generate_name(first_message, assistant_response, request_id)
        if not name:
            return

        try:
            conversation = Conversation.find_by_id(conversation_id)
            if not conversation:
                return

            conversation.conversation_name = name
            conversation.update()
        except Exception as error:
            logger.error(
                f"Chat contextual naming failed to persist. RequestId={request_id}, Error={error}", exc_info=True
            )

    @classmethod
    def generate_name(
        cls,
        first_message: str,
        assistant_response: str,
        request_id: str | None = None,
    ) -> str | None:
        try:
            llm = get_llm_by_credentials(
                llm_model=config.CHAT_CONTEXTUAL_NAMING_LLM_MODEL,
                streaming=False,
                request_id=request_id,
            )
            prompt = chat_naming_prompt.format(
                first_message=first_message or "",
                assistant_response=assistant_response or "",
            )
            response = llm.invoke(prompt)
            name = cls._extract_content(response).strip().strip('"').strip("'").strip()
            if not name:
                return None
            return name[:MAX_NAME_LENGTH]
        except Exception as error:
            logger.error(f"Chat contextual naming failed. RequestId={request_id}, Error={error}", exc_info=True)
            return None

    @staticmethod
    def _extract_content(response: BaseMessage | str | None) -> str:
        if response is None:
            return ""
        content = getattr(response, "content", response)
        if content is None:
            return ""
        if isinstance(content, list):
            return "".join(str(part) for part in content)
        return content if isinstance(content, str) else str(content)
