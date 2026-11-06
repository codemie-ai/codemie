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

import asyncio
import contextvars
import json
import queue
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from time import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, List

if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import ToolCallResumeRequest

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import BaseModel

from fastapi import BackgroundTasks, Request, status
from starlette.responses import StreamingResponse

from codemie.chains.base import Thought, StreamedGenerationResult
from codemie.configs import config, logger
from codemie.configs.customer_config import customer_config
from codemie.core.ability import Ability, Action
from codemie.core.dependecies import set_disable_prompt_cache
from codemie.core.errors import AgentErrorDetails, ErrorDetailLevel
from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException
from codemie.core.models import (
    BaseModelResponse,
    AssistantChatRequest,
    BackgroundTaskRequest,
    AssistantDetails,
    TokensUsage,
)
from codemie.core.routing_info import RoutingInfo
from codemie.core.thread import CancellationReason, ThreadedGenerator, finalize_thoughts
from codemie.enterprise.litellm.proxy_router import handle_agent_exception
from codemie.rest_api.a2a.client.remote_agent_connection import (
    RemoteAgentConnections,
    TaskCallbackArg,
    is_final_update,
)
from codemie.rest_api.a2a.types import (
    A2AClientError,
    A2AProtocolVersionError,
    AgentCard,
    AgentCardInterfaceError,
    Message,
    SendMessageRPCRequest,
    SendStreamingMessageRPCRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    get_jsonrpc_url,
)
from codemie.rest_api.a2a.utils import convert_to_task_request, convert_to_base_model_response
from codemie.rest_api.handlers.execution_model import RequestExecutionModel
from codemie.rest_api.models.assistant import Assistant, AssistantType
from codemie.rest_api.models.base import ConversationStatus
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.routers.utils import run_assistant_in_thread_pool
from codemie.rest_api.utils.client_context import get_client_source
from codemie.rest_api.security.user import User
from codemie.rest_api.utils.request_utils import extract_custom_headers
from codemie.service.assistant_service import AssistantService
from codemie.service.assistant.assistant_engine_builder import (
    BuildChannel,
)
from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService
from codemie.service.conversation.history_projection_service import (
    NATIVE_TOOLS_MODE,
    PLAIN_CHAT_MODE,
    SKILL_TOOL_NAME,
    TEXT_LEDGER_MODE,
    TOOL_REPLAY_TYPE,
    ConversationHistoryProjectionService,
)
from codemie.service.background_tasks_service import BackgroundTasksService
from codemie.service.constants import (
    AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY,
    AI_AGENT_QUEUE_POLL_TIMEOUT_SECONDS_KEY,
)
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.rest_api.handlers.tool_call_resume import (
    NDJSON_MEDIA_TYPE,
    ConsumedPendingToolCall,
    ToolCallResumeMixin,
    begin_resume,
    build_resumed_agent,
    resumed_turn_request,
)
from codemie.service.conversation_service import ConversationService, UpsertChatHistoryParams, _guard_finished
from codemie.service.dynamic_config_service import DynamicConfigService
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.request_summary_manager import request_summary_manager
from codemie.configs.pyroscope_config import pyroscope_profile

# Constants
ACCESS_DENIED_MESSAGE = "Access denied"


@dataclass
class ChatHistoryData:
    """Data class for chat history parameters"""

    execution_start: float
    request: AssistantChatRequest
    response: str
    thoughts: List[Thought]
    status: ConversationStatus = ConversationStatus.SUCCESS
    user_message_received_at: datetime | None = None
    a2ui_envelopes: List[dict] | None = None
    in_progress: bool = False


class AssistantRequestHandler(ToolCallResumeMixin, ABC):
    def __init__(self, assistant: Assistant, user: User, request_uuid: str):
        self.assistant = assistant
        self.user = user
        self.request_uuid = request_uuid
        self.background_tasks: BackgroundTasks | None = None
        self.client_source = get_client_source()
        self._execution_model = RequestExecutionModel(assistant)

    @abstractmethod
    def process_request(
        self,
        request: AssistantChatRequest,
        background_tasks: BackgroundTasks,
        raw_request: Request,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> StreamingResponse | BaseModelResponse:
        """
        Process assistant request with optional error handling configuration.

        Parameters:
        - include_tool_errors: Include tool error details in response
        - error_detail_level: Error verbosity (minimal/standard/full)
        """
        pass

    def _load_authorized_history(self, conversation_id: str | None) -> list:
        """Resolve the stored conversation history, enforcing ownership FIRST.

        The 403 is raised BEFORE any history read, so structured-answer intake cannot
        become an existence/answered-state oracle for another user's conversation.
        """
        conversation = Conversation.find_by_id(conversation_id) if conversation_id else None
        if conversation and not Ability(self.user).can(Action.READ, conversation):
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=ACCESS_DENIED_MESSAGE,
                details=f"You don't have permission to access conversation {conversation_id}.",
            )
        return conversation.history if conversation else []

    def _validate_a2ui_action(self, request: AssistantChatRequest) -> None:
        """Validate incoming A2UI answer fields against the stored conversation history.

        Runs whenever ANY A2UI answer field is present: the conversation service persists
        ``a2ui_data_model`` unconditionally, so a data model submitted without its action
        would otherwise reach conversation history without ownership, surface-existence,
        already-answered or size checks.
        """
        if request.a2ui_action is None and request.a2ui_data_model is None:
            return
        from codemie.service.conversation.a2ui_intake import validate_a2ui_intake

        if request.a2ui_action is None:
            raise ExtendedHTTPException(
                code=422,
                message="A2UI data model requires the matching A2UI action",
                details="a2uiDataModel was submitted without a2uiAction; the answered surface cannot be resolved.",
            )
        history = self._load_authorized_history(request.conversation_id)
        # Re-answer semantics: request.history_index marks the exact turn being
        # replaced by this submit (like editing the previous request).
        # The return value is the server's own stored record of the answered surface —
        # the answer was validated against it, and it is the only trusted description of
        # what was asked (the client action carries no catalog or component identity).
        surface_envelopes = validate_a2ui_intake(
            history,
            request.a2ui_action,
            request.a2ui_data_model,
            replacing_history_index=request.history_index,
        )
        logger.debug(
            f"A2UI answer accepted for conversation {request.conversation_id} "
            f"against {len(surface_envelopes)} stored surface envelopes."
        )

    def _deny_conversation_access(self, conversation: Conversation, conversation_id: str) -> None:
        logger.warning(
            f"User {self.user.id} denied access to conversation {conversation_id} owned by {conversation.user_id}"
        )
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ACCESS_DENIED_MESSAGE,
            details=f"You don't have permission to access conversation {conversation_id}.",
            help="Please ensure you have the correct permissions or contact the conversation owner.",
        )

    def _require_conversation_read(self, conversation: Conversation, conversation_id: str) -> None:
        if not Ability(self.user).can(Action.READ, conversation):
            self._deny_conversation_access(conversation, conversation_id)

    def _guard_conversation_not_finished(self, request: AssistantChatRequest) -> None:
        """Reject requests that target a finished conversation before inference starts; end a pending pause."""
        if not request.conversation_id:
            return
        conversation = Conversation.find_by_id(request.conversation_id)
        if not conversation:
            return
        self._require_conversation_read(conversation, request.conversation_id)
        _guard_finished(conversation)
        # A new message ends any earlier pause, whatever the tree flag of the agent now built (AR-4, AC19).
        self._end_pending_pause_at_turn_start(conversation, request.conversation_id)

    def _populate_conversation_history(self, request: AssistantChatRequest) -> None:
        """
        Retrieve and populate conversation history from existing conversation if conversation_id is provided.

        This method enhances the request with historical context by:
        1. Checking if a conversation_id exists in the request
        2. Retrieving the conversation from the database
        3. Verifying user permissions
        4. Converting conversation history to ChatMessage format
        5. Updating the request.history field

        Args:
            request: The assistant chat request to enhance with history

        Raises:
            ExtendedHTTPException: When conversation is not found or access is denied
        """
        if not self._is_conversation_replay_v2_enabled():
            self._populate_conversation_history_legacy(request)
            return

        if not request.conversation_id:
            logger.debug("Skipping conversation replay history population because conversation_id is missing.")
            return

        # Retrieve existing conversation using the same pattern as the router
        conversation = Conversation.find_by_id(request.conversation_id)

        if not conversation:
            logger.debug(f"Conversation {request.conversation_id} not found for user {self.user.id}")
            return

        self._require_conversation_read(conversation, request.conversation_id)
        _guard_finished(conversation)

        if request.history and not self._should_replace_request_history(request.history):
            logger.debug(
                f"Keeping request-provided history. ConversationId={request.conversation_id}, "
                f"HistoryMessages={len(request.history)}"
            )
            return

        try:
            # Update request with conversation history
            request.history = ConversationHistoryProjectionService.build_for_request(
                conversation=conversation,
                mode=self._resolve_history_projection_mode(request),
                current_assistant_id=getattr(self.assistant, "id", None),
                available_tool_names=self._get_available_replay_tool_names(),
            )

            logger.debug(
                f"Retrieved conversation history for conversation_id: {request.conversation_id}, "
                f"messages: {len(request.history)}, user_id: {self.user.id}"
            )

        except Exception as e:
            logger.error(
                f"Unexpected error converting conversation history for {request.conversation_id}: {str(e)}",
                exc_info=True,
            )
            raise ExtendedHTTPException(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="Failed to retrieve conversation history",
                details=f"An error occurred while processing conversation history: {str(e)}",
                help="Please try again or contact support if the issue persists.",
            ) from e

    def _sync_uploaded_files_to_workspace(self, request: AssistantChatRequest) -> None:
        if not request.conversation_id or not request.file_names:
            return

        AgentWorkspaceService().sync_uploaded_files(
            conversation_id=request.conversation_id,
            file_urls=request.file_names,
            user=self.user,
        )

    def _populate_conversation_history_legacy(self, request: AssistantChatRequest) -> None:
        if not request.conversation_id:
            return

        conversation = Conversation.find_by_id(request.conversation_id)

        if not conversation:
            logger.debug(f"Conversation {request.conversation_id} not found for user {self.user.id}")
            return

        self._require_conversation_read(conversation, request.conversation_id)
        _guard_finished(conversation)

        if request.history:
            logger.debug(
                f"History is already provided. "
                f"{len(request.history)} history messages, "
                f"conversation_id: {request.conversation_id}, "
            )
            return

        try:
            request.history = conversation.to_chat_history()
            logger.debug(
                f"Retrieved conversation history for conversation_id: {request.conversation_id}, "
                f"messages: {len(request.history)}, user_id: {self.user.id}"
            )
        except Exception as e:
            logger.error(
                f"Unexpected error converting conversation history for {request.conversation_id}: {str(e)}",
                exc_info=True,
            )
            raise ExtendedHTTPException(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="Failed to retrieve conversation history",
                details=f"An error occurred while processing conversation history: {str(e)}",
                help="Please try again or contact support if the issue persists.",
            ) from e

    @staticmethod
    def _should_replace_request_history(history: list | str) -> bool:
        """Return True when request history is plain chat and should be replaced by projected conversation history."""
        if not isinstance(history, list):
            logger.info("Replacing request history because it is not a list of model messages.")
            return True

        if not history:
            logger.debug("Replacing request history because it is empty.")
            return True

        for item in history:
            if isinstance(item, ToolMessage):
                logger.debug("Keeping request history because it already contains ToolMessage entries.")
                return False
            if isinstance(item, AIMessage) and getattr(item, "tool_calls", None):
                logger.debug("Keeping request history because it already contains AI tool calls.")
                return False
            if isinstance(item, BaseMessage):
                continue

        logger.info("Replacing request history because it only contains plain chat entries.")
        return True

    @staticmethod
    def _collect_toolkit_tool_names(toolkits: list | None) -> set[str]:
        tool_names: set[str] = set()
        for toolkit in toolkits or []:
            for tool in getattr(toolkit, "tools", []) or []:
                tool_name = getattr(tool, "name", None)
                if tool_name:
                    tool_names.add(tool_name)
        return tool_names

    @staticmethod
    def _collect_mcp_server_tool_names(mcp_servers: list | None) -> tuple[set[str], bool]:
        tool_names: set[str] = set()
        unknown_availability = False

        for mcp_server in mcp_servers or []:
            if getattr(mcp_server, "enabled", True) is False:
                continue

            server_tools = getattr(mcp_server, "tools", None)
            if not server_tools:
                unknown_availability = True
                continue

            tool_names.update(tool_name for tool_name in server_tools if tool_name)

        return tool_names, unknown_availability

    def _has_skill_tool(self) -> bool:
        skill_ids = getattr(self.assistant, "skill_ids", None)
        return isinstance(skill_ids, (list, tuple, set)) and bool(skill_ids)

    def _get_available_replay_tool_names(self) -> set[str] | None:
        tool_names = self._collect_toolkit_tool_names(getattr(self.assistant, "toolkits", []))
        mcp_tool_names, unknown_availability = self._collect_mcp_server_tool_names(
            getattr(self.assistant, "mcp_servers", [])
        )
        tool_names.update(mcp_tool_names)

        if self._has_skill_tool():
            tool_names.add(SKILL_TOOL_NAME)

        if unknown_availability:
            return None

        return tool_names

    def _build_agent(self, **kwargs: Any) -> Any:
        """Build the agent and record the model it runs on for the saved turn and LLM context."""
        agent = AssistantService.build_agent(**kwargs)
        self._execution_model.record_agent(agent)
        return agent

    def _execution_llm_model(self, request: AssistantChatRequest) -> str | None:
        """The model this request runs on (see RequestExecutionModel)."""
        return self._execution_model.for_request(request)

    def _resolve_history_projection_mode(self, request: AssistantChatRequest) -> str:
        """Select the safest replay mode for the active assistant/model."""
        if BedrockOrchestratorService.is_bedrock_assistant(self.assistant):
            logger.debug(
                f"Selected conversation replay mode. AssistantId={self.assistant.id}, "
                f"ConversationId={request.conversation_id}, Mode={PLAIN_CHAT_MODE}"
            )
            return PLAIN_CHAT_MODE

        llm_model = self._execution_llm_model(request)
        if llm_model in llm_service.get_react_llms():
            logger.debug(
                f"Selected conversation replay mode. AssistantId={self.assistant.id}, "
                f"ConversationId={request.conversation_id}, LLMModel={llm_model}, Mode={TEXT_LEDGER_MODE}"
            )
            return TEXT_LEDGER_MODE

        logger.debug(
            f"Selected conversation replay mode. AssistantId={self.assistant.id}, "
            f"ConversationId={request.conversation_id}, LLMModel={llm_model}, Mode={NATIVE_TOOLS_MODE}"
        )
        return NATIVE_TOOLS_MODE

    def save_chat_history(self, data: ChatHistoryData) -> None:
        """Public method to save chat history"""
        if not data.request.save_history:
            logger.debug(
                f"Skipping chat history persistence for conversation_id={data.request.conversation_id} "
                f"(save_history=False)"
            )
            request_summary_manager.clear_summary(self.request_uuid)
            return

        # Re-set LLM context immediately before the metric is emitted.
        # save_chat_history may run in any thread/context-copy — e.g. in the
        # streaming path each next() call on the sync generator receives a fresh
        # copy_context() snapshot (anyio copies the async event-loop context per
        # iteration), so any litellm_context set in an earlier iteration is gone.
        # Resolving it here guarantees the correct billing project in the same
        # execution context as upsert_chat_history → send_conversation_metric.
        from codemie.service.llm_service.utils import set_llm_context

        summary = request_summary_manager.get_summary(self.request_uuid)
        tokens_usage = (summary.tokens_usage if summary else None) or TokensUsage(
            input_tokens=0, output_tokens=0, money_spent=0
        )
        execution_model = self._execution_llm_model(data.request)
        served_model = (tokens_usage.routing.routed_model if tokens_usage.routing else None) or execution_model
        set_llm_context(self.assistant, None, self.user, served_model)
        ConversationService.upsert_chat_history(
            UpsertChatHistoryParams(
                request=data.request,
                user=self.user,
                assistant_response=data.response,
                time_elapsed=time() - data.execution_start,
                tokens_usage=tokens_usage,
                llm_runs=summary.llm_runs if summary else None,
                assistant=self.assistant,
                thoughts=self._filter_thoughts(data.thoughts),
                status=data.status,
                user_message_received_at=data.user_message_received_at,
                a2ui_envelopes=data.a2ui_envelopes,
                request_id=self.request_uuid,
                background_tasks=self.background_tasks,
                in_progress=data.in_progress,
                client_source=self.client_source,
                llm_model=execution_model,
            )
        )
        if not data.in_progress:
            request_summary_manager.clear_summary(self.request_uuid)

    @staticmethod
    def _build_routing(thought: dict) -> RoutingInfo | None:
        """Build a RoutingInfo from a persisted thought dict's nested 'routing' sub-dict.

        Pre-migration persisted thoughts (flat keys) have no 'routing' sub-dict and
        yield None here — acceptable, no history migration.
        """
        routing = thought.get('routing') or {}
        if not routing:
            return None
        return RoutingInfo(
            routed_model=routing.get('routed_model'),
            routed_model_label=routing.get('routed_model_label')
            or llm_service.get_model_label(routing.get('routed_model')),
            classifier_cost_usd=routing.get('classifier_cost_usd'),
        )

    @staticmethod
    def _filter_thoughts(thoughts: List[Thought]):
        if not AssistantRequestHandler._is_conversation_replay_v2_enabled():
            return [
                Thought(
                    id=thought.get('id'),
                    parent_id=thought.get('parent_id'),
                    message=thought.get('message'),
                    author_name=thought.get('author_name'),
                    author_type=thought.get('author_type'),
                    children=thought.get('children') if thought.get('children') else [],
                    input_text=thought.get('input_text', ''),
                    error=thought.get('error', False),
                    aborted=thought.get('aborted', False),
                    interrupted=thought.get('interrupted', False),
                    routing=AssistantRequestHandler._build_routing(thought),
                )
                for thought in thoughts
                if thought.get('message', '') or thought.get('aborted', False) or thought.get('interrupted', False)
            ]

        return [
            Thought(
                id=thought.get('id'),
                parent_id=thought.get('parent_id'),
                message=thought.get('message'),
                author_name=thought.get('author_name'),
                author_type=thought.get('author_type'),
                children=thought.get('children') if thought.get('children') else [],
                input_text=thought.get('input_text', ''),
                error=thought.get('error', False),
                aborted=thought.get('aborted', False),
                interrupted=thought.get('interrupted', False),
                metadata=thought.get('metadata') or {},
                output_format=thought.get('output_format'),
                in_progress=thought.get('in_progress', False),
                routing=AssistantRequestHandler._build_routing(thought),
            )
            for thought in thoughts
            if (
                thought.get('message')
                or thought.get('input_text')
                or thought.get('error', False)
                or thought.get('aborted', False)
                or thought.get('interrupted', False)
                or (thought.get('metadata') or {}).get('replay_type') == TOOL_REPLAY_TYPE
            )
        ]

    @staticmethod
    def _is_conversation_replay_v2_enabled() -> bool:
        return DynamicConfigService.get_bool_value_safe(
            AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY,
            default=config.AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED,
        )

    def _get_queue_poll_timeout_seconds(self) -> int:
        return max(
            1,
            DynamicConfigService.get_typed_value_safe(
                AI_AGENT_QUEUE_POLL_TIMEOUT_SECONDS_KEY,
                int,
                default=config.AI_AGENT_QUEUE_POLL_TIMEOUT_SECONDS or config.ai_agent_queue_poll_timeout_default,
            ),
        )

    def _build_timeout_result(
        self,
        generator_queue: ThreadedGenerator,
        execution_start: float,
        include_tool_errors: bool,
        error_detail_level: ErrorDetailLevel,
        log_context: str,
    ) -> StreamedGenerationResult:
        """Build the final chunk for a queue.Empty timeout, shared by the standard,
        hedged and A2A streaming paths.

        Closes the queue so the still-running producer thread's future sends become
        no-ops instead of piling up in a queue nobody drains anymore.
        """
        logger.error(f"{log_context} queue timed out assistant_id={self.assistant.id}")
        generator_queue.close()
        error_response = handle_agent_exception(TimeoutError("Agent stream timed out waiting for the next chunk"))
        # Tracebacks never go to the client, whatever include_tool_errors or HIDE_AGENT_STREAMING_EXCEPTIONS say.
        error_response.redact_for_streaming()
        error = error_response.get_error()
        # Same short {error_code, message} agent_error and execution_error as LangGraphAgent._send_error_to_thread,
        # independent of include_tool_errors, so the client classifies a timeout like any other failed turn.
        return StreamedGenerationResult(
            generated=error.message,
            generated_chunk="",
            last=True,
            time_elapsed=time() - execution_start,
            success=False,
            execution_error=error.error_code.value,
            agent_error=AgentErrorDetails(error_code=error.error_code, message=error.message),
            error_details=error.model_dump(mode="json") if include_tool_errors and error else None,
        )

    @staticmethod
    def _format_errors(generation_result, include_tool_errors: bool, error_detail_level: ErrorDetailLevel):
        """
        Extract and format errors from generation result.

        Args:
            generation_result: Result from agent.generate() with error details
            include_tool_errors: Whether to include tool errors in response
            error_detail_level: Level of detail for error formatting

        Returns:
            Tuple of (tool_errors, agent_error) formatted for response
        """
        tool_errors = None
        if include_tool_errors and generation_result.tool_errors:
            tool_errors = [err.format_for_level(error_detail_level) for err in generation_result.tool_errors]

        # Only include agent_error when include_tool_errors is True (backward compatibility)
        agent_error = generation_result.agent_error if include_tool_errors else None

        return tool_errors, agent_error


class StandardAssistantHandler(AssistantRequestHandler):
    supports_tool_call_resume: ClassVar[bool] = True

    @pyroscope_profile(
        lambda self, request, *a, **kw: {
            "operation": "assistant",
            "assistant_id": self.assistant.id,
            "request_uuid": self.request_uuid,
        }
    )
    def process_request(
        self,
        request: AssistantChatRequest,
        background_tasks: BackgroundTasks,
        raw_request: Request,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> StreamingResponse | BaseModelResponse:
        """
        Process assistant request with error handling options.
        """
        self.background_tasks = background_tasks
        self._guard_conversation_not_finished(request)
        self._sync_uploaded_files_to_workspace(request)

        # Validate structured A2UI answers against the stored conversation
        self._validate_a2ui_action(request)

        # Populate conversation history if conversation_id is provided (also guards finished conversations)
        self._populate_conversation_history(request)

        execution_start = time()
        user_message_received_at = datetime.now(timezone.utc)
        if request.stream:
            return self._handle_stream(
                request, raw_request, execution_start, user_message_received_at, include_tool_errors, error_detail_level
            )
        elif request.background_task:
            return self._handle_background(
                request,
                background_tasks,
                raw_request,
                execution_start,
                user_message_received_at,
                include_tool_errors,
                error_detail_level,
            )
        else:
            return self._handle_sync(
                request, raw_request, execution_start, user_message_received_at, include_tool_errors, error_detail_level
            )

    def _handle_stream(
        self,
        request: "AssistantChatRequest | ToolCallResumeRequest",
        raw_request: Request,
        execution_start: float,
        user_message_received_at: datetime | None = None,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
        resume: ConsumedPendingToolCall | None = None,
    ) -> StreamingResponse:
        """
        Handle streaming assistant request.

        Supports error handling via include_tool_errors and error_detail_level parameters.
        Errors are included in the final streamed chunk when last=True.

        With ``resume`` (a consumed pending row) the turn is the resumed turn of that pause: ``request`` only
        names the conversation. Inside the ``try``, the resume branch takes the paused entry's request from the
        row, seeds the generator, restores the stored request, resolves the pinned version, saves the placeholder
        and builds on ``RESUME`` (rule 5.4); any raise is main's turn error (rule 5.5).
        """
        import uuid

        if not request.conversation_id:
            request.conversation_id = str(uuid.uuid4())

        generator_queue = ThreadedGenerator(
            request_uuid=self.request_uuid, user_id=self.user.id, conversation_id=request.conversation_id
        )

        from codemie.service.generation_manager import GenerationManager

        GenerationManager().register(request.conversation_id, generator_queue)

        try:
            if resume is None:
                self.save_chat_history(
                    ChatHistoryData(
                        execution_start=execution_start,
                        request=request,
                        response="",
                        thoughts=[],
                        status=ConversationStatus.SUCCESS,
                        user_message_received_at=user_message_received_at,
                        in_progress=True,
                    )
                )
                logger.debug(
                    f"Initial prompt persisted with in_progress=True for conversation_id={request.conversation_id}"
                )
        except Exception as persist_exc:
            logger.error(f"Error persisting initial prompt in_progress: {str(persist_exc)}")

        raw_request.state.on_disconnect(
            lambda: self._handle_client_disconnect(
                request=request,
                threaded_generator=generator_queue,
                execution_start=execution_start,
                user_message_received_at=user_message_received_at,
            )
        )

        try:
            if resume is not None:
                # The paused entry's request, from the row's columns; it cannot raise (rule 5.4)
                request = resumed_turn_request(request.conversation_id, resume.record)
            request_headers = extract_custom_headers(raw_request)

            # Set cache control flag for this request
            set_disable_prompt_cache(request.disable_cache or False)

            if resume is None:
                agent = self._build_agent(
                    assistant=self.assistant,
                    request=request,
                    user=self.user,
                    request_uuid=raw_request.state.uuid,
                    thread_generator=generator_queue,
                    request_headers=request_headers,
                    channel=BuildChannel.STREAM,
                )
                stream = partial(
                    agent.stream, include_tool_errors=include_tool_errors, error_detail_level=error_detail_level
                )
            else:
                start = begin_resume(self, request, generator_queue, resume, execution_start, user_message_received_at)
                agent = build_resumed_agent(self, request, generator_queue, start)
                stream = partial(
                    agent.resume_from_interrupt,
                    start.decisions,
                    include_tool_errors=include_tool_errors,
                    error_detail_level=error_detail_level,
                )
            wrapped_stream = self._serve_data(
                stream,
                generator_queue,
                request,
                execution_start,
                agent,
                include_tool_errors,
                error_detail_level,
                user_message_received_at,
            )

            return StreamingResponse(
                content=wrapped_stream,
                media_type=NDJSON_MEDIA_TYPE,
            )
        except Exception as e:
            from codemie.service.generation_manager import GenerationManager

            GenerationManager().unregister(request.conversation_id, generator_queue)

            from codemie.core.template_security import TemplateSecurityError

            if isinstance(e, TemplateSecurityError):  # Return security error as a thought without calling LLM
                return self._return_security_error_response(
                    str(e), generator_queue, request, execution_start, user_message_received_at
                )

            generator_queue.close()
            try:
                self.save_chat_history(
                    ChatHistoryData(
                        execution_start=execution_start,
                        request=request,
                        response="",
                        thoughts=generator_queue.thoughts,
                        status=ConversationStatus.ERROR,
                        user_message_received_at=user_message_received_at,
                        in_progress=False,
                    )
                )
            except Exception as save_err:
                logger.error(f"Error persisting failure in _handle_stream: {str(save_err)}")
            raise

    def _handle_client_disconnect(
        self,
        request: AssistantChatRequest,
        threaded_generator: ThreadedGenerator,
        execution_start,
        user_message_received_at: datetime | None = None,
    ):
        """
        Passive client disconnect handler. We don't close the generator here as
        page refresh and temporary network fluctuations should continue generating in the background.
        """
        logger.debug(f"Client transport disconnected (passive) for conversation_id={request.conversation_id}")

    @staticmethod
    def _read_streamed_chunk(value: str, response, a2ui_envelopes: List[dict]):
        """Interpret one streamed chunk, returning the result the history should record.

        Two things are read out of the same line: the latest generated result, and any A2UI
        envelope, which is appended rather than replacing anything — one interactive surface
        arrives as several ordered envelopes and the turn keeps all of them.
        """
        try:
            generation_result = json.loads(value, object_hook=lambda d: SimpleNamespace(**d))
        except json.JSONDecodeError:
            logger.warning(f"Skipping malformed streamed chunk (invalid JSON): {value!r}")
            return response
        if getattr(generation_result, "a2ui", None) is not None:
            # Re-parse as a plain dict — envelopes are persisted raw (versioned)
            a2ui_envelopes.append(json.loads(value)["a2ui"])
        return generation_result if generation_result.generated is not None else response

    @staticmethod
    def _run_stream(stream, generator_queue: ThreadedGenerator) -> None:
        try:
            stream()
        except MCPAuthenticationRequiredException as exc:
            generator_queue.close(exc)

    def _start_stream(self, stream, generator_queue: ThreadedGenerator) -> None:
        run_assistant_in_thread_pool(partial(self._run_stream, stream, generator_queue))

    def _handle_serve_error(
        self,
        execution_start: float,
        request,
        response: StreamedGenerationResult,
        a2ui_envelopes: List[dict],
        user_message_received_at: datetime | None,
        generator_queue: ThreadedGenerator,
    ) -> None:
        generator_queue.queue.task_done()
        try:
            self.save_chat_history(
                ChatHistoryData(
                    execution_start=execution_start,
                    request=request,
                    response=response.generated,
                    thoughts=generator_queue.thoughts,
                    status=ConversationStatus.ERROR,
                    user_message_received_at=user_message_received_at,
                    a2ui_envelopes=a2ui_envelopes or None,
                    in_progress=False,
                )
            )
        except Exception as save_err:
            logger.error(f"Error saving failure state in _serve_data: {str(save_err)}")

    def _handle_serve_success(
        self,
        execution_start: float,
        request,
        response: StreamedGenerationResult,
        a2ui_envelopes: List[dict],
        user_message_received_at: datetime | None,
        generator_queue: ThreadedGenerator,
        agent,
    ) -> None:
        if generator_queue.cancellation_reason == CancellationReason.ABORTED_BY_USER:
            status = ConversationStatus.INTERRUPTED
        elif getattr(response, "success", None) is False:
            # The stream ended normally, but its last chunk reported a failed run (see _send_error_to_thread).
            status = ConversationStatus.ERROR
        else:
            status = ConversationStatus.SUCCESS
        self.save_chat_history(
            ChatHistoryData(
                execution_start=execution_start,
                request=request,
                response=response.generated if response else "",
                thoughts=finalize_thoughts(generator_queue.thoughts),
                status=status,
                user_message_received_at=user_message_received_at,
                a2ui_envelopes=a2ui_envelopes or None,
                in_progress=False,
            )
        )

    def _serve_data(
        self,
        stream,
        generator_queue: ThreadedGenerator,
        request,
        execution_start,
        agent=None,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
        user_message_received_at: datetime | None = None,
    ):
        # Assistant execution runs in a separate pool so assistant streams and
        # background generation do not contend with workflow producers/consumers.
        self._start_stream(stream, generator_queue)

        # We pass an empty string to avoid sending the default None value in the chat history.
        response = StreamedGenerationResult(generated="")
        # One interactive surface streams as 2-3 A2UI envelopes (createSurface →
        # updateComponents → [updateDataModel]); accumulate the whole ordered turn.
        a2ui_envelopes: List[dict] = []
        is_draining = False
        # Read once per request: the dynamic-config lookup opens a DB session, too costly per chunk.
        queue_poll_timeout = self._get_queue_poll_timeout_seconds()
        try:
            while True:
                try:
                    value = generator_queue.queue.get(timeout=queue_poll_timeout)
                except queue.Empty:
                    timeout_result = self._build_timeout_result(
                        generator_queue,
                        execution_start,
                        include_tool_errors,
                        error_detail_level,
                        log_context="[STANDARD] generator",
                    )
                    yield timeout_result.model_dump_json()
                    self.save_chat_history(
                        ChatHistoryData(
                            execution_start=execution_start,
                            request=request,
                            response=timeout_result.generated,
                            thoughts=finalize_thoughts(generator_queue.thoughts),
                            status=ConversationStatus.ERROR,
                            user_message_received_at=user_message_received_at,
                            a2ui_envelopes=a2ui_envelopes or None,
                        )
                    )
                    break
                if isinstance(value, BaseException):
                    self._handle_serve_error(
                        execution_start,
                        request,
                        response,
                        a2ui_envelopes,
                        user_message_received_at,
                        generator_queue,
                    )
                    raise value
                if value is not StopIteration:
                    response = self._read_streamed_chunk(value, response, a2ui_envelopes)
                    yield value
                    generator_queue.queue.task_done()
                    continue

                self._handle_serve_success(
                    execution_start,
                    request,
                    response,
                    a2ui_envelopes,
                    user_message_received_at,
                    generator_queue,
                    agent,
                )
                generator_queue.queue.task_done()
                break
        except GeneratorExit:
            # Client disconnected (passive refresh/network issue). Hand off to background drainer.
            is_draining = True
            logger.debug(
                f"GeneratorExit caught in _serve_data. Starting background drainer "
                f"for conversation_id={request.conversation_id}"
            )
            run_assistant_in_thread_pool(
                self._drain_and_save_chat_history,
                generator_queue,
                request,
                execution_start,
                response,
                a2ui_envelopes,
                user_message_received_at,
                agent,
            )
            raise
        finally:
            if not is_draining:
                from codemie.service.generation_manager import GenerationManager

                GenerationManager().unregister(request.conversation_id, generator_queue)

    def _drain_queue_items(
        self,
        generator_queue: ThreadedGenerator,
        last_response: StreamedGenerationResult,
        a2ui_envelopes: List[dict],
    ) -> StreamedGenerationResult:
        response = last_response
        queue_poll_timeout = self._get_queue_poll_timeout_seconds()
        while True:
            try:
                value = generator_queue.queue.get(timeout=queue_poll_timeout)
            except queue.Empty:
                # Abandoned producer: stop its future sends and let the caller persist the turn as failed.
                generator_queue.close()
                raise TimeoutError("Agent stream timed out waiting for the next chunk")
            if isinstance(value, BaseException):
                generator_queue.queue.task_done()
                raise value
            if value is StopIteration:
                generator_queue.queue.task_done()
                break
            response = self._read_streamed_chunk(value, response, a2ui_envelopes)
            generator_queue.queue.task_done()
        return response

    def _handle_drain_error(
        self,
        exc: Exception,
        generator_queue: ThreadedGenerator,
        request,
        execution_start,
        response: StreamedGenerationResult,
        a2ui_envelopes: List[dict],
        user_message_received_at: datetime | None,
    ) -> None:
        logger.error(f"Error in background drainer for conversation_id={request.conversation_id}: {str(exc)}")
        try:
            self.save_chat_history(
                ChatHistoryData(
                    execution_start=execution_start,
                    request=request,
                    response=response.generated if response else "",
                    thoughts=generator_queue.thoughts,
                    status=ConversationStatus.ERROR,
                    user_message_received_at=user_message_received_at,
                    a2ui_envelopes=a2ui_envelopes or None,
                    in_progress=False,
                )
            )
        except Exception as save_err:
            logger.error(
                f"Failed to persist error status in drainer for conversation_id={request.conversation_id}: "
                f"{str(save_err)}"
            )

    def _drain_and_save_chat_history(
        self,
        generator_queue: ThreadedGenerator,
        request,
        execution_start,
        last_response,
        a2ui_envelopes,
        user_message_received_at: datetime | None = None,
        agent=None,
    ):
        try:
            logger.debug(f"Background drainer started for conversation_id={request.conversation_id}")
            if a2ui_envelopes is None:
                a2ui_envelopes = []

            response = self._drain_queue_items(generator_queue, last_response, a2ui_envelopes)

            logger.debug(f"Background drainer completed for conversation_id={request.conversation_id}, saving history.")
            status = (
                ConversationStatus.INTERRUPTED
                if generator_queue.cancellation_reason == CancellationReason.ABORTED_BY_USER
                else ConversationStatus.SUCCESS
            )
            self.save_chat_history(
                ChatHistoryData(
                    execution_start=execution_start,
                    request=request,
                    response=response.generated if response else "",
                    thoughts=finalize_thoughts(generator_queue.thoughts),
                    status=status,
                    user_message_received_at=user_message_received_at,
                    a2ui_envelopes=a2ui_envelopes or None,
                    in_progress=False,
                )
            )
        except Exception as e:
            self._handle_drain_error(
                e,
                generator_queue,
                request,
                execution_start,
                last_response,
                a2ui_envelopes,
                user_message_received_at,
            )
        finally:
            from codemie.service.generation_manager import GenerationManager

            GenerationManager().unregister(request.conversation_id, generator_queue)

    def _return_security_error_response(
        self,
        error_message: str,
        generator_queue: ThreadedGenerator,
        request: AssistantChatRequest,
        execution_start: float,
        user_message_received_at: datetime | None = None,
    ) -> StreamingResponse:
        """
        Return a security error as a thought without calling the LLM.

        Args:
            error_message: The security error message to return
            generator_queue: The threaded generator queue
            request: The chat request
            execution_start: The execution start time

        Returns:
            StreamingResponse with the error thought
        """
        from codemie.chains.base import ThoughtAuthorType

        # Create an error thought
        error_thought = Thought(
            id=str(uuid.uuid4()),
            message=error_message,
            author_type=ThoughtAuthorType.System,
            author_name="Security Validator",
            error=True,
            in_progress=False,
        )

        # Add the thought to generator_queue so it's available for disconnect handler
        generator_queue.thoughts.append(error_thought.model_dump())

        # Create a StreamedGenerationResult with the error thought and last=True
        result = StreamedGenerationResult(
            thought=error_thought,
            generated="",  # No generated content since we didn't call LLM
            last=True,
            time_elapsed=time() - execution_start,
        )

        # Create a generator that yields the error thought and saves history
        def error_generator():
            try:
                yield result.model_dump_json() + "\n"

                # Save to history with error status after yielding
                # Convert Thought object to dict for compatibility with _filter_thoughts
                self.save_chat_history(
                    ChatHistoryData(
                        execution_start=execution_start,
                        request=request,
                        response="",  # No response since we didn't call LLM
                        thoughts=[error_thought.model_dump()],
                        status=ConversationStatus.ERROR,
                        user_message_received_at=user_message_received_at,
                    )
                )
            except Exception as e:
                logger.error(f"Error in security error generator: {str(e)}", exc_info=True)

        return StreamingResponse(
            content=error_generator(),
            media_type=NDJSON_MEDIA_TYPE,
        )

    def _handle_background(
        self,
        request: AssistantChatRequest,
        background_tasks: BackgroundTasks,
        raw_request: Request,
        execution_start: float,
        user_message_received_at: datetime | None = None,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> BaseModelResponse:
        """
        Handle background task assistant request.

        Note: Error handling parameters not yet implemented for background tasks.
        """
        background_task_id = BackgroundTasksService().save(
            BackgroundTaskRequest(
                task=request.text,
                user=self.user.as_user_model(),
                assistant=AssistantDetails(id=self.assistant.id, name=self.assistant.name),
            )
        )
        # The worker builds the agent on a pool thread that copies no context, and the background
        # task itself runs in the outer request context where the router's origin block never
        # applied - so the worker is handed a snapshot of this context, the way the chat-naming task
        # is, and the run's models read the same origin the first turn would have.
        context = contextvars.copy_context()
        background_tasks.add_task(
            run_assistant_in_thread_pool,
            context.run,
            self._background_generate,
            request,
            background_task_id,
            raw_request,
            execution_start,
            include_tool_errors,
            error_detail_level,
            user_message_received_at,
        )

        return BaseModelResponse(
            generated="Task is running in the background", time_elapsed=0, task_id=background_task_id
        )

    def _background_generate(
        self,
        request: AssistantChatRequest,
        background_task_id: str,
        raw_request: Request,
        execution_start: float,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
        user_message_received_at: datetime | None = None,
    ):
        """
        Background worker for assistant generation.

        Supports error handling when include_tool_errors is True.
        Errors are logged and can be retrieved via background task status.
        """
        request_uuid = raw_request.state.uuid

        request_headers = extract_custom_headers(raw_request)

        # Set cache control flag for this request
        set_disable_prompt_cache(request.disable_cache or False)

        agent = self._build_agent(
            assistant=self.assistant,
            request=request,
            user=self.user,
            request_uuid=request_uuid,
            request_headers=request_headers,
            channel=BuildChannel.BACKGROUND,
        )
        generation_result = agent.generate(background_task_id)

        # Handle both GenerationResult object and raw response
        if hasattr(generation_result, "generated"):
            response = generation_result.generated
            # Extract and log errors if available
            if include_tool_errors:
                tool_errors, agent_error = self._format_errors(
                    generation_result, include_tool_errors, error_detail_level
                )
                if tool_errors:
                    logger.warning(f"Background task {background_task_id} tool errors: {tool_errors}")
                if agent_error:
                    logger.warning(f"Background task {background_task_id} agent error: {agent_error}")
        else:
            response = generation_result

        thoughts = finalize_thoughts(agent.get_thoughts_from_callback())
        self.save_chat_history(
            ChatHistoryData(
                execution_start=execution_start,
                request=request,
                response=response,
                thoughts=thoughts,
                user_message_received_at=user_message_received_at,
            )
        )
        return response

    @staticmethod
    def _cast_llm_response_to_string(response: str | dict | BaseModel) -> str:
        if isinstance(response, dict):
            response = json.dumps(response)
        elif isinstance(response, BaseModel):
            response = response.model_dump_json()
        return response

    def _handle_sync(
        self,
        request: AssistantChatRequest,
        raw_request: Request,
        execution_start: float,
        user_message_received_at: datetime | None = None,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> BaseModelResponse:
        """
        Handle synchronous (non-streaming) assistant request.
        """
        request_headers = extract_custom_headers(raw_request)

        # Set cache control flag for this request
        set_disable_prompt_cache(request.disable_cache or False)

        agent = self._build_agent(
            assistant=self.assistant,
            request=request,
            user=self.user,
            request_uuid=raw_request.state.uuid,
            request_headers=request_headers,
            channel=BuildChannel.SYNC,
        )
        generation_result = agent.generate()
        response = generation_result.generated
        string_response = self._cast_llm_response_to_string(response)
        # Output of structured AgentExecutor with tools is string, so we parse it to dict
        if request.output_schema and isinstance(response, str):
            response = json.loads(response)
        time_elapsed = time() - execution_start
        thoughts = finalize_thoughts(agent.get_thoughts_from_callback())
        self.save_chat_history(
            ChatHistoryData(
                execution_start=execution_start,
                request=request,
                response=string_response,
                thoughts=thoughts,
                user_message_received_at=user_message_received_at,
            )
        )

        # Extract formatted errors using helper method
        tool_errors, agent_error = self._format_errors(generation_result, include_tool_errors, error_detail_level)

        return BaseModelResponse(
            generated=response,
            time_elapsed=time_elapsed,
            thoughts=self._filter_thoughts(thoughts),
            success=generation_result.success,
            agent_error=agent_error,
            tool_errors=tool_errors,
        )

    def handle_tool_call_resume(
        self,
        request: "ToolCallResumeRequest",
        conversation: Conversation,
        pending: ConsumedPendingToolCall,
        raw_request: Request,
    ) -> StreamingResponse:
        """Run the consumed pause as an ordinary turn of the standard stream; a resume never hedges (rule 5.3)."""
        return StandardAssistantHandler._handle_stream(
            self, request, raw_request, time(), datetime.now(timezone.utc), resume=pending
        )


class A2AAssistantHandler(AssistantRequestHandler):
    supports_tool_call_resume: ClassVar[bool] = False

    def __init__(self, assistant: Assistant, user: User, request_uuid: str):
        super().__init__(assistant, user, request_uuid)
        self.agent_card = assistant.agent_card
        try:
            self.remote_connection = RemoteAgentConnections(
                agent_card=self.agent_card,
            )
        except ValueError as e:
            raise self._unusable_card_error(e) from e

    @staticmethod
    def _unusable_card_error(e: ValueError) -> ExtendedHTTPException:
        return ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Remote A2A agent is not usable",
            details=str(e),
            help="Re-register the remote agent so its card declares a JSON-RPC interface (A2A protocol v1.0).",
        )

    @pyroscope_profile(
        lambda self, request, *a, **kw: {
            "operation": "assistant",
            "assistant_id": self.assistant.id,
            "request_uuid": self.request_uuid,
        }
    )
    def process_request(
        self,
        request: AssistantChatRequest,
        background_tasks: BackgroundTasks,
        raw_request: Request,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> StreamingResponse | BaseModelResponse:
        """
        Process request for remote A2A assistant.

        Note: Error handling parameters not yet implemented for A2A.
        """
        self.background_tasks = background_tasks
        self._guard_conversation_not_finished(request)
        self._sync_uploaded_files_to_workspace(request)

        # Validate structured A2UI answers against the stored conversation
        self._validate_a2ui_action(request)

        # Populate conversation history if conversation_id is provided
        self._populate_conversation_history(request)

        task_request = convert_to_task_request(request, raw_request)
        try:
            agent_url = get_jsonrpc_url(self.agent_card)
        except AgentCardInterfaceError as e:
            raise self._unusable_card_error(e) from e
        logger.debug(f"Call agent. Agent: {self.agent_card.name}. Url: {agent_url}. Request: {task_request}")
        if self.agent_card.capabilities.streaming:
            return self._handle_a2a_stream(request, task_request, include_tool_errors, error_detail_level)
        else:
            return self._handle_a2a_sync(request, task_request)

    def _handle_a2a_stream(
        self,
        request: AssistantChatRequest,
        task_request: SendStreamingMessageRPCRequest,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> StreamingResponse:
        execution_start = time()

        # Create a ThreadedGenerator for handling the streaming responses
        generator_queue = ThreadedGenerator(
            request_uuid=self.request_uuid, user_id=self.user.id, conversation_id=request.conversation_id
        )

        answer_streamed = False
        remote_responded = threading.Event()
        version_error: list[A2AProtocolVersionError] = []

        # Define a callback that will format and send responses to the generator_queue
        def task_callback(task_arg: TaskCallbackArg, agent_card: AgentCard):
            nonlocal answer_streamed
            status_ = getattr(task_arg, "status", None)
            # RemoteAgentConnections first reports a local SUBMITTED placeholder carrying the outgoing message
            if not (status_ and status_.message is task_request.params.message):
                remote_responded.set()
            response_data = self._format_stream_update(task_arg, execution_start, answer_streamed)
            if response_data and response_data.generated_chunk:
                answer_streamed = True
            logger.debug(
                f"Streaming response from agent. Agent: {agent_card.name}. "
                f"Response: {response_data}. "
                f"TaskArg: {task_arg}"
            )
            # Send the formatted response to the generator queue
            if response_data:
                generator_queue.send(response_data.model_dump_json())
            if is_final_update(task_arg):
                if response_data and not response_data.last and response_data.thought and not answer_streamed:
                    # Workaround to extract final thoughts to GenerationResult if agent require additional input
                    generator_queue.send(
                        StreamedGenerationResult(
                            generated=response_data.thought.message,
                            time_elapsed=time() - execution_start,
                            generated_chunk="",
                            last=True,
                        ).model_dump_json()
                    )
                generator_queue.close()
            return task_arg

        def run_remote() -> None:
            try:
                asyncio.run(self.remote_connection.send_task(task_request.params, task_callback))
            except A2AProtocolVersionError as e:
                logger.error(f"Remote A2A agent rejected the protocol version. Agent: {self.agent_card.name}. {e}")
                if remote_responded.is_set():
                    self._send_stream_error(generator_queue, e, execution_start)
                else:
                    version_error.append(e)
            except A2AClientError as e:
                logger.error(f"Remote A2A agent call failed. Agent: {self.agent_card.name}. Error: {e}")
                self._send_stream_error(generator_queue, e, execution_start)
            except Exception as e:
                logger.error(f"Unexpected error running remote A2A agent. Agent: {self.agent_card.name}. Error: {e}")
                self._send_stream_error(generator_queue, e, execution_start)
            finally:
                # A remote stream may end without a final update; never leave the consumer waiting.
                generator_queue.close()
                remote_responded.set()

        # The remote call runs on its own thread so chunks reach the UI while it runs and the server loop is free.
        threading.Thread(target=contextvars.copy_context().run, args=(run_remote,), daemon=True).start()

        # Wait for the first remote frame (or the end of the call): a version rejection arrives before any frame,
        # while an HTTP status can still be returned.
        remote_responded.wait()
        if version_error:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Remote A2A agent does not support the required protocol version",
                details=str(version_error[0]),
                help="The remote agent must implement A2A protocol v1.0 (JSON-RPC binding).",
            ) from version_error[0]

        return StreamingResponse(
            content=self._drain_stream(
                generator_queue, request, execution_start, include_tool_errors, error_detail_level
            ),
            media_type=NDJSON_MEDIA_TYPE,
        )

    async def _drain_stream(
        self,
        generator_queue: ThreadedGenerator,
        request: AssistantChatRequest,
        execution_start: float,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ):
        """Yield queued chat chunks as they arrive, then persist the chat history."""
        response = StreamedGenerationResult()
        answer_chunks: list[str] = []
        final_sent = False
        queue_poll_timeout = self._get_queue_poll_timeout_seconds()
        while True:
            try:
                value = await asyncio.to_thread(generator_queue.queue.get, timeout=queue_poll_timeout)
            except queue.Empty:
                yield self._finish_timed_out_stream(
                    generator_queue, request, execution_start, include_tool_errors, error_detail_level
                )
                break
            if value is StopIteration:
                final_chunk = self._finish_stream(
                    generator_queue, request, execution_start, response, answer_chunks, final_sent
                )
                if final_chunk:
                    yield final_chunk
                break
            generation_result = json.loads(value, object_hook=lambda d: SimpleNamespace(**d))
            if generation_result.generated_chunk:
                answer_chunks.append(generation_result.generated_chunk)
            if generation_result.generated is not None:
                response = generation_result
            final_sent = final_sent or bool(generation_result.last)
            yield value
            generator_queue.queue.task_done()

    def _finish_timed_out_stream(
        self,
        generator_queue: ThreadedGenerator,
        request: AssistantChatRequest,
        execution_start: float,
        include_tool_errors: bool,
        error_detail_level: ErrorDetailLevel,
    ) -> str:
        """Save the timed-out turn as ERROR and return the terminal timeout chunk."""
        timeout_result = self._build_timeout_result(
            generator_queue,
            execution_start,
            include_tool_errors,
            error_detail_level,
            log_context="[A2A] generator",
        )
        self.save_chat_history(
            ChatHistoryData(
                execution_start=execution_start,
                request=request,
                response=timeout_result.generated,
                thoughts=finalize_thoughts(generator_queue.thoughts),
                status=ConversationStatus.ERROR,
            )
        )
        return timeout_result.model_dump_json()

    def _finish_stream(
        self,
        generator_queue: ThreadedGenerator,
        request: AssistantChatRequest,
        execution_start: float,
        response: StreamedGenerationResult,
        answer_chunks: list[str],
        final_sent: bool,
    ) -> str | None:
        """Persist the chat history; return the closing chunk when none was streamed yet."""
        final_chunk = None
        if answer_chunks and not final_sent:
            # The UI ends the message at the first `last` chunk, so send exactly one, at the end
            response = StreamedGenerationResult(
                generated="".join(answer_chunks),
                generated_chunk="",
                time_elapsed=time() - execution_start,
                last=True,
            )
            final_chunk = response.model_dump_json()
        self.save_chat_history(
            ChatHistoryData(
                execution_start=execution_start,
                request=request,
                response=response.generated,
                thoughts=finalize_thoughts(generator_queue.thoughts),
            )
        )
        return final_chunk

    @staticmethod
    def _send_stream_error(generator_queue: ThreadedGenerator, error: Exception, execution_start: float) -> None:
        generator_queue.send(
            StreamedGenerationResult(
                generated=f"Error: {error}",
                time_elapsed=time() - execution_start,
                generated_chunk="",
                last=True,
            ).model_dump_json()
        )

    def _format_stream_update(
        self, task_arg: TaskCallbackArg, execution_start: float, answer_streamed: bool = False
    ) -> StreamedGenerationResult | None:
        """Map a remote update to a chat chunk: status text is a thought, artifact / message text is the answer."""
        if isinstance(task_arg, Message):
            return self._answer_chunk(self._text_parts(task_arg.parts), execution_start, answer_streamed)
        if isinstance(task_arg, TaskArtifactUpdateEvent):
            return self._answer_chunk(
                self._text_parts(task_arg.artifact.parts),
                execution_start,
                answer_streamed and not task_arg.append,
            )

        status_ = task_arg.status
        if status_.message:
            text_parts = self._extract_text_parts_from_message(task_arg)
            if text_parts:
                thought_message = " ".join(text_parts)
                input_text = ""
                if status_.state == TaskState.SUBMITTED:
                    input_text = thought_message
                    thought_message = ""
                return StreamedGenerationResult(
                    thought=Thought(
                        id=str(uuid.uuid4()),
                        author_name=self.agent_card.name,
                        author_type="Agent",
                        in_progress=True,
                        message=thought_message,
                        input_text=input_text,
                    )
                )
        if isinstance(task_arg, Task) and task_arg.artifacts and is_final_update(task_arg) and not answer_streamed:
            # A final Task snapshot carries the answer as artifacts, unless they already streamed
            return self._answer_chunk(
                [text for artifact in task_arg.artifacts for text in self._text_parts(artifact.parts)],
                execution_start,
                False,
            )
        return None

    @staticmethod
    def _answer_chunk(texts: list[str], execution_start: float, separate: bool) -> StreamedGenerationResult | None:
        """An incremental answer chunk; `last` is sent once, when the stream ends."""
        if not texts:
            return None
        text = " ".join(texts)
        return StreamedGenerationResult(
            generated_chunk=f"\n\n{text}" if separate else text,
            time_elapsed=time() - execution_start,
            last=False,
        )

    @staticmethod
    def _text_parts(parts) -> list[str]:
        return [part.text for part in parts if part.text is not None]

    def _extract_text_parts_from_message(self, task_arg: TaskCallbackArg) -> list[str]:
        status_ = getattr(task_arg, "status", None)
        if status_ and status_.message and status_.message.parts:
            return self._text_parts(status_.message.parts)
        return []

    def _handle_a2a_sync(self, request: AssistantChatRequest, task_request: SendMessageRPCRequest) -> BaseModelResponse:
        execution_start = time()
        try:
            result = asyncio.run(self.remote_connection.send_task(task_request.params, None))
        except A2AProtocolVersionError as e:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Remote A2A agent does not support the required protocol version",
                details=str(e),
                help="The remote agent must implement A2A protocol v1.0 (JSON-RPC binding).",
            ) from e
        except A2AClientError as e:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Remote A2A agent call failed",
                details=str(e),
            ) from e
        if result is None:
            raise ExtendedHTTPException(
                code=status.HTTP_502_BAD_GATEWAY,
                message="Remote A2A agent returned no result",
                details="The remote agent answered neither with a task nor with a message.",
            )

        model_response = convert_to_base_model_response(result)
        self.save_chat_history(
            ChatHistoryData(
                execution_start=execution_start, request=request, response=model_response.generated, thoughts=[]
            )
        )
        return model_response


def get_request_handler(assistant: Assistant, user: User, request_uuid: str) -> AssistantRequestHandler:
    """Factory function to create appropriate handler based on assistant type"""
    if assistant.type == AssistantType.A2A:
        return A2AAssistantHandler(assistant, user, request_uuid)
    if assistant.hedging_config is not None and customer_config.is_feature_enabled("requestHedging"):
        from codemie.rest_api.handlers.hedged_handler import HedgedAssistantHandler

        return HedgedAssistantHandler(assistant, user, request_uuid)
    return StandardAssistantHandler(assistant, user, request_uuid)
