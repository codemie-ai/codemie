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
import traceback
from time import time
from typing import Any, List, Optional, Sequence, Tuple

import jinja2
from deprecated import deprecated
from langchain.agents import AgentExecutor, create_tool_calling_agent, create_json_chat_agent
from langchain.agents.format_scratchpad.tools import _create_tool_message
from langchain.agents.output_parsers.tools import ToolAgentAction
from langchain_core.agents import AgentAction
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, BaseMessage
from langchain_core.prompts import (
    ChatPromptTemplate,
    MessagesPlaceholder,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from codemie.agents.callbacks.agent_invoke_callback import AgentInvokeCallback
from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.agents.callbacks.monitoring_callback import MonitoringCallback
from codemie.agents.structured_tool_agent import create_structured_tool_calling_agent
from codemie.agents.utils import (
    ExecutionErrorEnum,
    handle_agent_exception,
    render_text_description_and_args,
    validate_json_schema,
    get_run_config,
)
from codemie.chains.base import StreamedGenerationResult, GenerationResult
from codemie.chains.pure_chat_chain import PureChatChain
from codemie.configs import config
from codemie.configs.logger import logger, set_logging_info
from codemie.core.constants import (
    ChatRole,
    BackgroundTaskStatus,
    REQUEST_ID,
    USER_ID,
    USER_NAME,
    LLM_MODEL,
    AGENT_NAME,
    PROJECT,
    OUTPUT_FORMAT,
)
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.models import ChatMessage, AssistantChatRequest
from codemie.core.thread import ThreadedGenerator
from codemie.core.utils import extract_text_from_llm_output, calculate_tokens
from codemie_tools.base.file_object import FileObject
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService
from codemie.service.background_tasks_service import BackgroundTasksService
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.mcp.models import MCPToolInvocationResponse
from codemie.templates.agents.assistant_base import json_react_template_v2, user_prompt, markdown_response_prompt


class InvalidPromptTemplate(Exception):
    pass


class TaskResult(BaseModel):
    result: str
    success: bool
    intermediate_steps: list = Field(default_factory=list)
    original_exc: Optional[Any] = None

    @classmethod
    def failed_result(cls, result: str, original_exc: Any):
        return cls(result=result, success=False, original_exc=original_exc)

    @classmethod
    def from_agent_response(cls, response):
        if isinstance(response, GenerationResult):
            result = response.generated
            # Serialize objects from structured outputs as strings to pass to the next step.
            if isinstance(response.generated, dict):
                result = json.dumps(response.generated)
            elif isinstance(response.generated, BaseModel):
                result = response.generated.model_dump_json()
            return cls(result=result or '', success=response.success)

        # Determine the result and success
        if 'output' in response:
            result = extract_text_from_llm_output(response['output'])
            success = True
            if 'intermediate_steps' in response:
                return cls(result=result, success=success, intermediate_steps=response['intermediate_steps'])
        elif 'generated' in response:
            result = response['generated']
            success = True
        else:
            result = ''
            success = False
        return cls(result=result, success=success)


def get_react_json_prompt_template(system_prompt):
    messages = [
        SystemMessagePromptTemplate.from_template(system_prompt + json_react_template_v2),
        MessagesPlaceholder("chat_history", optional=True),
        MessagesPlaceholder(variable_name="messages", optional=True),
        HumanMessagePromptTemplate.from_template(user_prompt),
        MessagesPlaceholder("agent_scratchpad"),
    ]
    return ChatPromptTemplate.from_messages(messages)


class AIToolsAgent:
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
        verbose: bool = config.verbose,
        recursion_limit: int = config.AI_AGENT_RECURSION_LIMIT,
        thread_generator: ThreadedGenerator = None,
        stream_steps: bool = True,
        handle_tool_error: bool = True,
        throw_truncated_error: bool = False,
        assistant: Optional[Assistant] = None,
        trace_context=None,  # For workflow trace unification
    ):
        self.agent_name = agent_name
        self.description = description
        self.tools = tools
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
        self.output_schema = self._preprocess_output_schema(output_schema) if output_schema else None
        self.assistant = assistant
        self.trace_context = trace_context  # Store for trace unification

        set_logging_info(uuid=request_uuid, user_id=user.id, user_email=user.username)
        self.agent_name = agent_name
        self.verbose = verbose
        self.is_react = is_react
        self.stream_steps = stream_steps
        self.agent_executor = self.init_agent()

    def _is_unique_callback(self, callbacks: List[BaseCallbackHandler], candidate) -> bool:
        """Check if callback of this type doesn't exist in the list."""
        return not any(isinstance(cb, type(candidate)) for cb in callbacks)

    @staticmethod
    def _preprocess_output_schema(output_schema: dict | BaseModel) -> dict | BaseModel:
        if isinstance(output_schema, dict):
            check = validate_json_schema(output_schema)
            if not check:
                raise ValueError(f"Wrong JSON Schema was put in agent: {output_schema}")
            # If title doesn't exist, we manually add it
            output_schema["title"] = output_schema.get("title", "StructuredOutput")
        return output_schema

    def configure_callbacks(self, llm) -> List[BaseCallbackHandler]:
        # Initialize and prepare callbacks
        callbacks = list(self.callbacks or [])
        default_callbacks = [
            MonitoringCallback(),
            *(
                [AgentStreamingCallback(self.thread_generator)]
                if self.stream_steps and self.thread_generator
                else [AgentInvokeCallback()]
            ),
        ]

        # Add unique default callbacks
        callbacks.extend(callback for callback in default_callbacks if self._is_unique_callback(callbacks, callback))

        # Update LLM callbacks
        if hasattr(llm, 'callbacks') and llm.callbacks is not None:
            llm.callbacks.extend(callbacks)
        else:
            llm.callbacks = callbacks

        return callbacks

    def init_agent(self):
        llm = self._initialize_llm()
        callbacks = self.configure_callbacks(llm)

        if not self.tools:
            if self.output_schema:
                llm = llm.with_structured_output(self.output_schema)
            return self._create_fallback_agent(llm)

        if self.llm_model in llm_service.get_react_llms():
            agent = self._create_react_agent(llm)
        else:
            if self.output_schema:
                agent = self._create_structured_tool_calling_agent(llm)
            else:
                agent = self._create_tool_calling_agent(llm)

        # During agent creation the new tools can be added.
        self._configure_tools(callbacks)

        return AgentExecutor(
            agent=agent,
            tools=self.tools,
            verbose=self.verbose,
            max_iterations=self.recursion_limit,
            handle_parsing_errors=True,
            max_execution_time=None,  # Returning this back, but it is kind a dangerous option
            callbacks=callbacks,
            return_intermediate_steps=True,
        )

    def _initialize_llm(self):
        return get_llm_by_credentials(
            llm_model=self.llm_model, temperature=self.temperature, top_p=self.top_p, request_id=self.request_uuid
        )

    def _create_fallback_agent(self, llm):
        agent = PureChatChain(
            request=self.request,
            system_prompt=self._get_system_prompt(from_request=True),
            llm_model=self.llm_model,
            llm=llm,
            thread_generator=self.thread_generator,
            user=self.user,
            agent_name=self.agent_name,
        )
        logger.debug(f"LLMChain initialized for {self.agent_name} as fallback")
        return agent

    def _get_system_prompt(self, from_request: bool = False):
        system_prompt = self.system_prompt

        if from_request:
            system_prompt = self.request.system_prompt or system_prompt

        if config.LLM_REQUEST_ADD_MARKDOWN_PROMPT:
            system_prompt = system_prompt + " " + markdown_response_prompt

        return system_prompt

    def _configure_tools(self, callbacks):
        tool_metadata = {
            REQUEST_ID: self.request_uuid,
            USER_ID: self.user.id,
            USER_NAME: self.user.name,
            LLM_MODEL: self.llm_model,
            AGENT_NAME: self.agent_name,
            PROJECT: self.assistant.project if self.assistant else None,
            **(self.request.metadata or {}),
        }
        for tool in self.tools:
            tool.callbacks = callbacks

            if hasattr(tool, 'metadata') and tool.metadata:
                tool.metadata.update(tool_metadata)
            else:
                tool.metadata = tool_metadata

            if hasattr(tool, OUTPUT_FORMAT):
                tool.metadata[OUTPUT_FORMAT] = tool.output_format

            if hasattr(tool, 'throw_truncated_error'):
                tool.throw_truncated_error = self.throw_truncated_error

            tool.handle_tool_error = self.handle_tool_error

    def _create_react_agent(self, llm):
        return create_json_chat_agent(
            llm=llm,
            tools=self.tools,
            tools_renderer=render_text_description_and_args,
            prompt=get_react_json_prompt_template(self.system_prompt),
            # This stop sequence is shorter due to Boto3's limitations regarding end sequences.
            stop_sequence=["\nObserva"],
        )

    def _create_structured_tool_calling_agent(self, llm):
        return (
            create_structured_tool_calling_agent(
                llm,
                tools=self.tools,
                prompt=self.get_prompt_template(),
                structured_output=self.output_schema,
            )
            | self._do_nothing
        )

    def _create_tool_calling_agent(self, llm):
        return (
            create_tool_calling_agent(
                llm=llm,
                tools=self.tools,
                prompt=self.get_prompt_template(),
                message_formatter=_format_to_tool_messages,
            )
            | self._do_nothing
        )

    def is_pure_chain(self) -> bool:
        return isinstance(self.agent_executor, PureChatChain)

    def get_thoughts_from_callback(self):
        if self.is_pure_chain():
            return []
        return next(
            (callback.thoughts for callback in self.agent_executor.callbacks if hasattr(callback, "thoughts")), []
        )

    def _get_inputs(self, input_text: str = "", history=None):
        if history is None:
            history = []
        input_task = input_text if input_text else self._task
        raw_history = history if history else self.request.history
        history = self._filter_history(self._transform_history(raw_history))
        inputs = {"input": input_task, "chat_history": history}

        return inputs

    # Replace with https://github.com/google/A2A/blob/main/samples/python/agents/langgraph/agent.py
    def invoke_with_a2a_output(self, query: str = "") -> dict:
        try:
            if self.is_pure_chain():
                response = self.agent_executor.generate().generated
            else:
                inputs = self._get_inputs(query)
                response = self._invoke_agent(inputs).get('output', '')
            return {"is_task_complete": True, "require_user_input": False, "content": response}
        except Exception as e:
            logger.error(f"Invoking agent. Agent={self.agent_name}. Result=Failed", exc_info=True)
            return {"is_task_complete": False, "require_user_input": True, "content": str(e)}

    def invoke(self, input: str = "", history=None, args=None) -> str | BaseModel | dict:
        if args is None:
            args = {}
        if history is None:
            history = []
        if self.is_pure_chain():
            response = self.agent_executor.generate()
            return response.generated
        try:
            inputs = self._get_inputs(input, history)
            inputs.update(args)
            return self._invoke_agent(inputs).get('output', '')
        except Exception:
            stacktrace = traceback.format_exc()
            logger.error(f"AI Agent run failed with error: {stacktrace}", exc_info=True)
            return f"AI Agent run failed with error: {stacktrace}"

    def invoke_task(self, workflow_input: str = "", history=None, args=None) -> TaskResult:
        if args is None:
            args = {}
        if history is None:
            history = []
        filtered_history = self._filter_history(history)
        logger.debug(
            f"Invoking workflow task. Agent={self.agent_name}, Input={workflow_input}, ChatHistory={filtered_history}"
        )
        try:
            if self.is_pure_chain():
                inputs = {"question": workflow_input, "chat_history": filtered_history}
            else:
                inputs = {"input": workflow_input, "chat_history": filtered_history}
                inputs.update(args)

            return TaskResult.from_agent_response(self._invoke_agent(inputs))
        except Exception as e:
            error_message = f"Invoking workflow task. Agent={self.agent_name}. Result=Failed"
            logger.error(error_message, exc_info=True)
            return TaskResult.failed_result(str(e), original_exc=e)

    def _invoke_agent(self, inputs):
        logger.debug(f"Invoking task. Agent={self.agent_name}. Inputs={inputs}")
        # Config will be retrieved from PureChatChain
        if self.assistant and BedrockOrchestratorService.is_bedrock_assistant(self.assistant):
            logger.info("Invoking bedrock assistant generation.")
            response = BedrockOrchestratorService.invoke_bedrock_assistant(
                assistant=self.assistant,
                input_text=inputs.get("question", ""),
                conversation_id=self.conversation_id,
                chat_history=inputs.get("chat_history", []),
            )
            output = response.get("output", "")
            logger.info(f"Bedrock response: {output}")
            response = GenerationResult(
                generated=output,
                time_elapsed=None,
                input_tokens_used=None,
                tokens_used=None,
                success=True,
            )  # Bedrock doesn't provide token usage
        else:
            if self.is_pure_chain():
                response = self.agent_executor.invoke(inputs)
            else:
                response = self.agent_executor.invoke(inputs, config=self._get_run_config())

        logger.debug(f"Invoking task. Agent={self.agent_name}. Response={response}")
        return response

    def _get_run_config(self):
        tags = ["execution_engine:assistant_agent"]

        # Get assistant version if available
        assistant_version = None
        if self.assistant and hasattr(self.assistant, 'version'):
            assistant_version = self.assistant.version

        return get_run_config(
            request=self.request,
            llm_model=self.llm_model,
            agent_name=self.agent_name,
            conversation_id=self.conversation_id,
            username=self.user.username if self.user and self.user.username else None,
            additional_tags=tags,
            assistant_version=assistant_version,
            trace_context=self.trace_context,  # Pass trace context for workflow unification
        )

    def generate(self, background_task_id: str = "") -> GenerationResult:
        start_time = time()
        try:
            if self.assistant and BedrockOrchestratorService.is_bedrock_assistant(self.assistant):
                # Use BedrockOrchestratorService to invoke the Bedrock assistant
                logger.info("Invoking bedrock assistant generation.")
                response = BedrockOrchestratorService.invoke_bedrock_assistant(
                    assistant=self.assistant,
                    input_text=self.request.text or "",
                    conversation_id=self.conversation_id,
                )
                output = response.get("output", "")
                logger.info(f"Bedrock response: {output}")
                token_used = None  # Bedrock doesn't provide token usage
            elif self.is_pure_chain():
                response = self.agent_executor.generate()
                output = response.generated
                token_used = calculate_tokens(output)
            else:
                response = self._invoke_agent(self._get_inputs())
                output = response.get('output', '')
                # For Bedrock LLMs for background task output is a list of dict. The actual output is stored in the text
                # key. So this fix takes the first dict item in the output list and returns value for 'text' key
                if isinstance(output, list) and len(output) > 0:
                    output = output[0].get('text', '')

                token_used = calculate_tokens(output)

            time_elapsed = time() - start_time
            if background_task_id:
                BackgroundTasksService().update(
                    task_id=background_task_id, status=BackgroundTaskStatus.COMPLETED, final_output=output
                )

            return GenerationResult(
                generated=output,
                time_elapsed=time_elapsed,
                input_tokens_used=None,
                tokens_used=token_used,
                success=True,
            )
        except Exception:
            stacktrace = traceback.format_exc()
            error_output = f"AI Agent run failed with error: {stacktrace}"
            logger.error(error_output, exc_info=True)
            time_elapsed = time() - start_time
            if background_task_id:
                BackgroundTasksService().update(
                    task_id=background_task_id, status=BackgroundTaskStatus.FAILED, final_output=error_output
                )
            return GenerationResult(
                generated=error_output,
                time_elapsed=time_elapsed,
                input_tokens_used=None,
                tokens_used=None,
                success=False,
            )

    def stream(self):
        set_logging_info(
            uuid=self.request_uuid,
            user_id=self.user.id,
            conversation_id=self.conversation_id,
            user_email=self.user.username,
        )

        if self.is_pure_chain() and not (
            self.assistant and BedrockOrchestratorService.is_bedrock_assistant(self.assistant)
        ):
            return self.agent_executor.stream()

        execution_start = time()
        chunks_collector = []

        try:
            logger.info(f"Starting {self.agent_name} agent for task: {self._task}")
            self._agent_streaming(chunks_collector)
            logger.info(f"Finish {self.agent_name} agent for task: {self._task}")
            time_elapsed = time() - execution_start

            self.thread_generator.send(
                StreamedGenerationResult(
                    generated="".join(chunks_collector),
                    generated_chunk="",
                    last=True,
                    time_elapsed=time_elapsed,
                    debug={},
                    context=self.thread_context,
                ).model_dump_json()
            )
        except Exception as e:
            user_message, llm_error_code = handle_agent_exception(e)
            chunks_collector.append(user_message)

            time_elapsed = time() - execution_start
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

    def _agent_streaming(self, chunks_collector: List[str]):
        if self.assistant and BedrockOrchestratorService.is_bedrock_assistant(self.assistant):
            logger.info(f"Streaming Bedrock assistant output for AssistantId={self.assistant.id}")

            try:
                response = BedrockOrchestratorService.invoke_bedrock_assistant(
                    assistant=self.assistant,
                    input_text=self.request.text or "",
                    conversation_id=self.conversation_id,
                )
                AIToolsAgent.process_output(response["output"], chunks_collector)
            except Exception as e:
                logger.error(f"Error during Bedrock assistant invocation: {str(e)}", exc_info=True)
            return

        stream = self.agent_executor.stream(self._get_inputs(), config=self._get_run_config())
        for chunk in stream:
            if self.thread_generator.is_closed():
                logger.info(f"Stopping agent {self.agent_name}, user is disconnected")
                break
            if not chunk:
                continue
            AIToolsAgent.process_chunk(chunk, chunks_collector)

    @staticmethod
    def process_chunk(chunk, chunks_collector: List[str]):
        if "actions" in chunk:
            for action in chunk["actions"]:
                logger.info(f"Calling Tool: {action.tool} with input {action.tool_input}")
        elif "steps" in chunk:
            for step in chunk["steps"]:
                logger.debug(f"Tool Result: {step.observation}")
        elif "output" in chunk:
            AIToolsAgent.process_output(chunk["output"], chunks_collector)
        else:
            logger.error(f"Got tool error: {chunk.get('output')}")

    @staticmethod
    def process_output(output, chunks_collector: List[str]):
        logger.debug(f"Final result is: {output}")
        message = extract_text_from_llm_output(output)
        chunks_collector.append(message)

    @staticmethod
    def _transform_history(history: List[ChatMessage]) -> list:
        """Convert history to list of chain-compatible messages"""
        transformed_history = []

        for item in history:
            if item.role == ChatRole.USER:
                transformed_history.append(HumanMessage(content=item.message))
            elif item.role == ChatRole.ASSISTANT:
                transformed_history.append(AIMessage(content=item.message))

        return transformed_history

    @classmethod
    def _filter_history(cls, history: list) -> list:
        return [item for item in history if item.content]

    def set_thread_context(self, context: dict, parent_thought_id: str):
        self.thread_context = context
        for callback in self.callbacks:
            if isinstance(callback, AgentStreamingCallback):
                callback.parent_id = parent_thought_id
                callback.context = context

    def get_prompt_template(self):
        llm_model_details = llm_service.get_model_details(self.llm_model)
        first_message = SystemMessagePromptTemplate.from_template(
            self._get_system_prompt(),
            template_format="jinja2",
        )
        if not llm_model_details.features.system_prompt:
            first_message = HumanMessagePromptTemplate.from_template(
                self._get_system_prompt(),
                template_format="jinja2",
            )
        try:
            messages = [
                first_message,
                MessagesPlaceholder("chat_history", optional=True),
                MessagesPlaceholder(variable_name="messages", optional=True),
                HumanMessagePromptTemplate.from_template("{{input}}", template_format="jinja2"),
                MessagesPlaceholder("agent_scratchpad"),
            ]
            return ChatPromptTemplate.from_messages(messages, template_format="jinja2")
        except jinja2.exceptions.TemplateSyntaxError:
            raise InvalidPromptTemplate

    @property
    def _task(self):
        if not self.request.file_names:
            return self.request.text

        file_names = [FileObject.from_encoded_url(name).name for name in self.request.file_names]
        return f"{self.request.text}{"\n Attached files: " + ", ".join(file_names)}"

    @property
    def _do_nothing(self):
        return lambda x: x


@deprecated
def _convert_mcp_response_to_tool_messages(agent_action, observation: MCPToolInvocationResponse) -> list[BaseMessage]:
    tool_text_messages = []
    tool_image_messages = []
    for item in observation.content:
        if item.is_image():
            image_model = item.model_dump()
            image_model = {k: v for k, v in image_model.items() if v is not None}
            tool_image_messages.append(image_model)
        else:
            tool_text_messages.append(str(item))
    result = []
    if tool_text_messages:
        result.append(
            ToolMessage(
                tool_call_id=agent_action.tool_call_id,
                content="\n".join(tool_text_messages),
                additional_kwargs={"name": agent_action.tool},
            )
        )
    if tool_image_messages:
        result.extend(HumanMessage([item]) for item in tool_image_messages)
    return result


@deprecated
def _create_tool_message_int(
    agent_action: ToolAgentAction, observation: MCPToolInvocationResponse | str
) -> list[BaseMessage]:
    """TODO: generate description"""

    if isinstance(observation, MCPToolInvocationResponse):
        return _convert_mcp_response_to_tool_messages(agent_action, observation)
    else:
        return [_create_tool_message(agent_action, observation)]


@deprecated
def _format_to_tool_messages(
    intermediate_steps: Sequence[Tuple[AgentAction, MCPToolInvocationResponse | str]],
) -> List[BaseMessage]:
    """Convert (AgentAction, tool output) tuples into ToolMessages.

    Args:
        intermediate_steps: Steps the LLM has taken to date, along with observations.

    Returns:
        list of messages to send to the LLM for the next prediction.

    """

    messages = []
    for agent_action, observation in intermediate_steps:
        if isinstance(agent_action, ToolAgentAction):
            new_messages = list(agent_action.message_log) + _create_tool_message_int(agent_action, observation)
            messages.extend([new for new in new_messages if new not in messages])
        else:
            messages.append(AIMessage(content=agent_action.log))
    return messages
