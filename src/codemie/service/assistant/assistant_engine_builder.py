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

from typing import Any, Callable

from langgraph.graph.state import CompiledStateGraph

from codemie.configs.logger import logger
from codemie.core.models import AssistantChatRequest
from codemie.core.thread import MessageQueue
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User


class LangGraphAssistantBuilder:
    @staticmethod
    def create_subagent_executors(
        assistant: Assistant,
        user: User,
        request: AssistantChatRequest,
        request_uuid: str,
        thread_generator: MessageQueue,
        llm_model: str,
    ) -> list[CompiledStateGraph[Any, Any, Any, Any]]:
        if not assistant.assistant_ids:
            return []

        from codemie.service.tools.assistant_factory import create_assistant_executors

        logger.debug(f"Creating subagent executors for {len(assistant.assistant_ids)} sub-assistants")
        subagents = create_assistant_executors(
            assistant_ids=assistant.assistant_ids,
            user=user,
            request=request,
            request_uuid=request_uuid,
            thread_generator=thread_generator,
            llm_model=llm_model,
            parent_assistant=assistant,
        )
        logger.debug(f"Created {len(subagents)} subagent executors")
        return subagents

    @staticmethod
    def get_subagent_descriptions(assistant: Assistant, user: User) -> dict[str, str]:
        if not assistant.assistant_ids:
            return {}

        try:
            sub_assistants = Assistant.get_by_ids(user, assistant.assistant_ids, parent_assistant=assistant)
            descriptions = {
                sub_assistant.name: sub_assistant.description or f"Assistant {sub_assistant.name}"
                for sub_assistant in sub_assistants
            }
            logger.debug(f"Fetched descriptions for {len(descriptions)} subagents")
            return descriptions
        except Exception as error:
            logger.error(f"Failed to fetch subagent descriptions: {str(error)}")
            return {}

    @staticmethod
    def configure_agent_kwargs(
        agent_kwargs: dict[str, Any],
        assistant: Assistant,
        user: User,
        request: AssistantChatRequest,
        request_uuid: str,
        thread_generator: MessageQueue,
        llm_model: str,
        smart_tool_selection_enabled: bool,
        *,
        create_subagent_executors: Callable[..., list[CompiledStateGraph[Any, Any, Any, Any]]],
        get_subagent_descriptions: Callable[[Assistant, User], dict[str, str]],
    ) -> None:
        agent_kwargs["smart_tool_selection_enabled"] = smart_tool_selection_enabled

        subagents = create_subagent_executors(
            assistant=assistant,
            user=user,
            request=request,
            request_uuid=request_uuid,
            thread_generator=thread_generator,
            llm_model=llm_model,
        )
        if subagents:
            agent_kwargs["subagents"] = subagents
            agent_kwargs["subagent_descriptions"] = get_subagent_descriptions(assistant, user)
