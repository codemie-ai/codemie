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

import json
import re
from collections import deque
from time import time
from typing import Annotated, Any, List, Optional
import uuid
from uuid import UUID
import hashlib

from codemie.agents.supervisor.bootstrap import ensure_langgraph_supervisor_compatibility
from codemie.agents.supervisor.coordinator import (
    HandoffRunBinding,
    PendingHandoff,
    SupervisorCoordinator,
    _SupervisorChunkContext,
    _SupervisorHandoffTracker,
)
from codemie.agents.supervisor.constants import (
    METADATA_KEY_HANDOFF_DESTINATION,
    METADATA_KEY_PARALLEL_SUBAGENT_PARENT_HANDOFF,
    METADATA_KEY_SUBAGENT_TASK,
)
from codemie.agents.supervisor.history import (
    PARALLEL_SUBAGENT_HANDOFF_ACK_KEY,
    _strip_handoff_back_messages_pre_model_hook,
    _strip_subagent_task_messages_pre_model_hook,
    _subagent_task_pre_model_hook,
)
from codemie.agents.supervisor.pre_model_hooks import (
    _compose_pre_model_hooks,
    _image_artifact_pre_model_hook,
)
from codemie.agents.langgraph_event_adapter import LangGraphCallbackBridge, LangGraphEventAdapter
from codemie.agents.tools.agent import WorkspaceAwareAgent
from codemie.core.errors import ErrorResponse
from codemie.enterprise.litellm.proxy_router import handle_agent_exception
from codemie_tools.base.file_object import FileObject
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool as langchain_tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, Send
from langgraph_supervisor import create_supervisor
from pydantic import BaseModel, Field

from codemie.agents.agent_runtime_utils import (
    filter_history,
    is_unique_callback,
    serialize_inputs,
    serialize_messages,
    serialize_response,
    serialize_tool_calls,
    transform_history,
    truncate_log_value,
)
from codemie.agents.agent_callback_factory import build_supervisor_callbacks, build_tool_callbacks
from codemie.agents.assistant_agent import TaskResult
from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.agents.callbacks.tool_error_capture_callback import ToolErrorCaptureCallback
from codemie.agents.smart_react_agent import create_smart_react_agent
from codemie.agents.utils import suppress_stdout, validate_json_schema
from codemie.agents.utils import get_run_config, ExecutionErrorEnum
from codemie.chains.base import StreamedGenerationResult, GenerationResult, ThoughtAuthorType
from codemie.chains.pure_chat_chain import PureChatChain
from codemie.configs import Config, config
from codemie.configs.logger import logger, set_logging_info
from codemie.core.constants import (
    BackgroundTaskStatus,
    REQUEST_ID,
    USER_ID,
    USER_NAME,
    LLM_MODEL,
    AGENT_NAME,
    ASSISTANT_ID,
    PROJECT,
    OUTPUT_FORMAT,
    SUPERVISOR_HANDOFF_TOOL_PREFIX,
)
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.models import ChatMessage, AssistantChatRequest
from codemie.core.thread import ThreadedGenerator
from codemie.core.utils import extract_text_from_llm_output, calculate_tokens, unpack_json_strings
from codemie.rest_api.models.assistant import AssistantBase
from codemie.rest_api.security.user import User
from codemie.service.background_tasks_service import BackgroundTasksService
from codemie.service.conversation.history_compaction_service import ConversationHistoryCompactionService
from codemie.service.constants import AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY
from codemie.service.dynamic_config_service import DynamicConfigService
from codemie.service.file_service.image_service import ImageService
from codemie.service.llm_service.llm_service import LLMService
from codemie.service.llm_service.utils import set_llm_context
from codemie.templates.agents.assistant_base import markdown_response_prompt
from codemie.core.exceptions import MCPAuthenticationRequiredException, TokenLimitExceededException
from codemie.core.otel_tracing import get_otel_context_for_thread, propagated_span, record_exception_on_span, traced

__all__ = ["LangGraphAgent", "_SupervisorChunkContext", "_SupervisorHandoffTracker"]


class LangGraphAgent(WorkspaceAwareAgent):
    # When this agent is run as part of a workflow (instead of natively within LangGraph),
    # LangGraph overrides the max_concurrency of all subgraphs.
    # This is problematic because the agent's execution
    # should remain independent when instance methods are used.
    MAX_CONCURRENCY = 10000
    SUPERVISOR_HANDOFF_TOOL_PREFIX = SUPERVISOR_HANDOFF_TOOL_PREFIX
    # Maximum allowed length for assistant (agent) name after normalization
    ASSISTANT_NAME_MAX_LENGTH = 64

    @staticmethod
    def _is_conversation_replay_v2_enabled() -> bool:
        return DynamicConfigService.get_bool_value_safe(
            AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY,
            default=config.AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED,
        )

    def __init__(
        self,
        agent_name: str,
        description: str,
        tools: list[BaseTool],
        request: AssistantChatRequest,
        system_prompt: str,
        request_uuid: str,
        user: User,
        llm_model: str,
        output_schema: Optional[dict | BaseModel] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        is_react: bool = False,
        callbacks=None,
        supervisor_callbacks: Optional[list[BaseCallbackHandler]] = None,
        verbose: bool = config.verbose,
        recursion_limit: int = config.AI_AGENT_RECURSION_LIMIT,
        thread_generator: ThreadedGenerator = None,
        stream_steps: bool = True,
        handle_tool_error: bool = True,
        throw_truncated_error: bool = False,
        assistant: Optional[AssistantBase] = None,
        override_global_checkpointer: bool = True,
        smart_tool_selection_enabled: Optional[bool] = False,
        tool_selection_limit: Optional[int] = None,
        subagents: Optional[list] = None,
        subagent_descriptions: Optional[dict[str, str]] = None,
        trace_context=None,  # For workflow trace unification
    ):
        ensure_langgraph_supervisor_compatibility()

        self.agent_name = agent_name
        self.description = description
        self.tools = tools
        self.subagents = subagents or []
        self.subagent_descriptions = subagent_descriptions or {}
        self.request = request
        self.recursion_limit = recursion_limit
        self.system_prompt = system_prompt
        self.thread_generator = thread_generator
        self.user = user
        self.llm_model = llm_model
        self.temperature = temperature
        self.top_p = top_p
        self.request_uuid = request_uuid
        self.conversation_id = request.conversation_id
        self.handle_tool_error = handle_tool_error
        self.throw_truncated_error = throw_truncated_error
        self.callbacks = callbacks
        self.thread_context = None
        self.supervisor_callbacks = supervisor_callbacks or []
        self.output_schema = self._preprocess_output_schema(output_schema) if output_schema else None
        self.assistant = assistant
        self.override_global_checkpointer = override_global_checkpointer
        self.trace_context = trace_context
        self._current_llm_run_id: uuid.UUID | None = None  # tracks active LLM invocation
        self._sub_assistant_name_mapping: dict[str, str] = {}
        self.history_compaction_pre_model_hook = (
            ConversationHistoryCompactionService.build_langgraph_pre_model_hook(
                llm_model=llm_model,
                request_id=request_uuid,
            )
            if self._is_conversation_replay_v2_enabled()
            else None
        )
        # Capture the active OTel context from the calling thread (Thread A / asyncio HTTP handler)
        # so that stream(), which runs in a bare threading.Thread with no inherited contextvars,
        # can attach it and become a child of the HTTP request span.
        self._otel_context = get_otel_context_for_thread()

        # Smart tool selection/lookup configuration
        # Controls both:
        # 1. Dynamic tool selection (selecting subset from available tools)
        # 2. Smart tool lookup (finding tools when no toolkits configured)
        self.smart_tool_selection_enabled = smart_tool_selection_enabled
        self.tool_selection_limit = tool_selection_limit or config.TOOL_SELECTION_LIMIT

        set_logging_info(uuid=request_uuid, user_id=user.id, user_email=user.username)
        self.verbose = verbose
        self.is_react = is_react
        self.stream_steps = stream_steps

        # Tool error capture callback (LangChain-based)
        self.tool_error_callback = ToolErrorCaptureCallback(agent_name=agent_name)

        self.agent_executor = self.init_agent()

        self._supervisor_state: Optional[str] = None
        self._supervisor_coordinator = SupervisorCoordinator(
            handoff_tool_prefix=self.SUPERVISOR_HANDOFF_TOOL_PREFIX,
            extract_agent_name_from_tool=self._extract_agent_name_from_tool,
            tool_call_id_to_uuid=self._tool_call_id_to_uuid,
            resolve_display_name=self._build_subassistant_display_name,
            emit_handoff=lambda *args, **kwargs: self._on_supervisor_handoff(*args, **kwargs),
            emit_subassistant_back=lambda *args, **kwargs: self._on_subassistant_back(*args, **kwargs),
            set_thread_context=lambda *args, **kwargs: self.set_thread_context(*args, **kwargs),
        )
        self._supervisor_chunk_context_type = _SupervisorChunkContext
        self._callback_bridge = LangGraphCallbackBridge(self, get_logger=lambda: logger)
        self._event_adapter = LangGraphEventAdapter(self)
        self._buffered_supervisor_contexts: dict[str, list[_SupervisorChunkContext]] = {}

    @property
    def _handoff_run_ids(self) -> dict[str, HandoffRunBinding]:
        return self._supervisor_coordinator.tracker.run_bindings

    @_handoff_run_ids.setter
    def _handoff_run_ids(self, value: dict[str, HandoffRunBinding]) -> None:
        self._supervisor_coordinator.tracker.run_bindings = value

    @property
    def _pending_handoffs(self) -> dict[str, deque[PendingHandoff]]:
        return self._supervisor_coordinator.tracker.pending

    @_pending_handoffs.setter
    def _pending_handoffs(self, value: dict[str, deque[PendingHandoff]]) -> None:
        self._supervisor_coordinator.tracker.pending = value

    @property
    def _active_handoffs(self) -> dict[str, list[PendingHandoff]]:
        return self._supervisor_coordinator.tracker.active

    @_active_handoffs.setter
    def _active_handoffs(self, value: dict[str, list[PendingHandoff]]) -> None:
        self._supervisor_coordinator.tracker.active = value

    ###### Init logic ######

    def _should_prebind_tools(self, parallel_tool_calling: bool) -> bool:
        return bool(
            self.tools and parallel_tool_calling and not self.subagents and not self.smart_tool_selection_enabled
        )

    def _prepare_llm_for_agent(self):
        llm = self._initialize_llm()
        parallel_tool_calling = any(model in self.llm_model for model in config.DISABLE_PARALLEL_TOOLS_CALLING_MODELS)

        # Don't pre-bind tools when using smart tool selection (tools are bound inside create_react_agent)
        # For multi agents, tools binding happens inside create_supervisor
        if self._should_prebind_tools(parallel_tool_calling):
            llm = llm.bind_tools(self.tools, parallel_tool_calls=False)

        return llm, parallel_tool_calling

    def _build_supervisor_agent(self, llm, system_prompt: str):
        handoff_tools = self._create_handoff_tools()
        all_tools = (self.tools or []) + handoff_tools

        return create_supervisor(
            model=llm,
            agents=self.subagents,
            tools=all_tools,
            prompt=system_prompt,
            add_handoff_back_messages=False,
            output_mode="last_message",
            response_format=self.output_schema,
            pre_model_hook=_compose_pre_model_hooks(
                _strip_handoff_back_messages_pre_model_hook,
                _strip_subagent_task_messages_pre_model_hook,
            ),
        ).compile()

    def _build_single_agent(self, llm, system_prompt: str, agent_name: str, parallel_tool_calling: bool):
        return create_smart_react_agent(
            model=llm,
            tools=self.tools,
            prompt=system_prompt,
            response_format=self.output_schema,
            name=agent_name,
            tool_selection_enabled=self.smart_tool_selection_enabled,
            tool_selection_limit=self.tool_selection_limit,
            parallel_tool_calls=False if parallel_tool_calling else None,
            pre_model_hook=_compose_pre_model_hooks(
                self.history_compaction_pre_model_hook,
                _subagent_task_pre_model_hook,
                # Runs for all agents, but does nothing unless a tool
                # returns image artifacts (e.g. Jira with screenshots).
                _image_artifact_pre_model_hook,
            ),
        )

    def init_agent(self):
        llm, parallel_tool_calling = self._prepare_llm_for_agent()

        self.callbacks = self.configure_callbacks()
        system_prompt = self._get_system_prompt(from_request=True)

        agent_name = self.format_assistant_name(self.agent_name)

        if self.subagents:
            agent = self._build_supervisor_agent(llm, system_prompt)
        else:
            agent = self._build_single_agent(llm, system_prompt, agent_name, parallel_tool_calling)

        self._configure_tools()
        return agent

    def _create_handoff_tools(self) -> list[BaseTool]:
        """
        Create custom handoff tools for each subagent that include their descriptions.

        Since self.subagents contains compiled LangGraph agents (not simple objects),
        we rely on self.subagent_descriptions (dict[agent_name -> description])
        to get descriptions for each agent.

        Each handoff tool will have:
        - name: "transfer_to_{normalized_agent_name}"
        - description: The subagent's description from self.subagent_descriptions
        - task: A required parameter that the supervisor LLM must fill in with instructions for the subagent.

        Returns:
            List of handoff tools for supervisor to use
        """
        handoff_tools = []

        # Iterate through subagent descriptions (provided separately at init)
        for agent_name, description in self.subagent_descriptions.items():
            if not agent_name:
                logger.warning("Empty agent name found in subagent_descriptions. Skipping.")
                continue

            # Normalize agent name using existing method
            # This normalized name MUST match the subagent's internal name in the graph
            normalized_agent_name = self.format_assistant_name(agent_name)

            # Truncate ONLY for the tool name to fit within 64 char limit
            # The agent_name parameter must use normalized (not truncated) to match the actual subagent
            normalized_handoff_tool_name = f"{self.SUPERVISOR_HANDOFF_TOOL_PREFIX}_{normalized_agent_name}"[
                : self.ASSISTANT_NAME_MAX_LENGTH
            ]
            normalized_handoff_agent_name = normalized_handoff_tool_name[len(self.SUPERVISOR_HANDOFF_TOOL_PREFIX) + 1 :]

            # Store mapping from truncated tool name to original name for UI display
            # Callbacks receive the tool name (which may be truncated), so we need this mapping
            self._sub_assistant_name_mapping[normalized_handoff_agent_name] = agent_name

            # Create handoff tool with description
            # Add "Sub-assistant" prefix if description exists, otherwise use default message
            final_description = f"Sub-assistant: {description}" if description else f'Hand off task to {agent_name}'
            handoff_tool = LangGraphAgent._create_custom_handoff_tool(
                agent_name=normalized_handoff_agent_name,  # Use normalized name to match subagent's internal name
                name=normalized_handoff_tool_name,  # Truncate only the tool name
                description=final_description,
            )
            handoff_tools.append(handoff_tool)
            logger.debug(f"Created handoff tool for {agent_name}: {description}")

        return handoff_tools

    def create_handoff_tool_name(self, agent_name: str) -> str:
        return f"{self.SUPERVISOR_HANDOFF_TOOL_PREFIX}_{agent_name}"

    @staticmethod
    def _build_handoff_tool_message(task: str, name: str, tool_call_id: str, agent_name: str) -> ToolMessage:
        return ToolMessage(
            content=task,
            name=name,
            tool_call_id=tool_call_id,
            response_metadata={METADATA_KEY_HANDOFF_DESTINATION: agent_name},
            additional_kwargs={METADATA_KEY_SUBAGENT_TASK: True},
        )

    @staticmethod
    def _resolve_handoff_messages(state: dict[str, Any], agent_name: str) -> tuple[list[Any], AIMessage]:
        state_messages = state.get("messages") or []
        prompt_messages = state.get("llm_input_messages") or []
        messages = list(prompt_messages or state_messages)
        if not messages:
            raise ValueError(f"Cannot handoff to {agent_name}: state contains no messages")

        ai_message = state_messages[-1] if state_messages else messages[-1]
        if messages[-1] is not ai_message:
            messages.append(ai_message)
        if not isinstance(ai_message, AIMessage):
            raise TypeError(
                f"Cannot handoff to {agent_name}: expected AIMessage as last message, "
                f"got {type(ai_message).__name__}"
            )

        return messages, ai_message

    @classmethod
    def _build_parallel_handoff_spec(cls, tool_call: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
        parallel_agent_name = cls._extract_agent_name_from_tool(tool_call["name"])
        parallel_tool_args = unpack_json_strings(tool_call.get("args", {}))
        parallel_task = parallel_tool_args.get("task", "") if isinstance(parallel_tool_args, dict) else ""
        return tool_call, parallel_agent_name, parallel_task

    @classmethod
    def _extract_parallel_handoff_specs(cls, ai_message: AIMessage) -> list[tuple[dict[str, Any], str, str]]:
        handoff_tool_calls = [
            tool_call for tool_call in ai_message.tool_calls if cls._check_is_handoff_tool(tool_call["name"])
        ]
        return [cls._build_parallel_handoff_spec(tool_call) for tool_call in handoff_tool_calls]

    @staticmethod
    def _build_parallel_parent_handoff_artifacts(
        parallel_handoff_specs: list[tuple[dict[str, Any], str, str]],
    ) -> tuple[list[ToolMessage], list[ToolMessage]]:
        parent_handoff_acks = [
            ToolMessage(
                content="",
                name=parallel_tool_call["name"],
                tool_call_id=parallel_tool_call["id"],
                id=f"parallel-handoff-ack-{parallel_tool_call['id']}",
                additional_kwargs={PARALLEL_SUBAGENT_HANDOFF_ACK_KEY: True},
            )
            for parallel_tool_call, _, _ in parallel_handoff_specs
        ]
        parent_handoff_messages = [
            ToolMessage(
                content=parallel_task,
                name=parallel_tool_call["name"],
                tool_call_id=parallel_tool_call["id"],
                id=f"parallel-handoff-parent-{parallel_tool_call['id']}",
                additional_kwargs={METADATA_KEY_PARALLEL_SUBAGENT_PARENT_HANDOFF: True},
                response_metadata={METADATA_KEY_HANDOFF_DESTINATION: parallel_agent_name},
            )
            for parallel_tool_call, parallel_agent_name, parallel_task in parallel_handoff_specs
        ]
        return parent_handoff_acks, parent_handoff_messages

    @staticmethod
    def _should_return_parallel_handoff_ack(
        parallel_handoff_specs: list[tuple[dict[str, Any], str, str]],
        tool_call_id: str,
    ) -> bool:
        return bool(parallel_handoff_specs and parallel_handoff_specs[0][0]["id"] != tool_call_id)

    @staticmethod
    def _get_parallel_handoff_ack(parent_handoff_acks: list[ToolMessage], tool_call_id: str) -> ToolMessage:
        return next(ack for ack in parent_handoff_acks if ack.tool_call_id == tool_call_id)

    @classmethod
    def _build_parallel_sends(
        cls,
        ai_message: AIMessage,
        messages: list[Any],
        parallel_handoff_specs: list[tuple[dict[str, Any], str, str]],
    ) -> list[Send]:
        parallel_sends = []
        for parallel_tool_call, parallel_agent_name, parallel_task in parallel_handoff_specs:
            parallel_tool_message = cls._build_handoff_tool_message(
                task=parallel_task,
                name=parallel_tool_call["name"],
                tool_call_id=parallel_tool_call["id"],
                agent_name=parallel_agent_name,
            )
            parallel_handoff_call_message = cls._strip_other_tool_calls(ai_message, parallel_tool_call["id"])
            parallel_handoff_messages = messages[:-1]
            parallel_handoff_messages.extend((parallel_handoff_call_message, parallel_tool_message))
            parallel_sends.append(Send(parallel_agent_name, {"messages": parallel_handoff_messages}))
        return parallel_sends

    @classmethod
    def _build_parallel_handoff_result(
        cls,
        ai_message: AIMessage,
        messages: list[Any],
        tool_call_id: str,
    ) -> Command | ToolMessage:
        parallel_handoff_specs = cls._extract_parallel_handoff_specs(ai_message)
        parent_handoff_acks, parent_handoff_messages = cls._build_parallel_parent_handoff_artifacts(
            parallel_handoff_specs
        )

        if cls._should_return_parallel_handoff_ack(parallel_handoff_specs, tool_call_id):
            return cls._get_parallel_handoff_ack(parent_handoff_acks, tool_call_id)

        parallel_sends = cls._build_parallel_sends(ai_message, messages, parallel_handoff_specs)
        return Command(
            graph=Command.PARENT,
            update={"messages": [*messages[:-1], *parent_handoff_messages]},
            goto=parallel_sends,
        )

    @staticmethod
    def _create_custom_handoff_tool(
        *,
        agent_name: str,
        name: str,
        description: str,
    ) -> BaseTool:
        """Create a handoff tool that forces the supervisor LLM to provide an explicit task.

        The ``task`` parameter is LLM-visible and required. On execution, the
        sub-agent receives the full state required for LangGraph's merge
        semantics, while a pre-model hook narrows the actual LLM input down to
        the synthesized task message.
        """
        ensure_langgraph_supervisor_compatibility()

        @langchain_tool(name, description=description)
        def handoff_to_agent_with_task(
            task: Annotated[
                str,
                Field(
                    description=(
                        "A self-contained task instruction for the sub-agent. "
                        "Include all context required to complete the task independently — "
                        "do not assume the sub-agent has access to prior conversation turns."
                    )
                ),
            ],
            state: Annotated[dict, InjectedState],
            tool_call_id: Annotated[str, InjectedToolCallId],
        ) -> Command:
            tool_message = LangGraphAgent._build_handoff_tool_message(task, name, tool_call_id, agent_name)
            messages, ai_message = LangGraphAgent._resolve_handoff_messages(state, agent_name)

            if len(ai_message.tool_calls) > 1:
                return LangGraphAgent._build_parallel_handoff_result(ai_message, messages, tool_call_id)

            handoff_messages = [*messages, tool_message]

            return Command(
                graph=Command.PARENT,
                goto=agent_name,
                update={**state, "messages": handoff_messages},
            )

        handoff_to_agent_with_task.metadata = {METADATA_KEY_HANDOFF_DESTINATION: agent_name}
        return handoff_to_agent_with_task

    def configure_callbacks(self) -> List[BaseCallbackHandler]:
        self.supervisor_callbacks = build_supervisor_callbacks(
            self.supervisor_callbacks,
            thread_generator=self.thread_generator,
            stream_steps=self.stream_steps,
            name_resolver=self,
        )
        return build_tool_callbacks(
            self.callbacks,
            thread_generator=self.thread_generator,
            stream_steps=self.stream_steps,
            tool_error_callback=self.tool_error_callback,
            name_resolver=self,
        )

    def _initialize_llm(self):
        return get_llm_by_credentials(
            llm_model=self.llm_model,
            temperature=self.temperature,
            top_p=self.top_p,
            request_id=self.request_uuid,
        )

    ###### Called by external entities ######

    def invoke(self, input: str = "", history=None, args=None) -> str | BaseModel | dict:
        """
        Invoke agent and receive only generation result, which can be a regular string or a structured output
        """
        if args is None:
            args = {}
        if history is None:
            history = []
        try:
            set_llm_context(self.assistant, None, self.user)
            inputs = self._get_inputs(input, history)
            inputs.update(args)
            output = self._invoke_agent(inputs).generated
            return output
        except MCPAuthenticationRequiredException:
            raise
        except Exception as e:
            return self._get_user_error_message(e)

    @traced(
        "agent.invoke_task",
        lambda self, *args, **kwargs: {
            "codemie.agent_name": self.agent_name,
            "codemie.model": self.llm_model,
            "codemie.request_id": self.request_uuid,
            "codemie.conversation_id": self.conversation_id or "",
        },
    )
    def invoke_task(self, workflow_input: str = "", history=None, args=None) -> TaskResult:
        """
        Used in workflows invocation
        """
        if args is None:
            args = {}
        if history is None:
            history = []
        set_llm_context(self.assistant, None, self.user)
        logger.debug(
            f"Invoking workflow task. Agent={self.agent_name}, "
            f"Input={self._truncate_log_content(workflow_input)}, "
            f"ChatHistory={self._serialize_messages_for_log(self._filter_history(self._transform_history(history)))}"
        )
        try:
            inputs = self._get_inputs(workflow_input, history)
            inputs.update(args)
            agent_response = self._invoke_agent(inputs)

            return TaskResult.from_agent_response(agent_response)
        except MCPAuthenticationRequiredException:
            raise
        except Exception as e:
            record_exception_on_span(e)
            return TaskResult.failed_result(self._get_user_error_message(e), original_exc=e)

    @traced(
        "agent.generate",
        lambda self, *args, **kwargs: {
            "codemie.agent_name": self.agent_name,
            "codemie.model": self.llm_model,
            "codemie.request_id": self.request_uuid,
            "codemie.conversation_id": self.conversation_id or "",
        },
    )
    def generate(self, background_task_id: str = "") -> GenerationResult:
        """
        Executes the AI agent and returns the generated output along with performance metadata.
        This method handles the complete generation process, providing not just the AI output
        but also valuable metadata like token usage and execution time. It represents the standard
        way to invoke the agent when you need detailed performance metrics alongside results.
        """
        start_time = time()
        try:
            # Clear previous errors before new execution
            self.tool_error_callback.clear()

            set_llm_context(self.assistant, None, self.user)
            response = self._invoke_agent(self._get_inputs())
            output = response.generated
            self._persist_generated_workspace_files(
                response=output,
                conversation_id=self.conversation_id,
                user=self.user,
                request_file_names=self.request.file_names,
            )

            token_used = calculate_tokens(json.dumps(output))

            time_elapsed = time() - start_time
            if background_task_id:
                BackgroundTasksService().update(
                    task_id=background_task_id, status=BackgroundTaskStatus.COMPLETED, final_output=output
                )

            return self._build_generation_result(
                output,
                success=True,
                time_elapsed=time_elapsed,
                tokens_used=token_used,
            )
        except MCPAuthenticationRequiredException:
            raise
        except Exception as e:
            record_exception_on_span(e)
            time_elapsed = time() - start_time
            user_message = self._get_user_error_message(e)

            if background_task_id:
                BackgroundTasksService().update(
                    task_id=background_task_id, status=BackgroundTaskStatus.FAILED, final_output=user_message
                )

            return self._build_generation_result(user_message, success=False, time_elapsed=time_elapsed)

    def stream(self):
        """
        This method enables assistant streaming, with the stream’s destination generally being a user interface (UI).
        To handle the incoming stream content, you can use a thread generator.
        """
        # stream() runs in a bare threading.Thread (Thread C) that does not inherit contextvars
        # from the asyncio HTTP handler (Thread A).  propagated_span() attaches the context
        # captured in __init__ so that the span becomes a child of the HTTP request span.
        with propagated_span(
            self._otel_context,
            "agent.stream",
            {
                "codemie.agent_name": self.agent_name,
                "codemie.model": self.llm_model,
                "codemie.request_id": self.request_uuid,
                "codemie.conversation_id": self.conversation_id or "",
            },
        ):
            set_logging_info(
                uuid=self.request_uuid,
                user_id=self.user.id,
                conversation_id=self.conversation_id,
                user_email=self.user.username,
            )
            set_llm_context(self.assistant, None, self.user)

            execution_start = time()
            chunks_collector = []

            auth_required_error: MCPAuthenticationRequiredException | None = None
            try:
                logger.info(f"Starting {self.agent_name} agent for task: {self._task}")
                result = self._agent_streaming(chunks_collector)
                logger.info(f"Finish {self.agent_name} agent for task: {self._task}")
                time_elapsed = time() - execution_start

                result = json.dumps(result) if isinstance(result, (dict, BaseModel)) else result

                self.thread_generator.send(
                    StreamedGenerationResult(
                        generated=result,
                        generated_chunk="",
                        last=True,
                        time_elapsed=time_elapsed,
                        debug={},
                        context=self.thread_context,
                    ).model_dump_json()
                )
            except MCPAuthenticationRequiredException as e:
                auth_required_error = e
                self.thread_generator.close(e)
                raise
            except Exception as e:
                record_exception_on_span(e)
                time_elapsed = time() - execution_start
                error_response: ErrorResponse = handle_agent_exception(e)
                llm_error_code = error_response.get_error().error_code.value
                if config.HIDE_AGENT_STREAMING_EXCEPTIONS:
                    user_message = error_response.get_error().message
                else:
                    user_message = self.extended_error(error_response, e)
                chunks_collector.append(user_message)
                generated, execution_error = self._process_chunks(chunks_collector, config, llm_error_code)

                self.thread_generator.send(
                    StreamedGenerationResult(
                        generated=generated,
                        generated_chunk="",
                        last=True,
                        time_elapsed=time_elapsed,
                        debug={},
                        context=self.thread_context,
                        execution_error=execution_error,
                    ).model_dump_json()
                )
            finally:
                if auth_required_error is None:
                    self.thread_generator.close()

    def _process_chunks(
        self,
        chunks_collector: list[str],
        cfg: Config,
        llm_error_code: str | None = None,
    ) -> tuple[str, str | None]:
        """Build final generated text and determine ``execution_error``.

        When *llm_error_code* is provided it takes precedence: the friendly
        LLM message (already in chunks) is safe for the end-user, so we
        always join chunks and propagate the specific error code.
        """
        if llm_error_code:
            return "".join(chunks_collector), llm_error_code

        if cfg.HIDE_AGENT_STREAMING_EXCEPTIONS:
            if any("guardrail" in chunk.lower() for chunk in chunks_collector):
                return cfg.CUSTOM_GUARDRAILS_MESSAGE, ExecutionErrorEnum.GUARDRAILS.value
            return cfg.CUSTOM_STACKTRACE_MESSAGE, ExecutionErrorEnum.STACKTRACE.value

        return "".join(chunks_collector), None

    def invoke_with_a2a_output(self, query: str = "") -> dict:
        try:
            inputs = self._get_inputs(query)
            response = self._invoke_agent(inputs).generated
            return {"is_task_complete": True, "require_user_input": False, "content": response}
        except Exception as e:
            return {
                "is_task_complete": False,
                "require_user_input": True,
                "content": self._get_user_error_message(e),
            }

    ###### Core method ######

    def _stream_graph(
        self, inputs, config=None, chunks_collector: Optional[list[str]] = None
    ) -> str | dict | BaseModel:
        """
        Stream the agent's response graph and process chunks incrementally.

        This method executes the LangGraph agent and streams its response in chunks, allowing for
        real-time processing of the generated content. It handles user disconnection gracefully
        and collects all chunks for potential UI display.

        Args:
            inputs: The input data to be processed by the agent executor.
            config: Optional configuration parameters for the agent executor. Defaults to None.
            chunks_collector: Optional list to collect chunks of response data for UI streaming.
                            If None, a new empty list will be created. Defaults to None.

        Returns:
            str | dict | BaseModel: The content of the last AI message generated, which can be
                                a string, dictionary, or a BaseModel instance depending on
                                the agent's configuration.

        Notes:
            - The method monitors for user disconnection via the thread_generator.
            - Each chunk is processed via the process_chunk method.
            - When streaming completes, the _on_chain_end callback is triggered with the final message.
        """
        if chunks_collector is None:
            chunks_collector = []

        stream = self.agent_executor.stream(
            inputs, config=config, stream_mode=["updates", "messages"], subgraphs=bool(self.subagents)
        )
        last_message = ""
        has_structured_response = False

        with suppress_stdout():
            for chunk in stream:
                if self._should_stop_streaming():
                    break
                if not chunk:
                    continue

                self.process_chunk(chunk, chunks_collector)
                chunk = self._prepare_chunk_for_processing(chunk)
                current_message = self._get_last_ai_message_content(chunk)

                last_message, has_structured_response = self._update_last_message_if_needed(
                    current_message, last_message, has_structured_response
                )

        self._finalize_stream_result(last_message)
        return last_message

    def _should_stop_streaming(self) -> bool:
        """Check if streaming should stop due to user disconnection."""
        if self.thread_generator and self.thread_generator.is_closed():
            logger.info(f"Stopping agent {self.agent_name}, user is disconnected")
            return True
        return False

    def _prepare_chunk_for_processing(self, chunk):
        """Prepare chunk for processing by extracting relevant data for subagents."""
        if self.subagents:
            _, *chunk = chunk
        return chunk

    def _update_last_message_if_needed(
        self,
        current_message: str | dict | BaseModel,
        last_message: str | dict | BaseModel,
        has_structured_response: bool,
    ) -> tuple[str | dict | BaseModel, bool]:
        """
        Update last_message if current_message should replace it.

        Prioritizes structured responses (dict/BaseModel) over regular string messages.
        Once a structured response is found, it won't be overwritten by regular messages.

        Returns:
            Tuple of (updated_last_message, updated_has_structured_response)
        """
        if not current_message:
            return last_message, has_structured_response

        is_structured = isinstance(current_message, (dict, BaseModel))
        should_update = not has_structured_response or is_structured

        if should_update:
            # Update flag: keep True if already True, or set True if current message is structured
            updated_flag = has_structured_response or is_structured
            return current_message, updated_flag

        return last_message, has_structured_response

    def _finalize_stream_result(self, last_message: str | dict | BaseModel) -> None:
        """Finalize streaming by calling callbacks and logging warnings if needed."""
        self._on_chain_end(last_message)
        logger.debug(f"Final result is: {last_message}")

        if not last_message:
            message = (
                f"Last AIMessage of agent is empty. Assistant: {self.agent_name}, request_uuid: {self.request_uuid}"
            )
            logger.warning(message)

    ###### Helpers ######

    def _get_user_error_message(self, error: Exception) -> str:
        error_response: ErrorResponse = handle_agent_exception(error)
        if config.HIDE_AGENT_STREAMING_EXCEPTIONS:
            return error_response.get_error().message
        return self.extended_error(error_response, error)

    def _get_tool_errors(self):
        return self.tool_error_callback.tool_errors if self.tool_error_callback.has_errors() else None

    def _build_generation_result(
        self,
        generated,
        *,
        success: bool,
        time_elapsed: float | None = None,
        tokens_used: int | None = None,
    ) -> GenerationResult:
        return GenerationResult(
            generated=generated,
            time_elapsed=time_elapsed,
            input_tokens_used=None,
            tokens_used=tokens_used,
            success=success,
            agent_error=None,
            tool_errors=self._get_tool_errors(),
        )

    def _invoke_agent(self, inputs) -> GenerationResult:
        logger.debug(f"Invoking task. Agent={self.agent_name}. Inputs={self._serialize_inputs_for_log(inputs)}")
        set_llm_context(self.assistant, None, self.user)
        try:
            import contextlib

            run_config = self._get_run_config()
            trace_ctx = run_config.pop("_trace_ctx", contextlib.nullcontext())
            with trace_ctx:
                output = self._stream_graph(inputs, config=run_config)

            response = self._build_generation_result(output, success=True)

            logger.debug(
                f"Invoking task. Agent={self.agent_name}. Response={self._serialize_response_for_log(response)}"
            )
            return response
        except MCPAuthenticationRequiredException:
            raise
        except Exception as e:
            return self._build_generation_result(self._get_user_error_message(e), success=False)

    def _get_run_config(self):
        tags = ["execution_engine:langgraph_agent"]

        # Get assistant version if available
        assistant_version = None
        if self.assistant and hasattr(self.assistant, "version"):
            assistant_version = self.assistant.version

        run_config = get_run_config(
            request=self.request,
            llm_model=self.llm_model,
            agent_name=self.agent_name,
            conversation_id=self.conversation_id,
            username=self.user.username if self.user and self.user.username else None,
            additional_tags=tags,
            assistant_version=assistant_version,
            trace_context=self.trace_context,
        )

        # Add LangGraph specific configuration
        run_config.update({"recursion_limit": self.recursion_limit})
        if self.override_global_checkpointer:
            # When this agent is run as part of a workflow (instead of natively within LangGraph),
            # LangGraph overrides the agent's checkpoint globally, which can lead to errors.
            # This is problematic because the agent's execution
            # should remain independent when instance methods are used.
            run_config.update(
                {
                    "__pregel_checkpointer": InMemorySaver(),
                    "max_concurrency": self.MAX_CONCURRENCY,
                    "thread_id": "thread",
                }
            )
        return run_config

    def _is_unique_callback(self, callbacks: List[BaseCallbackHandler], candidate) -> bool:
        """Check if callback of this type doesn't exist in the list."""
        return is_unique_callback(callbacks, candidate)

    def _agent_streaming(self, chunks_collector: list[str]) -> str | dict | BaseModel:
        import contextlib

        inputs = self._get_inputs()
        run_config = self._get_run_config()
        trace_ctx = run_config.pop("_trace_ctx", contextlib.nullcontext())
        with trace_ctx:
            result = self._stream_graph(inputs, run_config, chunks_collector)
        return result

    def _get_system_prompt(self, from_request: bool = False):
        system_prompt = self.system_prompt

        if from_request:
            system_prompt = self.request.system_prompt or system_prompt

        if config.LLM_REQUEST_ADD_MARKDOWN_PROMPT:
            system_prompt = system_prompt + " " + markdown_response_prompt

        return system_prompt

    def _get_last_ai_message_content(self, chunk) -> str | BaseModel | dict:
        chunk_type, value = chunk
        result = ""
        if chunk_type == "updates":
            target_field = "agent" if not self.subagents else "supervisor"
            node_state = value.get(target_field)
            if isinstance(node_state, dict) and self.is_valid_ai_message(message := node_state["messages"][-1]):
                result = extract_text_from_llm_output(message.content)
            elif response := value.get("generate_structured_response"):
                result = response["structured_response"]
        return result

    def _configure_tools(self):
        tool_metadata = {
            REQUEST_ID: self.request_uuid,
            USER_ID: self.user.id,
            USER_NAME: self.user.name,
            LLM_MODEL: self.llm_model,
            AGENT_NAME: self.agent_name,
            ASSISTANT_ID: self.assistant.id if self.assistant else None,
            PROJECT: self.assistant.project if self.assistant else None,
            **(self.request.metadata or {}),
        }
        for tool in self.tools:
            if hasattr(tool, 'metadata') and tool.metadata:
                tool.metadata.update(tool_metadata)
            else:
                tool.metadata = tool_metadata

            if hasattr(tool, OUTPUT_FORMAT):
                tool.metadata[OUTPUT_FORMAT] = tool.output_format

            if hasattr(tool, 'throw_truncated_error'):
                tool.throw_truncated_error = self.throw_truncated_error

            tool.handle_tool_error = self.handle_tool_error

    def _get_inputs(self, input_text: str = "", history=None):
        if history is None:
            history = []
        if not input_text:
            input_text = self._task
        user_input_content = [{"type": "text", "text": input_text}]

        raw_history = history if history else self.request.history
        history = self._filter_history(self._transform_history(raw_history))

        if self.request.file_names:
            # Use our filter_base64_images method to get base64 content and mime type for all images
            base64_images = ImageService.filter_base64_images(self.request.file_names)

            if base64_images:
                user_input_content.append({"type": "text", "text": "Attached images:"})

                # Add each image with proper type format and its specific mime type
                for image_info in base64_images:
                    user_input_content.append(
                        {
                            "type": "image",
                            "source_type": "base64",
                            "data": image_info['content'],
                            "mime_type": image_info['mime_type'],
                        }
                    )
        input_task = HumanMessage(content=user_input_content)
        agent_inputs = {"messages": [*history, input_task]}
        logger.debug(
            f"LangGraphAgent input payload. Agent={self.agent_name}, "
            f"Messages={self._serialize_messages_for_log(agent_inputs['messages'])}"
        )

        return agent_inputs

    def __parse_message_type(self, value, chunks_collector: list[str]):
        self._event_adapter.parse_message_type(value, chunks_collector)

    def __parse_update_type(self, value):
        self._event_adapter.parse_update_type(value)

    def __parse_supervisor_message_type(self, value, chunks_collector: list[str], author: str | None = None):
        self._event_adapter.parse_supervisor_message_type(value, chunks_collector, author=author)

    def __parse_supervisor_update_type(self, value: dict, author: str | None = None):
        self._event_adapter.parse_supervisor_update_type(value, author=author)

    def _queue_supervisor_handoffs(self, handoff_calls: list[dict[str, Any]], author: str | None = None) -> None:
        self._supervisor_coordinator.queue_supervisor_handoffs(handoff_calls, author=author)

    def __handle_supervisor_tool_calls(self, last_message: AIMessage, author: str | None = None) -> None:
        self._event_adapter.handle_supervisor_tool_calls(last_message, author=author)

    def __handle_supervisor_handoff_back(
        self, messages: list, run_id: tuple[UUID, str | None] | None, author: str | None = None
    ) -> None:
        """Handle subassistant returning control to the supervisor."""
        logger.debug("Handoff back to supervisor")
        self._supervisor_coordinator.handle_supervisor_handoff_back(messages, run_id, author=author)

    def __handle_supervisor_subassistant_result(
        self, messages: list, run_id: tuple[UUID, str | None] | None, author: str | None = None
    ) -> None:
        """Handle subassistant completion when handoff-back messages are disabled."""
        self._supervisor_coordinator.handle_supervisor_subassistant_result(messages, run_id, author=author)

    def _process_chunk_for_agent(self, chunk, chunks_collector: list[str]):
        self._event_adapter.process_chunk_for_agent(chunk, chunks_collector)

    def _promote_pending_handoff(self, context: _SupervisorChunkContext, chunk) -> None:
        logger.debug(chunk)
        self._supervisor_coordinator.promote_pending_handoff(context)

    def _dispatch_supervisor_chunk(self, context: _SupervisorChunkContext, chunks_collector: list[str]) -> None:
        self._event_adapter.dispatch_supervisor_chunk(context, chunks_collector)

    def _buffer_supervisor_context(self, context: _SupervisorChunkContext) -> None:
        if not context.author:
            return
        self._buffered_supervisor_contexts.setdefault(context.author, []).append(context)

    def _flush_buffered_supervisor_contexts(self, author: str | None, chunks_collector: list[str]) -> None:
        if not author:
            return

        buffered_contexts = self._buffered_supervisor_contexts.pop(author, [])
        for buffered_context in buffered_contexts:
            self._dispatch_supervisor_chunk(buffered_context, chunks_collector)

    def _process_chunk_for_supervisor(self, chunk, chunks_collector: list[str]):
        self._event_adapter.process_chunk_for_supervisor(chunk, chunks_collector)

    def process_chunk(self, chunk, chunks_collector: List[str]):
        if self.subagents:
            self._process_chunk_for_supervisor(chunk, chunks_collector)
        else:
            self._process_chunk_for_agent(chunk, chunks_collector)

    def get_thoughts_from_callback(self):
        return self._callback_bridge.get_thoughts_from_callback()

    def is_finish_reason_stop(self, message: AIMessage) -> bool:
        response_medatada = message.response_metadata
        stop_reason = response_medatada.get("stop_reason") or response_medatada.get("finish_reason")
        if stop_reason == "end_turn" or stop_reason == "stop":
            return True
        if response_medatada:
            if LLMService.BASE_NAME_CLAUDE in self.llm_model:
                stop_reason = response_medatada.get("stop_reason", response_medatada.get("stopReason"))
                return stop_reason == "end_turn"
            else:
                return response_medatada.get("finish_reason") == "stop"

    def is_finish_reason_tool_calls(self, message: AIMessage) -> bool:
        return hasattr(message, "tool_calls") and bool(message.tool_calls)

    def _safe_check_for_truncation(self, message: AIMessage) -> None:
        """
        Safely check for truncation with error handling.

        This wrapper ensures that TokenLimitExceededException is raised properly,
        while any unexpected errors in the detection logic are logged but don't break execution.

        Args:
            message: AIMessage to check for truncation

        Raises:
            TokenLimitExceededException: If response was truncated (expected, stops execution)
        """
        try:
            self._check_for_truncated_response(message)
        except TokenLimitExceededException:
            # Re-raise token limit exceptions as they're expected and should stop execution
            raise
        except Exception as e:
            # Log unexpected errors in detection logic but don't break the agent flow
            logger.error(f"Unexpected error during truncation check: {e}", exc_info=True)

    def _get_truncation_indicator(self, response_metadata: dict) -> str | None:
        """
        Check response metadata for truncation indicators.

        Args:
            response_metadata: Response metadata from AIMessage

        Returns:
            Truncation indicator string if truncated, None otherwise
        """
        # LiteLLM proxy / OpenAI format
        finish_reason = response_metadata.get("finish_reason")
        if finish_reason in ["length", "max_tokens"]:
            return f"finish_reason={finish_reason}"

        # Native Claude format (Bedrock uses 'stopReason' camelCase)
        stop_reason = response_metadata.get("stop_reason", response_metadata.get("stopReason"))
        if stop_reason == "max_tokens":
            return f"stop_reason={stop_reason}"

        return None

    def _log_incomplete_tool_calls(self, message: AIMessage) -> str:
        """
        Log incomplete tool calls and return context string.

        Args:
            message: AIMessage that may contain tool calls

        Returns:
            Context string describing what was truncated
        """
        has_tool_calls = hasattr(message, "tool_calls") and message.tool_calls
        if not has_tool_calls:
            logger.error("   🔧 Response truncated before tool calls could be generated")
            return "before tool arguments could be generated"

        incomplete_tools = []
        for tool_call in message.tool_calls:
            tool_name = tool_call.get('name', 'unknown')
            tool_args = tool_call.get('args', {})
            incomplete_tools.append(tool_name)
            logger.error(
                f"   🔧 Incomplete Tool Call - Name: {tool_name}, "
                f"Args: {tool_args}, "
                f"ID: {tool_call.get('id', 'unknown')}"
            )

        return f"while generating {', '.join(incomplete_tools)} arguments"

    def _check_for_truncated_response(self, message: AIMessage) -> None:
        """
        Validate that LLM response wasn't truncated due to max_tokens limit.

        This check must be performed before tool execution to prevent calling tools
        with incomplete arguments, which would cause Pydantic validation errors.

        Called in two locations:
        - __parse_update_type: For single-agent flows
        - __parse_supervisor_update_type: For multi-agent (supervisor) flows

        Args:
            message: AIMessage from LLM containing tool_calls

        Raises:
            TokenLimitExceededException: If response was truncated (finish_reason='length'
                                        or stop_reason='max_tokens')
        """
        response_metadata = message.response_metadata
        if not response_metadata:
            return

        truncation_indicator = self._get_truncation_indicator(response_metadata)
        if not truncation_indicator:
            return

        logger.error(
            f"⚠️ TRUNCATED RESPONSE DETECTED: LLM response was cut off due to max_tokens limit! "
            f"Model: {self.llm_model}, {truncation_indicator}. "
            f"This causes incomplete tool call arguments."
        )

        tool_context = self._log_incomplete_tool_calls(message)

        error_message = (
            f"\n⚠️ TOKEN LIMIT EXCEEDED\n"
            f"API Response: {truncation_indicator}\n"
            f"Model: '{self.llm_model}'\n\n"
            f"The configured max_output_tokens limit was reached {tool_context}.\n"
            f"Incomplete tool input was formed and cannot be passed to the tool for execution.\n\n"
            f"🔧 FIX: Increase 'max_output_tokens' for '{self.llm_model}' in your config file.\n"
            f"The current limit is insufficient for this operation.\n"
            f"CodeMie Support: {config.CODEMIE_SUPPORT}"
        )

        raise TokenLimitExceededException(
            message=error_message,
            model=self.llm_model,
            truncation_reason=truncation_indicator,
        )

    def _parse_tool_message(self, action: ToolMessage, author: str | None = None):
        self._event_adapter.parse_tool_message(action, author=author)

    def _process_agent_streaming(self, token, chunks_collector: list[str], run_id: str, author: str | None = None):
        self._event_adapter.process_agent_streaming(token, chunks_collector, run_id, author=author)

    ###### Callbacks ########

    def _on_llm_start(self):
        self._callback_bridge.on_llm_start()

    def _on_llm_new_token(self, token, run_id: str, author: str | None = None):
        self._callback_bridge.on_llm_new_token(token, run_id, author=author)

    def _on_llm_end(self, response, run_id: str, author: str | None = None):
        self._callback_bridge.on_llm_end(response, run_id, author=author)

    def _on_llm_error(self, error: BaseException, run_id: str, author: str | None = None):
        self._callback_bridge.on_llm_error(error, run_id, author=author)

    def _on_tool_start(self, tool_name: str, input_str: str, run_id: str | None = None, author: str | None = None):
        self._callback_bridge.on_tool_start(tool_name, input_str, run_id=run_id, author=author)

    def _on_tool_end(
        self,
        output,
        run_id: UUID,
        author: str | None = None,
    ):
        self._callback_bridge.on_tool_end(output, run_id, author=author)

    def _on_supervisor_handoff(
        self,
        destination: str,
        run_id: str,
        input_str: str = "",
        author: str | None = None,
        display_name: str | None = None,
    ):
        self._callback_bridge.on_supervisor_handoff(
            destination,
            run_id,
            input_str=input_str,
            author=author,
            display_name=display_name,
        )

    def _on_subassistant_back(self, output, run_id: str | None = None, author: str | None = None):
        self._callback_bridge.on_subassistant_back(output, run_id=run_id, author=author)

    def _on_tool_error(self, output, run_id: UUID | None = None, author: str | None = None):
        self._callback_bridge.on_tool_error(output, run_id=run_id, author=author)

    def _on_chain_end(self, output):
        self._callback_bridge.on_chain_end(output)

    def is_pure_chain(self) -> bool:
        return isinstance(self.agent_executor, PureChatChain)

    ###### Static helpers ######

    @staticmethod
    def _preprocess_output_schema(output_schema: dict | BaseModel) -> dict | BaseModel:
        if isinstance(output_schema, dict):
            check = validate_json_schema(output_schema)
            if not check:
                raise ValueError(f"Wrong JSON Schema was put in agent: {output_schema}")
            output_schema["title"] = output_schema.get("title", "StructuredOutput")
            output_schema["description"] = output_schema.get("description", "Structured output")
        return output_schema

    @staticmethod
    def is_valid_ai_message(message: AIMessage) -> bool:
        return bool(message.content and isinstance(message, AIMessage))

    @staticmethod
    def _get_tool_call_args(message: AIMessage) -> tuple[str, str]:
        tool_call = message.tool_calls[0]
        tool_name = tool_call["name"]
        unpacked_args = unpack_json_strings(tool_call["args"])
        return tool_name, str(unpacked_args)

    @staticmethod
    def _tool_call_id_to_uuid(tool_call_id: str) -> UUID:
        """Convert a tool call ID (any string) to a deterministic UUID.

        LLM providers use their own ID formats (e.g. "call_abc123" from OpenAI).
        We convert them to UUID so they satisfy the run_id type expected by callbacks,
        while keeping the mapping deterministic so start and end always get the same UUID.
        """
        if not tool_call_id:
            return uuid.uuid4()
        try:
            return UUID(tool_call_id)
        except ValueError:
            return uuid.uuid5(uuid.NAMESPACE_URL, tool_call_id)

    @staticmethod
    def process_output(output, chunks_collector: List[str]):
        message = extract_text_from_llm_output(output)
        chunks_collector.append(message)

    @staticmethod
    def format_assistant_name(name: str) -> str:
        # Replace forbidden characters with underscore
        name = re.sub(r'[\s<|\\/>]', '_', name)
        # Remove any character that's not alphanumeric or underscore
        name = re.sub(r'\W', '', name).lower()
        return LangGraphAgent.truncate_sub_assistant_handoff_tool_name(name)

    @classmethod
    def _check_is_handoff_tool(cls, tool_name: str) -> bool:
        return tool_name.startswith(cls.SUPERVISOR_HANDOFF_TOOL_PREFIX)

    @classmethod
    def _extract_agent_name_from_tool(cls, tool_name: str) -> str:
        return tool_name[len(cls.SUPERVISOR_HANDOFF_TOOL_PREFIX) + 1 :]

    @staticmethod
    def _transform_history(history: List[ChatMessage]) -> list:
        """Convert history to list of chain-compatible messages"""
        return transform_history(
            history,
            supports_rich_history=LangGraphAgent._is_conversation_replay_v2_enabled(),
        )

    @staticmethod
    def _strip_other_tool_calls(ai_message: AIMessage, tool_call_id: str) -> AIMessage:
        """Return a copy of ai_message retaining only the tool call matching tool_call_id.

        Mirrors langgraph_supervisor.handoff._remove_non_handoff_tool_calls without
        depending on that private symbol.  Preserves non-tool-use content blocks
        (e.g. "text") and removes "tool_use" blocks that don't match tool_call_id
        (Anthropic-style content arrays).
        """
        content = ai_message.content
        if isinstance(content, list) and len(content) >= 1 and isinstance(content[0], dict):
            content = [block for block in content if block.get("type") != "tool_use" or block.get("id") == tool_call_id]
        return AIMessage(
            content=content,
            tool_calls=[tc for tc in ai_message.tool_calls if tc["id"] == tool_call_id],
            name=ai_message.name,
            # Parallel handoffs must get a fresh message id so LangGraph's
            # add_messages reducer appends each branch instead of overwriting
            # sibling branches that originated from the same supervisor turn.
            id=str(uuid.uuid4()),
            response_metadata=ai_message.response_metadata,
            additional_kwargs=ai_message.additional_kwargs,
            usage_metadata=ai_message.usage_metadata,
        )

    def _rebind_author_to_active_handoff(self, author: str, raw_author: str, task: str) -> None:
        self._supervisor_coordinator.rebind_author_to_active_handoff(author, raw_author, task)

    def _build_subassistant_display_name(self, agent_name: str, index: int, total: int) -> str:
        base_name = self.get_original_sub_assistant_name(agent_name).replace('_', ' ').title()
        if total <= 1:
            return base_name
        return f"{base_name} #{index}"

    @classmethod
    def _filter_history(cls, history: list) -> list:
        return filter_history(history, supports_rich_history=cls._is_conversation_replay_v2_enabled())

    def set_thread_context(self, context: dict, parent_thought_id: str | None, author: str | None = None):
        self.thread_context = context
        for callback in [*self.callbacks, *self.supervisor_callbacks]:
            if hasattr(callback, "set_context"):
                callback.set_context(context, parent_thought_id, author)
            if hasattr(callback, "parent_id"):
                callback.parent_id = parent_thought_id

    def set_subagent_execution(self):
        for callback in self.callbacks:
            if isinstance(callback, AgentStreamingCallback):
                run_id = uuid.uuid4()
                thought = callback.thoughts_storage.create_thought(
                    run_id=run_id, tool_name=AgentStreamingCallback.GENERIC_TOOL_NAME
                )
                thought.author_type = ThoughtAuthorType.Agent.value

    @staticmethod
    def _serialize_messages_for_log(messages: list[Any]) -> str:
        return serialize_messages(messages)

    @staticmethod
    def _serialize_tool_calls_for_log(tool_calls: list[dict]) -> list[dict[str, str | None]]:
        return serialize_tool_calls(tool_calls)

    @staticmethod
    def _truncate_log_content(content: Any) -> str:
        return truncate_log_value(content)

    @classmethod
    def _serialize_inputs_for_log(cls, inputs: dict) -> str:
        return serialize_inputs(inputs, messages_key="messages")

    @classmethod
    def _serialize_response_for_log(cls, response: Any) -> str:
        return serialize_response(response)

    @property
    def _task(self):
        if not self.request.file_names:
            return self.request.text

        file_names = [FileObject.from_encoded_url(name).name for name in self.request.file_names]
        return f"{self.request.text}{"\n Attached files: " + ", ".join(file_names)}"

    @staticmethod
    def truncate_sub_assistant_handoff_tool_name(name: str) -> str:
        """
        Truncate assistant name to ensure it fits within the handoff tool name length constraint.

        The handoff tool name format is: "transfer_to_{assistant_name}"
        This entire string must be <= 64 characters (LangGraphAgent.ASSISTANT_NAME_MAX_LENGTH).

        When truncation is needed, adds a hash suffix for uniqueness to prevent collisions.

        Args:
            name: The original assistant name (already normalized)

        Returns:
            The processed name - either the original or truncated with hash for uniqueness
        """
        # Calculate max allowed length for the assistant name
        # handoff_tool_name = "transfer_to_" + name must be <= 64 chars
        prefix_length = len(LangGraphAgent.SUPERVISOR_HANDOFF_TOOL_PREFIX) + 1  # +1 for underscore
        max_name_length = LangGraphAgent.ASSISTANT_NAME_MAX_LENGTH - prefix_length

        # If name fits, return it as is
        if len(name) <= max_name_length:
            return name

        # Name needs truncation - generate hash for uniqueness to avoid collisions
        hash_name = hashlib.sha256(name.encode()).hexdigest()

        # Calculate remaining length for original name part
        hash_length = 10  # Use a fixed portion of the hash for consistency
        remaining_length = max_name_length - hash_length - 1  # -1 for underscore separator

        if remaining_length > 0:
            # Keep part of the original name and add hash portion for uniqueness
            # Use last characters of hash for better distribution
            truncated_name = name[:remaining_length] + "_" + hash_name[-hash_length:]
        else:
            # Not enough space for original name, use hash only (last characters)
            truncated_name = hash_name[-max_name_length:]

        return truncated_name

    def get_original_sub_assistant_name(self, truncated_name: str) -> str:
        """
        Retrieve the original sub-assistant name from a truncated name.

        This is used by callbacks to display the full original name in thoughts,
        while the truncated name is used internally for tool name constraints.

        Args:
            truncated_name: The truncated name (without "transfer_to_" prefix)

        Returns:
            The original assistant name if a mapping exists, otherwise the truncated name
        """
        return self._sub_assistant_name_mapping.get(truncated_name, truncated_name)
