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
from time import time
from typing import List, Optional

import langgraph_supervisor.supervisor
from codemie_tools.base.file_object import FileObject
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph_supervisor import create_supervisor, create_handoff_tool
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from codemie.agents.assistant_agent import TaskResult
from codemie.agents.callbacks.agent_invoke_callback import AgentInvokeCallback
from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.agents.callbacks.monitoring_callback import MonitoringCallback
from codemie.agents.callbacks.tool_error_capture_callback import ToolErrorCaptureCallback
from codemie.agents.smart_react_agent import create_smart_react_agent
from codemie.agents.utils import ExecutionErrorEnum, suppress_stdout
from codemie.agents.utils import validate_json_schema, get_run_config, handle_agent_exception
from codemie.chains.base import StreamedGenerationResult, GenerationResult, ThoughtAuthorType, ThoughtOutputFormat
from codemie.chains.pure_chat_chain import PureChatChain
from codemie.configs import config
from codemie.configs.logger import logger, set_logging_info
from codemie.core.constants import (
    ChatRole,
    BackgroundTaskStatus,
    UniqueThoughtParentIds,
    REQUEST_ID,
    USER_ID,
    USER_NAME,
    LLM_MODEL,
    AGENT_NAME,
    ASSISTANT_ID,
    PROJECT,
    OUTPUT_FORMAT,
    ToolNamePrefix,
)
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.models import ChatMessage, AssistantChatRequest
from codemie.core.thread import ThreadedGenerator
from codemie.core.utils import extract_text_from_llm_output, calculate_tokens, unpack_json_strings
from codemie.rest_api.models.assistant import AssistantBase
from codemie.rest_api.security.user import User
from codemie.service.background_tasks_service import BackgroundTasksService
from codemie.service.file_service.image_service import ImageService
from codemie.service.llm_service.llm_service import LLMService
from codemie.service.llm_service.utils import set_llm_context
from codemie.templates.agents.assistant_base import markdown_response_prompt
from codemie.core.exceptions import TokenLimitExceededException

# LangGraph supervisor agent adds wrong params to our LLM in tools binding stage
# which causes parallel tools calls errors
# even if we specify "parallel_tool_calls=False"
langgraph_supervisor.supervisor._supports_disable_parallel_tool_calls = lambda x: any(
    model in getattr(x, "model_name", "model") for model in config.DISABLE_PARALLEL_TOOLS_CALLING_MODELS
)


def _extract_image_blocks(artifact: object) -> list[dict]:
    """Convert a tool artifact into a list of base64 image content blocks."""
    if not isinstance(artifact, list):
        return []
    return [
        {
            "type": "image",
            "source_type": "base64",
            "data": item["data"],
            "mime_type": item["mime_type"],
        }
        for item in artifact
        if isinstance(item, dict) and "data" in item and "mime_type" in item
    ]


def _image_artifact_pre_model_hook(state: dict) -> dict:
    """Inject image artifacts from ToolMessages into the LLM input.

    This is a native LangGraph ``pre_model_hook`` for ``create_react_agent``.
    It scans the most recent round of tool messages (everything after the
    last ``AIMessage``) for ``ToolMessage.artifact`` entries containing
    downloaded image data and, when found, appends a ``HumanMessage`` with
    the images to the transient ``llm_input_messages`` list.

    ``llm_input_messages`` is used as LLM input **without** persisting to
    graph state, so base64 payloads never leak into conversation history.
    """
    messages = state.get("messages", [])
    if not messages:
        return {"llm_input_messages": messages}

    image_blocks: list[dict] = []
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            break
        if isinstance(msg, ToolMessage) and getattr(msg, "artifact", None):
            image_blocks.extend(_extract_image_blocks(msg.artifact))

    if not image_blocks:
        return {"llm_input_messages": messages}

    injected = HumanMessage(
        content=[
            {"type": "text", "text": "[Attached images from the tool response above]"},
            *image_blocks,
        ]
    )
    return {"llm_input_messages": [*messages, injected]}


class LangGraphAgent:
    # When this agent is run as part of a workflow (instead of natively within LangGraph),
    # LangGraph overrides the max_concurrency of all subgraphs.
    # This is problematic because the agent's execution
    # should remain independent when instance methods are used.
    MAX_CONCURRENCY = 10000
    SUPERVISOR_HANDOFF_TOOL_PREFIX = "transfer_to"
    # Maximum allowed length for assistant (agent) name after normalization
    ASSISTANT_NAME_MAX_LENGTH = 64

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
        self.trace_context = trace_context  # Store for trace unification

        # Smart tool selection/lookup configuration
        # Controls both:
        # 1. Dynamic tool selection (selecting subset from available tools)
        # 2. Smart tool lookup (finding tools when no toolkits configured)
        self.smart_tool_selection_enabled = smart_tool_selection_enabled
        self.tool_selection_limit = tool_selection_limit or config.TOOL_SELECTION_LIMIT

        set_logging_info(uuid=request_uuid, user_id=user.id, user_email=user.username)
        self.agent_name = agent_name
        self.verbose = verbose
        self.is_react = is_react
        self.stream_steps = stream_steps

        # Tool error capture callback (LangChain-based)
        self.tool_error_callback = ToolErrorCaptureCallback(agent_name=agent_name)

        self.agent_executor = self.init_agent()

        self._supervisor_state: Optional[str] = None

    ###### Init logic ######

    def init_agent(self):
        llm = self._initialize_llm()
        parallel_tool_calling = any(model in self.llm_model for model in config.DISABLE_PARALLEL_TOOLS_CALLING_MODELS)

        # Don't pre-bind tools when using smart tool selection (tools are bound inside create_react_agent)
        # For multi agents, tools binding happens inside create_supervisor
        should_prebind_tools = (
            self.tools
            and parallel_tool_calling
            and not self.subagents
            and not self.smart_tool_selection_enabled  # NEW: Don't pre-bind for smart tool selection
        )

        if should_prebind_tools:
            llm = llm.bind_tools(self.tools, parallel_tool_calls=False)

        self.callbacks = self.configure_callbacks()
        system_prompt = self._get_system_prompt(from_request=True)

        agent_name = self.format_assistant_name(self.agent_name)

        if self.subagents:
            # Use supervisor pattern for multi-agent systems
            # Create custom handoff tools with descriptions for each subagent
            handoff_tools = self._create_handoff_tools()

            # Combine regular tools with handoff tools
            all_tools = (self.tools or []) + handoff_tools

            agent = create_supervisor(
                model=llm,
                agents=self.subagents,
                tools=all_tools,  # Include handoff tools with descriptions
                prompt=system_prompt,
                add_handoff_back_messages=True,
                output_mode="full_history",
                response_format=self.output_schema,
            ).compile()
        else:
            # Use create_smart_react_agent which handles threshold checks and fallback internally
            # If smart tools are disabled or below threshold, it will use standard create_react_agent
            agent = create_smart_react_agent(
                model=llm,
                tools=self.tools,
                prompt=system_prompt,
                response_format=self.output_schema,
                name=agent_name,
                tool_selection_enabled=self.smart_tool_selection_enabled,
                tool_selection_limit=self.tool_selection_limit,
                parallel_tool_calls=False if parallel_tool_calling else None,
                # Runs for all agents, but does nothing unless a tool
                # returns image artifacts (e.g. Jira with screenshots).
                pre_model_hook=_image_artifact_pre_model_hook,
            )

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
            normalized_name = self.format_assistant_name(agent_name)

            # Create handoff tool with description
            # Add "Sub-assistant" prefix if description exists, otherwise use default message
            final_description = f"Sub-assistant: {description}" if description else f'Hand off task to {agent_name}'
            handoff_tool = create_handoff_tool(
                agent_name=normalized_name,
                name=f"{self.SUPERVISOR_HANDOFF_TOOL_PREFIX}_{normalized_name}",
                description=final_description,
            )

            handoff_tools.append(handoff_tool)
            logger.debug(f"Created handoff tool for {agent_name}: {description}")

        return handoff_tools

    def configure_callbacks(self) -> List[BaseCallbackHandler]:
        # Initialize and prepare callbacks
        callbacks = list(self.callbacks or [])
        agent_streaming_callback = AgentStreamingCallback(self.thread_generator)
        default_callbacks = [
            MonitoringCallback(),
            self.tool_error_callback,
            *([agent_streaming_callback] if self.stream_steps and self.thread_generator else [AgentInvokeCallback()]),
        ]

        if self.stream_steps and self.thread_generator:
            self.supervisor_callbacks.append(AgentStreamingCallback(self.thread_generator))
        # Add unique default callbacks
        callbacks.extend(callback for callback in default_callbacks if self._is_unique_callback(callbacks, callback))

        return callbacks

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
        except Exception as e:
            user_message, _ = handle_agent_exception(e)
            return user_message

    def invoke_task(self, workflow_input: str = "", history=None, args=None) -> TaskResult:
        """
        Used in workflows invocation
        """
        if args is None:
            args = {}
        if history is None:
            history = []
        set_llm_context(self.assistant, None, self.user)
        logger.debug(f"Invoking workflow task. Agent={self.agent_name}, Input={workflow_input}, ChatHistory={history}")
        try:
            inputs = self._get_inputs(workflow_input, history)
            inputs.update(args)
            agent_response = self._invoke_agent(inputs)

            return TaskResult.from_agent_response(agent_response)
        except Exception as e:
            user_message, _ = handle_agent_exception(e)
            return TaskResult.failed_result(user_message, original_exc=e)

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

            token_used = calculate_tokens(json.dumps(output))

            time_elapsed = time() - start_time
            if background_task_id:
                BackgroundTasksService().update(
                    task_id=background_task_id, status=BackgroundTaskStatus.COMPLETED, final_output=output
                )

            # Include tool errors and callback errors in response
            return GenerationResult(
                generated=output,
                time_elapsed=time_elapsed,
                input_tokens_used=None,
                tokens_used=token_used,
                success=True,
                agent_error=None,
                tool_errors=self.tool_error_callback.tool_errors if self.tool_error_callback.has_errors() else None,
            )
        except Exception as e:
            time_elapsed = time() - start_time
            user_message, _ = handle_agent_exception(e)

            if background_task_id:
                BackgroundTasksService().update(
                    task_id=background_task_id, status=BackgroundTaskStatus.FAILED, final_output=user_message
                )

            # Include tool errors and callback errors even on agent failure
            return GenerationResult(
                generated=user_message,
                time_elapsed=time_elapsed,
                input_tokens_used=None,
                tokens_used=None,
                success=False,
                agent_error=None,
                tool_errors=self.tool_error_callback.tool_errors if self.tool_error_callback.has_errors() else None,
            )

    def stream(self):
        """
        This method enables assistant streaming, with the stream’s destination generally being a user interface (UI).
        To handle the incoming stream content, you can use a thread generator.
        """
        set_logging_info(
            uuid=self.request_uuid,
            user_id=self.user.id,
            conversation_id=self.conversation_id,
            user_email=self.user.username,
        )
        set_llm_context(self.assistant, None, self.user)

        execution_start = time()
        chunks_collector = []

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
        except Exception as e:
            time_elapsed = time() - execution_start
            user_message, llm_error_code = handle_agent_exception(e)
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
            self.thread_generator.close()

    def invoke_with_a2a_output(self, query: str = "") -> dict:
        try:
            inputs = self._get_inputs(query)
            response = self._invoke_agent(inputs).generated
            return {"is_task_complete": True, "require_user_input": False, "content": response}
        except Exception as e:
            user_message, _ = handle_agent_exception(e)
            return {"is_task_complete": False, "require_user_input": True, "content": user_message}

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

    def _process_chunks(
        self,
        chunks_collector: List[str],
        config: BaseSettings,
        llm_error_code: str | None = None,
    ) -> tuple[str, str | None]:
        """Build final generated text and determine ``execution_error``.

        When *llm_error_code* is provided it takes precedence: the friendly
        LLM message (already in chunks) is safe for the end-user, so we
        always join chunks and propagate the specific error code.
        """
        if llm_error_code:
            return "".join(chunks_collector), llm_error_code

        if config.HIDE_AGENT_STREAMING_EXCEPTIONS:
            if any("guardrail" in chunk.lower() for chunk in chunks_collector):
                return config.CUSTOM_GUARDRAILS_MESSAGE, ExecutionErrorEnum.GUARDRAILS.value
            return config.CUSTOM_STACKTRACE_MESSAGE, ExecutionErrorEnum.STACKTRACE.value

        return "".join(chunks_collector), None

    def _invoke_agent(self, inputs) -> GenerationResult:
        logger.debug(f"Invoking task. Agent={self.agent_name}. Inputs={inputs}")
        set_llm_context(self.assistant, None, self.user)
        try:
            output = self._stream_graph(inputs, config=self._get_run_config())

            # Include tool errors and callback errors collected during execution
            response = GenerationResult(
                generated=output,
                time_elapsed=None,
                input_tokens_used=None,
                tokens_used=None,
                success=True,
                agent_error=None,
                tool_errors=self.tool_error_callback.tool_errors if self.tool_error_callback.has_errors() else None,
            )

            logger.debug(f"Invoking task. Agent={self.agent_name}. Response={response}")
            return response
        except Exception as e:
            user_message, _ = handle_agent_exception(e)
            return GenerationResult(
                generated=user_message,
                time_elapsed=None,
                input_tokens_used=None,
                tokens_used=None,
                success=False,
                agent_error=None,
                tool_errors=self.tool_error_callback.tool_errors if self.tool_error_callback.has_errors() else None,
            )

    def _get_run_config(self):
        tags = ["execution_engine:langgraph_agent"]

        # Get assistant version if available
        assistant_version = None
        if self.assistant and hasattr(self.assistant, 'version'):
            assistant_version = self.assistant.version

        run_config = get_run_config(
            request=self.request,
            llm_model=self.llm_model,
            agent_name=self.agent_name,
            conversation_id=self.conversation_id,
            username=self.user.username if self.user and self.user.username else None,
            additional_tags=tags,
            assistant_version=assistant_version,
            trace_context=self.trace_context,  # Pass trace context for workflow unification
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
        return not any(isinstance(cb, type(candidate)) for cb in callbacks)

    def _agent_streaming(self, chunks_collector: list[str]) -> str | dict | BaseModel:
        inputs = self._get_inputs()
        config = self._get_run_config()

        result = self._stream_graph(inputs, config, chunks_collector)
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
            if target_field in value and self.is_valid_ai_message(message := value[target_field]["messages"][-1]):
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

        return agent_inputs

    def __parse_message_type(self, value, chunks_collector: list[str]):
        message, metadata = value

        if self.is_valid_ai_message(message):
            token = extract_text_from_llm_output(message.content)
            self._process_agent_streaming(token, chunks_collector)
        elif self.is_finish_reason_stop(message):
            if metadata.get("langgraph_node") == "agent":
                self._on_llm_end(response=message)

    def __parse_update_type(self, value):
        # Tool call request
        if "agent" in value and self.is_finish_reason_tool_calls(value["agent"]["messages"][-1]):
            message = value["agent"]["messages"][-1]
            # Validate response wasn't truncated before executing tool
            self._safe_check_for_truncation(message)
            tool_name, tool_args = self._get_tool_call_args(message)
            logger.debug(f"Calling Tool: {tool_name} with input {tool_args}")
            self._on_tool_start(tool_name, tool_args)
        # Tools calling result
        elif "tools" in value:
            for action in value["tools"]["messages"]:
                if isinstance(action, ToolMessage):
                    logger.debug(f"Tool {action.name} call result: {action.content}")
                    self._parse_tool_message(action)

    def __parse_supervisor_message_type(self, value, chunks_collector: list[str]):
        message, _ = value

        if self.is_valid_ai_message(message) and not message.response_metadata.get("__is_handoff_back"):
            token = extract_text_from_llm_output(message.content)
            self._process_agent_streaming(token, chunks_collector)
        elif self.is_finish_reason_stop(message):
            self._on_llm_end(response=message)

    def __parse_supervisor_update_type(self, value: dict):
        messages = list(value.values())[0].get("messages", [])
        if not messages:
            return
        last_message = messages[-1]

        # Tool calls parsing
        if isinstance(last_message, AIMessage) and self.is_finish_reason_tool_calls(last_message):
            # Validate response wasn't truncated before executing tool
            self._safe_check_for_truncation(last_message)
            tool_name, tool_args = self._get_tool_call_args(last_message)
            # Detect handoff event
            if self._check_is_handoff_tool(tool_name):
                if content := extract_text_from_llm_output(last_message.content):
                    self._on_llm_end(content)
                agent_name = self._extract_agent_name_from_tool(tool_name)
                self._on_supervisor_handoff(f"{ToolNamePrefix.AGENT.value}_{agent_name}")
                self.set_thread_context({}, UniqueThoughtParentIds.LATEST.value)
            else:
                # Regular tool calls
                logger.debug(f"Calling Tool: {tool_name} with input {tool_args}")
                self._on_tool_start(tool_name, tool_args)
        elif last_message.response_metadata.get("__is_handoff_back"):
            logger.debug("Handoff back to supervisor")
            subassistant_answer = extract_text_from_llm_output(messages[-3].content)
            self._on_subassistant_back(subassistant_answer)
            self.set_thread_context({}, None)
        elif destination := last_message.response_metadata.get("__handoff_destination"):
            logger.debug(f"Transerring to {destination}")
        elif isinstance(last_message, ToolMessage):
            logger.debug(f"Tool {last_message.name} call result: {last_message.content}")
            self._parse_tool_message(last_message)

    def _process_chunk_for_agent(self, chunk, chunks_collector: list[str]):
        chunk_type, value = chunk
        if chunk_type == "messages":
            self.__parse_message_type(value, chunks_collector)
        elif chunk_type == "updates":
            self.__parse_update_type(value)

    def _process_chunk_for_supervisor(self, chunk, chunks_collector: list[str]):
        _, chunk_type, value = chunk
        if chunk_type == "messages":
            self.__parse_supervisor_message_type(value, chunks_collector)
        elif chunk_type == "updates":
            self.__parse_supervisor_update_type(value)

    def process_chunk(self, chunk, chunks_collector: List[str]):
        if self.subagents:
            self._process_chunk_for_supervisor(chunk, chunks_collector)
        else:
            self._process_chunk_for_agent(chunk, chunks_collector)

    def get_thoughts_from_callback(self):
        if self.is_pure_chain():
            return []
        return next((callback.thoughts for callback in self.callbacks if hasattr(callback, "thoughts")), [])

    def is_finish_reason_stop(self, message: AIMessage) -> bool:
        response_medatada = message.response_metadata
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

    def _parse_tool_message(self, action: ToolMessage):
        if action.status == "error":
            # Tool errors are now automatically captured by ToolErrorCaptureCallback
            # via LangChain's on_tool_error callback method - no manual capture needed

            # Still notify other callbacks (for logging, UI streaming, etc.)
            self._on_tool_error(action.content)
        else:
            if action.status != "success":
                message = f"Unknown tool action status: {action.status}"
                message += f"\nAssistant: {self.agent_name}, request_uuid: {self.request_uuid}"
                message += "\nExpected 'success' or 'error'"
                logger.warning(message)
            self._on_tool_end(action.content)

    def _process_agent_streaming(self, token, chunks_collector: list[str]):
        self._on_llm_new_token(token)
        LangGraphAgent.process_output(token, chunks_collector)

    ###### Callbacks ########

    def _on_llm_start(self):
        for callback in self.callbacks:
            try:
                callback.on_llm_start(None, None, run_id=None)
            except Exception as e:
                logger.error(f"On LLM start callback {callback} error: {e}")

    def _on_llm_new_token(self, token):
        for callback in self.callbacks:
            try:
                callback.on_llm_new_token(token=token, run_id=None)
            except Exception as e:
                logger.error(f"On LLM new token callback {callback} error: {e}")

    def _on_llm_end(self, response):
        for callback in self.callbacks:
            try:
                callback.on_llm_end(response, run_id=None)
            except Exception as e:
                logger.error(f"On llm end callback {callback} error: {e}")

    def _on_llm_error(self, error: BaseException):
        for callback in self.callbacks:
            try:
                callback.on_llm_error(error, run_id=None)
            except Exception as e:
                logger.error(f"On llm error callback {callback} error: {e}")

    def _on_tool_start(self, tool_name: str, input_str: str):
        serialized = {"name": tool_name}
        for callback in self.callbacks:
            try:
                callback.on_tool_start(serialized, input_str, run_id=None)
            except Exception as e:
                logger.error(f"On tool start callback {callback} error: {e}")

    def _on_tool_end(self, output):
        for callback in self.callbacks:
            try:
                callback.on_tool_end(output, run_id=None)
            except Exception as e:
                logger.error(f"On tool end callback {callback} error: {e}")

    def _on_supervisor_handoff(self, destination: str, input_str: str = ""):
        serialized = {"name": destination}
        metadata = {OUTPUT_FORMAT: ThoughtOutputFormat.MARKDOWN.value}
        for callback in self.supervisor_callbacks:
            try:
                callback.on_tool_start(serialized, input_str, run_id=None, metadata=metadata)
            except Exception as e:
                logger.error(f"On supervisor hanoff callback {callback} error: {e}")

    def _on_subassistant_back(self, output):
        for callback in self.supervisor_callbacks:
            try:
                callback.on_tool_end(output, run_id=None)
            except Exception as e:
                logger.error(f"On supervisor back {callback} error: {e}")

    def _on_tool_error(self, output):
        for callback in self.callbacks:
            try:
                callback.on_tool_error(output, run_id=None)
            except Exception as e:
                logger.error(f"On tool error callback {callback} error: {e}")

    def _on_chain_end(self, output):
        for callback in self.callbacks:
            try:
                callback.on_chain_end(output, run_id=None)
            except Exception as e:
                logger.error(f"On chain end callback {callback} error: {e}")

    def is_pure_chain(self) -> bool:
        return isinstance(self.agent_executor, PureChatChain)

    ###### Static helpers ######

    @staticmethod
    def _preprocess_output_schema(output_schema: dict | BaseModel) -> dict | BaseModel:
        if isinstance(output_schema, dict):
            check = validate_json_schema(output_schema)
            if not check:
                raise ValueError(f"Wrong JSON Schema was put in agent: {output_schema}")
            # If title doesn't exist, we manually add it
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
    def process_output(output, chunks_collector: List[str]):
        message = extract_text_from_llm_output(output)
        chunks_collector.append(message)

    @staticmethod
    def format_assistant_name(name: str) -> str:
        # Replace forbidden characters with underscore
        name = re.sub(r'[\s<|\\/>]', '_', name)
        # Remove any character that's not alphanumeric or underscore
        name = re.sub(r'\W', '', name).lower()

        name = name.replace(" ", "_")
        # Truncate name to a maximum defined by class constant
        return name[: LangGraphAgent.ASSISTANT_NAME_MAX_LENGTH]

    @classmethod
    def _check_is_handoff_tool(cls, tool_name: str) -> bool:
        return tool_name.startswith(cls.SUPERVISOR_HANDOFF_TOOL_PREFIX)

    @classmethod
    def _extract_agent_name_from_tool(cls, tool_name: str) -> str:
        return tool_name[len(cls.SUPERVISOR_HANDOFF_TOOL_PREFIX) + 1 :]

    @staticmethod
    def _transform_history(history: List[ChatMessage]) -> list:
        """Convert history to list of chain-compatible messages"""
        transformed_history = []

        for item in history:
            # If already transformed (HumanMessage/AIMessage), keep as is
            if isinstance(item, (HumanMessage, AIMessage)):
                transformed_history.append(item)
            # Otherwise, transform ChatMessage to HumanMessage/AIMessage
            elif hasattr(item, 'role'):
                if item.role == ChatRole.USER:
                    transformed_history.append(HumanMessage(content=item.message))
                elif item.role == ChatRole.ASSISTANT:
                    transformed_history.append(AIMessage(content=item.message))

        return transformed_history

    @staticmethod
    def _get_node_name_from_metadata(node: tuple, default_name: str = "supervisor") -> str:
        return node[0].split(":")[0] if node else default_name

    @classmethod
    def _filter_history(cls, history: list) -> list:
        return [item for item in history if item.content]

    def set_thread_context(self, context: dict, parent_thought_id: str | None):
        self.thread_context = context
        for callback in self.callbacks:
            if isinstance(callback, AgentStreamingCallback):
                callback.parent_id = parent_thought_id
                callback.context = context

    def set_subagent_execution(self):
        for callback in self.callbacks:
            if hasattr(callback, "_current_thought"):
                callback.set_current_thought()
                callback._current_thought.author_type = ThoughtAuthorType.Agent.value

    @property
    def _task(self):
        if not self.request.file_names:
            return self.request.text

        file_names = [FileObject.from_encoded_url(name).name for name in self.request.file_names]
        return f"{self.request.text}{"\n Attached files: " + ", ".join(file_names)}"
