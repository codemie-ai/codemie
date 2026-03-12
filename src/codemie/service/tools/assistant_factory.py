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

from typing import List, Optional, Any

from langgraph.graph.state import CompiledStateGraph

from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.configs.logger import logger
from codemie.core.thread import MessageQueue
from codemie.core.models import AssistantChatRequest
from codemie.core.constants import UniqueThoughtParentIds
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User


def _validate_remote_entity_exists_and_cleanup(assistant: Assistant) -> Optional[str]:
    # Import here to avoid circular imports
    from codemie.service.aws_bedrock.bedrock_agent_service import BedrockAgentService

    return BedrockAgentService.validate_remote_entity_exists_and_cleanup(assistant)


class AssistantFactory:
    """
    Factory for creating agent executors from assistants.

    This class is responsible for creating agent executors (compiled graphs)
    based on assistant configurations for use as subagents in multi-agent systems.
    """

    def __init__(
        self,
        assistant: Assistant,
        user: Optional[User] = None,
        request: Optional[AssistantChatRequest] = None,
        request_uuid: Optional[str] = None,
        thread_generator: Optional[MessageQueue] = None,
        llm_model: Optional[str] = None,
    ):
        self.assistant = assistant
        self.user = user
        self.request = request
        self.request_uuid = request_uuid
        self.thread_generator = thread_generator
        self.llm_model = llm_model

    def build(self):
        """
        Build an agent executor (compiled graph) for the assistant.

        Returns:
            Compiled agent executor graph
        """
        try:
            # Import here to avoid circular imports
            from codemie.service.assistant_service import AssistantService

            # Pre-build the agent
            agent = AssistantService.build_agent(
                assistant=self.assistant,
                request=self.request.model_copy(),
                user=self.user,
                request_uuid=self.request_uuid,
                thread_generator=self.thread_generator,
                tool_callbacks=[AgentStreamingCallback(self.thread_generator)],
            )
            agent.set_thread_context({}, UniqueThoughtParentIds.LATEST.value)

            # Return the agent_executor directly
            return agent.agent_executor

        except Exception as e:
            logger.error(f"Failed to create agent executor for assistant {self.assistant.id}: {str(e)}")
            raise


def create_assistant_executors(
    assistant_ids: List[str],
    user: Optional[User] = None,
    request: Optional[AssistantChatRequest] = None,
    request_uuid: Optional[str] = None,
    thread_generator: Optional[MessageQueue] = None,
    llm_model: Optional[str] = None,
    parent_assistant: Optional[Assistant] = None,
) -> list[CompiledStateGraph[Any, Any, Any, Any]]:
    """
    Create agent executors from assistant IDs with optional version pinning.

    Args:
        assistant_ids: List of sub-assistant IDs to create executors from
        user: User making the request
        request: Chat request containing optional sub_assistants_versions
        request_uuid: Unique request identifier
        thread_generator: Thread generator for streaming
        llm_model: LLM model being used
        parent_assistant: Parent assistant (orchestrator) that contains these sub-assistants

    Returns:
        List of agent executors (compiled graphs) for the sub-assistants
    """
    executors = []

    # Get version overrides from parent assistant or request
    sub_assistants_versions = None
    if request and request.sub_assistants_versions:
        sub_assistants_versions = request.sub_assistants_versions

    # Get assistant objects from IDs
    try:
        assistants = Assistant.get_by_ids(user, assistant_ids, parent_assistant=parent_assistant)
    except Exception as e:
        logger.error(f"Error fetching assistants: {str(e)}")
        return []

    # Create executors from assistants
    for assistant in assistants:
        deleted_name = _validate_remote_entity_exists_and_cleanup(assistant)

        if deleted_name:
            logger.info(f"Assistant {assistant.id} has been deleted remotely: {deleted_name}")
            continue

        try:
            # Apply version if specified in sub_assistants_versions
            if sub_assistants_versions and assistant.id in sub_assistants_versions:
                version_number = sub_assistants_versions[assistant.id]
                logger.debug(f"Applying version {version_number} to sub-assistant {assistant.id}")

                from codemie.service.assistant.assistant_version_service import AssistantVersionService

                assistant = AssistantVersionService.apply_version_to_assistant(assistant, version_number)

            factory = AssistantFactory(
                assistant=assistant,
                user=user,
                request=request,
                request_uuid=request_uuid,
                thread_generator=thread_generator,
                llm_model=llm_model,
            )
            executor = factory.build()
            executors.append(executor)
        except Exception as e:
            logger.error(f"Failed to create executor for assistant {assistant.id}: {str(e)}")

    return executors
