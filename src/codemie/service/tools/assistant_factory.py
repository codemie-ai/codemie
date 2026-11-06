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

from typing import List, Optional

from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.configs.logger import logger
from codemie.core.thread import MessageQueue
from codemie.core.models import AssistantChatRequest
from codemie.core.constants import UniqueThoughtParentIds
from codemie.core.exception_formatting import describe_exception
from codemie.service.assistant.assistant_engine_builder import (
    SubagentBuildContext,
    SubagentHandle,
)
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.mcp.models import MCPToolLoadException


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
        build_context: SubagentBuildContext | None = None,
    ):
        self.assistant = assistant
        self.user = user
        self.request = request
        self.request_uuid = request_uuid
        self.thread_generator = thread_generator
        self.build_context = build_context or SubagentBuildContext()

    def build(self) -> SubagentHandle:
        """
        Build an agent executor (compiled graph) for the assistant.

        Model policy is not checked here. AssistantService.build_agent resolves the sub-assistant's
        model via resolve_assistant_model under the sub-assistant's own project: an unconfigured
        whitelist is unrestricted, an empty whitelist raises ModelNotWhitelistedException, a
        populated whitelist falls back from the user's pick to the sub-assistant's own model and
        then the project default, and global assistants are exempt.

        Returns:
            SubagentHandle with the built agent instance, its compiled executor graph and its
            tool-confirmation flags

        Raises:
            ModelNotWhitelistedException: If the sub-assistant's project allows no models (400)
        """
        try:
            # Import here to avoid circular imports
            from codemie.service.assistant_service import AssistantService

            # Pre-build the agent
            handle = AssistantService.build_agent_handle(
                assistant=self.assistant,
                request=self.request.model_copy(),
                user=self.user,
                request_uuid=self.request_uuid,
                thread_generator=self.thread_generator,
                tool_callbacks=[AgentStreamingCallback(self.thread_generator)],
                is_subagent=True,
                build_context=self.build_context,
            )
            handle.agent.set_thread_context({}, UniqueThoughtParentIds.LATEST.value)

            return handle

        except Exception as e:
            logger.error(f"Failed to create agent executor for assistant {self.assistant.id}: {describe_exception(e)}")
            raise


def create_assistant_executors(
    assistant_ids: List[str],
    user: Optional[User] = None,
    request: Optional[AssistantChatRequest] = None,
    request_uuid: Optional[str] = None,
    thread_generator: Optional[MessageQueue] = None,
    parent_assistant: Optional[Assistant] = None,
    build_context: SubagentBuildContext | None = None,
) -> list[SubagentHandle]:
    """
    Create agent executors from assistant IDs with optional version pinning.

    Args:
        assistant_ids: List of sub-assistant IDs to create executors from
        user: User making the request
        request: Chat request containing optional sub_assistants_versions
        request_uuid: Unique request identifier
        thread_generator: Thread generator for streaming
        parent_assistant: Parent assistant (orchestrator) that contains these sub-assistants
        build_context: Build context of the parent; each sub-assistant gets a child of it

    Returns:
        List[SubagentHandle]: the built sub-assistants
    """
    handles: list[SubagentHandle] = []
    if build_context is None:
        build_context = SubagentBuildContext()

    # Get version overrides from parent assistant or request
    sub_assistants_versions = _sub_assistants_versions(request)

    # Get assistant objects from IDs
    try:
        assistants = Assistant.get_by_ids(user, assistant_ids, parent_assistant=parent_assistant)
    except Exception as e:
        logger.error(f"Error fetching assistants: {str(e)}")
        return handles

    # Create handles from assistants
    for assistant in assistants:
        deleted_name = _validate_remote_entity_exists_and_cleanup(assistant)

        if deleted_name:
            logger.info(f"Assistant {assistant.id} has been deleted remotely: {deleted_name}")
            continue

        child_context = build_context.child()
        try:
            assistant = _with_pinned_version(assistant, sub_assistants_versions)
            factory = AssistantFactory(
                assistant=assistant,
                user=user,
                request=request,
                request_uuid=request_uuid,
                thread_generator=thread_generator,
                build_context=child_context,
            )
            handles.append(factory.build())
        except MCPToolLoadException as e:
            e.attach_assistant_context(assistant_name=assistant.name, assistant_id=assistant.id)
            logger.error(
                f"Failed to create executor for sub-assistant '{assistant.name}' (id={assistant.id}) "
                f"due to MCP server '{e.server_name}': {str(e.original_error)}"
            )
            raise
        except Exception as e:
            logger.error(f"Failed to create executor for assistant {assistant.id}: {describe_exception(e)}")

    return handles


def _sub_assistants_versions(request: AssistantChatRequest | None) -> dict[str, int] | None:
    """The sub-assistant version pins of the request, or ``None`` when it pins none."""
    if request and request.sub_assistants_versions:
        return request.sub_assistants_versions
    return None


def _with_pinned_version(assistant: Assistant, sub_assistants_versions: dict[str, int] | None) -> Assistant:
    """Apply the version pinned for the sub-assistant in ``sub_assistants_versions``, if any."""
    if not sub_assistants_versions or assistant.id not in sub_assistants_versions:
        return assistant
    version_number = sub_assistants_versions[assistant.id]
    logger.debug(f"Applying version {version_number} to sub-assistant {assistant.id}")

    from codemie.service.assistant.assistant_version_service import AssistantVersionService

    return AssistantVersionService.apply_version_to_assistant(assistant, version_number)
