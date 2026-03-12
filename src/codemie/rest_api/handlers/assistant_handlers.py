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
import json
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from time import time
from types import SimpleNamespace
from typing import List
from pydantic import BaseModel

from fastapi import BackgroundTasks, Request, status
from starlette.responses import StreamingResponse

from codemie.chains.base import Thought, StreamedGenerationResult
from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.dependecies import set_disable_prompt_cache
from codemie.core.errors import ErrorDetailLevel
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BaseModelResponse, AssistantChatRequest, BackgroundTaskRequest, AssistantDetails
from codemie.core.thread import ThreadedGenerator
from codemie.rest_api.a2a.client.remote_agent_connection import RemoteAgentConnections, TaskCallbackArg
from codemie.rest_api.a2a.types import Task, SendTaskRequest, SendTaskStreamingRequest, AgentCard, TaskState
from codemie.rest_api.a2a.utils import convert_to_task_request, convert_to_base_model_response
from codemie.rest_api.models.assistant import Assistant, AssistantType
from codemie.rest_api.models.base import ConversationStatus
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.routers.utils import run_in_thread_pool
from codemie.rest_api.security.user import User
from codemie.rest_api.utils.request_utils import extract_custom_headers
from codemie.service.assistant_service import AssistantService
from codemie.service.background_tasks_service import BackgroundTasksService
from codemie.service.conversation_service import ConversationService
from codemie.service.request_summary_manager import request_summary_manager


# Constants
NDJSON_MEDIA_TYPE = "application/x-ndjson"


@dataclass
class ChatHistoryData:
    """Data class for chat history parameters"""

    execution_start: float
    request: AssistantChatRequest
    response: str
    thoughts: List[Thought]
    status: ConversationStatus = ConversationStatus.SUCCESS


class AssistantRequestHandler(ABC):
    def __init__(self, assistant: Assistant, user: User, request_uuid: str):
        self.assistant = assistant
        self.user = user
        self.request_uuid = request_uuid

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
        # Skip if history is already provided or conversation_id is missing
        if request.history or not request.conversation_id:
            logger.debug(
                f"History is already provided or conversation_id is missing. "
                f"{len(request.history)} history messages, "
                f"conversation_id: {request.conversation_id or 'None'}, "
            )
            return

        # Retrieve existing conversation using the same pattern as the router
        conversation = Conversation.find_by_id(request.conversation_id)

        if not conversation:
            logger.debug(f"Conversation {request.conversation_id} not found for user {self.user.id}")
            return

        # Verify user has read access to this conversation using Ability pattern
        if not Ability(self.user).can(Action.READ, conversation):
            logger.warning(
                f"User {self.user.id} denied access to conversation {request.conversation_id} "
                f"owned by {conversation.user_id}"
            )
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Access denied",
                details=f"You don't have permission to access conversation {request.conversation_id}.",
                help="Please ensure you have the correct permissions or contact the conversation owner.",
            )

        try:
            # Update request with conversation history
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

    def save_chat_history(self, data: ChatHistoryData) -> None:
        """Public method to save chat history"""
        if not data.request.save_history:
            logger.debug(
                f"Skipping chat history persistence for conversation_id={data.request.conversation_id} "
                f"(save_history=False)"
            )
            request_summary_manager.clear_summary(self.request_uuid)
            return

        tokens_usage = request_summary_manager.get_summary(self.request_uuid).tokens_usage
        ConversationService.upsert_chat_history(
            request=data.request,
            user=self.user,
            assistant_response=data.response,
            time_elapsed=time() - data.execution_start,
            tokens_usage=tokens_usage,
            assistant=self.assistant,
            thoughts=self._filter_thoughts(data.thoughts),
            status=data.status,
        )
        request_summary_manager.clear_summary(self.request_uuid)

    @staticmethod
    def _filter_thoughts(thoughts: List[Thought]):
        return [
            Thought(
                id=thought.get('id'),
                message=thought.get('message'),
                author_name=thought.get('author_name'),
                author_type=thought.get('author_type'),
                children=thought.get('children') if thought.get('children') else [],
                input_text=thought.get('input_text', ''),
                error=thought.get('error', False),
            )
            for thought in thoughts
            if thought.get('message', '')
        ]


class StandardAssistantHandler(AssistantRequestHandler):
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
        # Populate conversation history if conversation_id is provided
        self._populate_conversation_history(request)

        execution_start = time()
        if request.stream:
            return self._handle_stream(request, raw_request, execution_start, include_tool_errors, error_detail_level)
        elif request.background_task:
            return self._handle_background(
                request,
                background_tasks,
                raw_request,
                execution_start,
                include_tool_errors,
                error_detail_level,
            )
        else:
            return self._handle_sync(request, raw_request, execution_start, include_tool_errors, error_detail_level)

    def _handle_stream(
        self,
        request: AssistantChatRequest,
        raw_request: Request,
        execution_start: float,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> StreamingResponse:
        """
        Handle streaming assistant request.

        Supports error handling via include_tool_errors and error_detail_level parameters.
        Errors are included in the final streamed chunk when last=True.
        """
        generator_queue = ThreadedGenerator(
            request_uuid=self.request_uuid, user_id=self.user.id, conversation_id=request.conversation_id
        )

        raw_request.state.on_disconnect(
            lambda: self._handle_client_disconnect(
                request=request,
                threaded_generator=generator_queue,
                execution_start=execution_start,
            )
        )

        try:
            # Extract headers if propagation is enabled
            request_headers = extract_custom_headers(raw_request, request.propagate_headers)

            # Set cache control flag for this request
            set_disable_prompt_cache(request.disable_cache or False)

            agent = AssistantService.build_agent(
                assistant=self.assistant,
                request=request,
                user=self.user,
                request_uuid=raw_request.state.uuid,
                thread_generator=generator_queue,
                request_headers=request_headers,
            )
            stream = getattr(agent, "stream")
            wrapped_stream = self._serve_data(
                stream,
                generator_queue,
                request,
                execution_start,
                agent,
                include_tool_errors,
                error_detail_level,
            )

            return StreamingResponse(
                content=wrapped_stream,
                media_type=NDJSON_MEDIA_TYPE,
            )
        except Exception as e:
            # Import at function level to avoid circular imports
            from codemie.core.template_security import TemplateSecurityError

            if isinstance(e, TemplateSecurityError):
                # Return security error as a thought without calling LLM
                return self._return_security_error_response(str(e), generator_queue, request, execution_start)
            else:
                # Re-raise other exceptions
                raise

    def _handle_client_disconnect(
        self,
        request: AssistantChatRequest,
        threaded_generator: ThreadedGenerator,
        execution_start,
    ):
        """
        Stop thread generator queue on client disconnect
        """
        if not threaded_generator.is_closed():
            self._save_history_for_disconnect(
                request=request,
                execution_start=execution_start,
                threaded_generator=threaded_generator,
            )
            logger.debug("Client disconnected")
            threaded_generator.close()

    def _save_history_for_disconnect(
        self,
        request: AssistantChatRequest,
        threaded_generator: ThreadedGenerator,
        execution_start,
    ):
        try:
            thoughts = threaded_generator.thoughts

            # Check if there's already a security error thought - don't overwrite it
            has_security_error = any(
                t.get('author_name') == 'Security Validator' and t.get('error') is True for t in thoughts
            )

            if has_security_error:
                # Security error already saved, don't overwrite
                logger.debug("Security error already saved, skipping disconnect handler save")
                return

            response = "Agent has been interrupted by client"
            self.save_chat_history(
                ChatHistoryData(
                    execution_start=execution_start,
                    request=request,
                    response=response,
                    thoughts=thoughts,
                    status=ConversationStatus.INTERRUPTED,
                )
            )
        except Exception as e:
            logger.error(f"Error while saving history for disconnected client: {str(e)}")

    def _serve_data(
        self,
        stream,
        generator_queue: ThreadedGenerator,
        request,
        execution_start,
        agent=None,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ):
        thread = threading.Thread(target=stream)
        thread.start()
        # We pass an empty string to avoid sending the default None value in the chat history.
        response = StreamedGenerationResult(generated="")
        while True:
            value = generator_queue.queue.get()
            if value is not StopIteration:
                generation_result = json.loads(value, object_hook=lambda d: SimpleNamespace(**d))
                if generation_result.generated is not None:
                    response = generation_result

            else:
                self.save_chat_history(
                    ChatHistoryData(
                        execution_start=execution_start,
                        request=request,
                        response=response.generated,
                        thoughts=generator_queue.thoughts,
                    )
                )
                # Yield final chunk with error information if available
                if agent is not None and include_tool_errors:
                    agent_result = getattr(agent, "last_generation_result", None)
                    if agent_result:
                        tool_errors, agent_error = self._format_errors(
                            agent_result, include_tool_errors, error_detail_level
                        )
                        final_chunk = StreamedGenerationResult(
                            last=True,
                            success=agent_result.success,
                            agent_error=agent_error,
                            tool_errors=tool_errors,
                            time_elapsed=time() - execution_start,
                        )
                        yield final_chunk.model_dump_json() + "\n"
                break

            yield value
            generator_queue.queue.task_done()

    def _return_security_error_response(
        self,
        error_message: str,
        generator_queue: ThreadedGenerator,
        request: AssistantChatRequest,
        execution_start: float,
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
        background_tasks.add_task(
            run_in_thread_pool,
            self._background_generate,
            request,
            background_task_id,
            raw_request,
            execution_start,
            include_tool_errors,
            error_detail_level,
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
    ):
        """
        Background worker for assistant generation.

        Supports error handling when include_tool_errors is True.
        Errors are logged and can be retrieved via background task status.
        """
        request_uuid = raw_request.state.uuid

        # Extract headers if propagation is enabled
        request_headers = extract_custom_headers(raw_request, request.propagate_headers)

        # Set cache control flag for this request
        set_disable_prompt_cache(request.disable_cache or False)

        agent = AssistantService.build_agent(
            assistant=self.assistant,
            request=request,
            user=self.user,
            request_uuid=request_uuid,
            request_headers=request_headers,
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

        thoughts = agent.get_thoughts_from_callback()
        self.save_chat_history(
            ChatHistoryData(execution_start=execution_start, request=request, response=response, thoughts=thoughts)
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
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> BaseModelResponse:
        """
        Handle synchronous (non-streaming) assistant request.
        """
        # Extract headers if propagation is enabled
        request_headers = extract_custom_headers(raw_request, request.propagate_headers)

        # Set cache control flag for this request
        set_disable_prompt_cache(request.disable_cache or False)

        agent = AssistantService.build_agent(
            assistant=self.assistant,
            request=request,
            user=self.user,
            request_uuid=raw_request.state.uuid,
            request_headers=request_headers,
        )
        generation_result = agent.generate()
        response = generation_result.generated
        string_response = self._cast_llm_response_to_string(response)
        # Output of structured AgentExecutor with tools is string, so we parse it to dict
        if request.output_schema and isinstance(response, str):
            response = json.loads(response)
        time_elapsed = time() - execution_start
        thoughts = agent.get_thoughts_from_callback()
        self.save_chat_history(
            ChatHistoryData(
                execution_start=execution_start,
                request=request,
                response=string_response,
                thoughts=thoughts,
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


class A2AAssistantHandler(AssistantRequestHandler):
    def __init__(self, assistant: Assistant, user: User, request_uuid: str):
        super().__init__(assistant, user, request_uuid)
        self.agent_card = assistant.agent_card
        self.remote_connection = RemoteAgentConnections(
            agent_card=self.agent_card,
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
        # Populate conversation history if conversation_id is provided
        self._populate_conversation_history(request)

        task_request = convert_to_task_request(request, raw_request)
        logger.debug(f"Call agent. Agent: {self.agent_card.name}. Url: {self.agent_card.url}. Request: {task_request}")
        if self.agent_card.capabilities.streaming:
            return self._handle_a2a_stream(request, task_request)
        else:
            return self._handle_a2a_sync(request, task_request)

    def _handle_a2a_stream(
        self, request: AssistantChatRequest, task_request: SendTaskStreamingRequest
    ) -> StreamingResponse:
        execution_start = time()

        # Create a ThreadedGenerator for handling the streaming responses
        generator_queue = ThreadedGenerator(
            request_uuid=self.request_uuid, user_id=self.user.id, conversation_id=request.conversation_id
        )

        # Define a callback that will format and send responses to the generator_queue
        def task_callback(task_arg: TaskCallbackArg, agent_card: AgentCard):
            # Format the response based on the type of update
            response_data = None
            if hasattr(task_arg, 'status') and task_arg.status and task_arg.status.message:
                text_parts = self._extract_text_parts_from_message(task_arg)
                if text_parts:
                    thought_message = ' '.join(text_parts)
                    input_text = ''
                    if task_arg.status.state == TaskState.SUBMITTED:
                        input_text = thought_message
                        thought_message = ''
                    response_data = StreamedGenerationResult(
                        thought=Thought(
                            id=str(uuid.uuid4()),
                            author_name=self.agent_card.name,
                            author_type='Agent',
                            in_progress=True,
                            message=thought_message,
                            input_text=input_text,
                        )
                    )
            # Handle artifact updates
            elif hasattr(task_arg, 'artifact'):
                text_parts = self._extract_text_parts_from_artifacts(task_arg)
                if text_parts:
                    response_data = StreamedGenerationResult(
                        generated=' '.join(text_parts),
                        time_elapsed=time() - execution_start,
                        generated_chunk="",
                        last=True,
                    )
            logger.debug(
                f"Streaming response from agent. Agent: {agent_card.name}. "
                f"Response: {response_data}. "
                f"TaskArg: {task_arg}"
            )
            # Send the formatted response to the generator queue
            if response_data:
                generator_queue.send(response_data.model_dump_json())
            if hasattr(task_arg, "final") and task_arg.final:
                if response_data and not response_data.last and response_data.thought:
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

        # Create an async generator that will yield responses from the queue
        async def stream_generator():
            await self.remote_connection.send_task(task_request.params, task_callback)

            response = StreamedGenerationResult()
            while True:
                value = generator_queue.queue.get()
                if value is not StopIteration:
                    generation_result = json.loads(value, object_hook=lambda d: SimpleNamespace(**d))
                    if generation_result.generated is not None:
                        response = generation_result
                else:
                    self.save_chat_history(
                        ChatHistoryData(
                            execution_start=execution_start,
                            request=request,
                            response=response.generated,
                            thoughts=generator_queue.thoughts,
                        )
                    )
                    break
                yield value
                generator_queue.queue.task_done()

        return StreamingResponse(content=stream_generator(), media_type=NDJSON_MEDIA_TYPE)

    def _extract_text_parts_from_message(self, task_arg: TaskCallbackArg) -> list[str]:
        if hasattr(task_arg, 'status') and task_arg.status and task_arg.status.message:
            message_parts = task_arg.status.message.parts
            if message_parts:
                return [part.text for part in message_parts if hasattr(part, 'text')]
        return []

    def _extract_text_parts_from_artifacts(self, task_arg):
        if hasattr(task_arg, 'artifact') and task_arg.artifact and task_arg.artifact.parts:
            return [part.text for part in task_arg.artifact.parts if hasattr(part, 'text')]
        return []

    def _handle_a2a_sync(self, request: AssistantChatRequest, task_request: SendTaskRequest) -> BaseModelResponse:
        execution_start = time()
        task_response: Task = asyncio.run(self.remote_connection.send_task(task_request.params, None))

        model_response = convert_to_base_model_response(task_response)
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
    return StandardAssistantHandler(assistant, user, request_uuid)
