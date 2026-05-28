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

import uuid
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, ToolMessage

from codemie.agents.supervisor.constants import METADATA_KEY_HANDOFF_BACK, METADATA_KEY_HANDOFF_DESTINATION
from codemie.chains.base import ThoughtOutputFormat
from codemie.configs.logger import logger
from codemie.core.constants import OUTPUT_FORMAT
from codemie.core.utils import extract_text_from_llm_output, unpack_json_strings


class LangGraphCallbackBridge:
    def __init__(self, agent: Any, *, get_logger: Any) -> None:
        self.agent = agent
        self.get_logger = get_logger

    def _log_error(self, message: str) -> None:
        self.get_logger().error(message)

    def get_thoughts_from_callback(self) -> list[dict[str, Any]]:
        if self.agent.is_pure_chain():
            return []

        thoughts = []
        for callback in [*self.agent.supervisor_callbacks, *self.agent.callbacks]:
            callback_thoughts = getattr(callback, "thoughts", None)
            if callback_thoughts:
                thoughts.extend(callback_thoughts)
        return thoughts

    def on_llm_start(self) -> None:
        self.agent._current_llm_run_id = uuid.uuid4()
        for callback in self.agent.callbacks:
            try:
                callback.on_llm_start(None, None, run_id=self.agent._current_llm_run_id)
            except Exception as error:
                self._log_error(f"On LLM start callback {callback} error: {error}")

    def on_llm_new_token(self, token: str, run_id: str, author: str | None = None) -> None:
        for callback in self.agent.callbacks:
            try:
                callback.on_llm_new_token(token=token, run_id=run_id, author=author)
            except Exception as error:
                self._log_error(f"On LLM new token callback {callback} error: {error}")

    def on_llm_end(self, response: Any, run_id: str, author: str | None = None) -> None:
        for callback in self.agent.callbacks:
            try:
                callback.on_llm_end(response, run_id=run_id, author=author)
            except Exception as error:
                self._log_error(f"On llm end callback {callback} error: {error}")

    def on_llm_error(self, error: BaseException, run_id: str, author: str | None = None) -> None:
        for callback in self.agent.callbacks:
            try:
                callback.on_llm_error(error, run_id=run_id, author=author)
            except Exception as callback_error:
                self._log_error(f"On llm error callback {callback} error: {callback_error}")
        self.agent._current_llm_run_id = None

    def on_tool_start(
        self,
        tool_name: str,
        input_str: str,
        run_id: UUID | None = None,
        author: str | None = None,
    ) -> None:
        serialized = {"name": tool_name}
        callback_run_id = run_id or uuid.uuid4()
        for callback in self.agent.callbacks:
            try:
                callback.on_tool_start(serialized, input_str, run_id=callback_run_id, author=author)
            except Exception as error:
                self._log_error(f"On tool start callback {callback} error: {error}")

    def on_tool_end(self, output: Any, run_id: UUID, author: str | None = None) -> None:
        for callback in self.agent.callbacks:
            try:
                callback.on_tool_end(output, run_id=run_id, author=author)
            except Exception as error:
                self._log_error(f"On tool end callback {callback} error: {error}")

    def on_supervisor_handoff(
        self,
        destination: str,
        run_id: UUID,
        input_str: str = "",
        author: str | None = None,
        display_name: str | None = None,
    ) -> None:
        serialized = {"name": display_name or destination}
        metadata = {OUTPUT_FORMAT: ThoughtOutputFormat.MARKDOWN.value}
        for callback in self.agent.supervisor_callbacks:
            try:
                callback.on_tool_start(serialized, input_str, run_id=run_id, metadata=metadata, author=author)
            except Exception as error:
                self._log_error(f"On supervisor hanoff callback {callback} error: {error}")

    def on_subassistant_back(self, output: Any, run_id: UUID | None = None, author: str | None = None) -> None:
        for callback in self.agent.supervisor_callbacks:
            try:
                callback.on_tool_end(output, run_id=run_id, author=author)
            except Exception as error:
                self._log_error(f"On supervisor back {callback} error: {error}")

    def on_tool_error(self, output: Any, run_id: UUID | None = None, author: str | None = None) -> None:
        callback_run_id = run_id or uuid.uuid4()
        for callback in self.agent.callbacks:
            try:
                callback.on_tool_error(output, run_id=callback_run_id, author=author)
            except Exception as error:
                self._log_error(f"On tool error callback {callback} error: {error}")

    def on_chain_end(self, output: Any) -> None:
        for callback in self.agent.callbacks:
            try:
                callback.on_chain_end(output, run_id=None)
            except Exception as error:
                self._log_error(f"On chain end callback {callback} error: {error}")


class LangGraphEventAdapter:
    def __init__(self, agent: Any) -> None:
        self.agent = agent

    def parse_message_type(self, value: Any, chunks_collector: list[str]) -> None:
        message, metadata = value

        if self.agent.is_valid_ai_message(message):
            token = extract_text_from_llm_output(message.content)
            self.agent._process_agent_streaming(token, chunks_collector, message.id)
        elif self.agent.is_finish_reason_stop(message):
            if metadata.get("langgraph_node") == "agent":
                self.agent._on_llm_end(response=message, run_id=message.id)

    def parse_update_type(self, value: dict[str, Any]) -> None:
        if "agent" in value and self.agent.is_finish_reason_tool_calls(value["agent"]["messages"][-1]):
            message = value["agent"]["messages"][-1]
            self.agent._safe_check_for_truncation(message)
            if content := extract_text_from_llm_output(message.content):
                self.agent._on_llm_end(content, run_id=message.id)
            for tool_call in message.tool_calls:
                tool_name = tool_call["name"]
                tool_args = str(unpack_json_strings(tool_call["args"]))
                run_id = self.agent._tool_call_id_to_uuid(tool_call.get("id", ""))
                logger.debug(f"Calling Tool: {tool_name} with input {tool_args}")
                self.agent._on_tool_start(tool_name, tool_args, run_id=run_id)
        elif "tools" in value:
            for action in value["tools"]["messages"]:
                if isinstance(action, ToolMessage):
                    logger.debug(f"Tool {action.name} call result: {action.content}")
                    self.agent._parse_tool_message(action)

    def parse_supervisor_message_type(
        self,
        value: Any,
        chunks_collector: list[str],
        author: str | None = None,
    ) -> None:
        message, _ = value
        if self.agent.is_valid_ai_message(message) and not message.response_metadata.get(METADATA_KEY_HANDOFF_BACK):
            token = extract_text_from_llm_output(message.content)
            self.agent._process_agent_streaming(token, chunks_collector, message.id, author=author)
        elif self.agent.is_finish_reason_stop(message):
            self.agent._on_llm_end(response=message, run_id=message.id, author=author)

    def parse_supervisor_update_type(self, value: dict[str, Any], author: str | None = None) -> None:
        state_update = next((item for item in value.values() if isinstance(item, dict)), None)
        if state_update is None:
            return

        messages = state_update.get("messages", [])
        if not messages:
            return
        last_message = messages[-1]

        if isinstance(last_message, AIMessage) and self.agent.is_finish_reason_tool_calls(last_message):
            self.handle_supervisor_tool_calls(last_message, author=author)
        elif last_message.response_metadata.get(METADATA_KEY_HANDOFF_BACK):
            node_name = list(value.keys())[0]
            lookup_author = author or (node_name if node_name != "supervisor" else None)
            self.agent._LangGraphAgent__handle_supervisor_handoff_back(
                messages,
                run_id=self.agent._supervisor_coordinator.binding_for(lookup_author),
                author=lookup_author,
            )
        elif author and self.agent._supervisor_coordinator.binding_for(author) and isinstance(last_message, AIMessage):
            self.agent._LangGraphAgent__handle_supervisor_subassistant_result(
                messages,
                run_id=self.agent._supervisor_coordinator.binding_for(author),
                author=author,
            )
        elif destination := last_message.response_metadata.get(METADATA_KEY_HANDOFF_DESTINATION):
            logger.debug(f"Transferring to {destination}")
        elif isinstance(last_message, ToolMessage):
            logger.debug(f"Tool {last_message.name} call result: {last_message.content}")
            self.agent._parse_tool_message(last_message, author=author)

    def handle_supervisor_tool_calls(self, last_message: AIMessage, author: str | None = None) -> None:
        self.agent._safe_check_for_truncation(last_message)
        if content := extract_text_from_llm_output(last_message.content):
            self.agent._on_llm_end(content, run_id=last_message.id, author=author)

        handoff_calls = [tc for tc in last_message.tool_calls if self.agent._check_is_handoff_tool(tc["name"])]
        regular_calls = [tc for tc in last_message.tool_calls if not self.agent._check_is_handoff_tool(tc["name"])]
        if handoff_calls:
            self.agent._queue_supervisor_handoffs(handoff_calls, author=author)
        if regular_calls:
            for tool_call in regular_calls:
                tool_name = tool_call["name"]
                tool_args = str(unpack_json_strings(tool_call["args"]))
                run_id = self.agent._tool_call_id_to_uuid(tool_call.get("id", ""))
                logger.debug(f"Calling Tool: {tool_name} with input {tool_args}")
                self.agent._on_tool_start(tool_name, tool_args, run_id=run_id, author=author)

    def process_chunk_for_agent(self, chunk: Any, chunks_collector: list[str]) -> None:
        chunk_type, value = chunk
        if chunk_type == "messages":
            self.parse_message_type(value, chunks_collector)
        elif chunk_type == "updates":
            self.parse_update_type(value)

    def dispatch_supervisor_chunk(self, context: Any, chunks_collector: list[str]) -> None:
        if context.chunk_type == "messages":
            self.parse_supervisor_message_type(context.value, chunks_collector, author=context.author)
        elif context.chunk_type == "updates":
            self.parse_supervisor_update_type(context.value, author=context.author)

    def process_chunk_for_supervisor(self, chunk: Any, chunks_collector: list[str]) -> None:
        context = self.agent._supervisor_chunk_context_type.from_chunk(chunk)
        if context.author:
            pending_handoffs = self.agent._pending_handoffs.get(context.raw_author)
            if pending_handoffs and self.agent._supervisor_coordinator.binding_for(context.author) is None:
                if context.delegated_task or len(pending_handoffs) == 1:
                    self.agent._promote_pending_handoff(context, chunk)
                    self.dispatch_supervisor_chunk(context, chunks_collector)
                    self.agent._flush_buffered_supervisor_contexts(context.author, chunks_collector)
                    return

                self.agent._buffer_supervisor_context(context)
                return

            if context.delegated_task:
                self.agent._rebind_author_to_active_handoff(
                    author=context.author,
                    raw_author=context.raw_author,
                    task=context.delegated_task,
                )

        self.dispatch_supervisor_chunk(context, chunks_collector)

    def process_chunk(self, chunk: Any, chunks_collector: list[str]) -> None:
        if self.agent.subagents:
            self.process_chunk_for_supervisor(chunk, chunks_collector)
        else:
            self.process_chunk_for_agent(chunk, chunks_collector)

    def parse_tool_message(self, action: ToolMessage, author: str | None = None) -> None:
        run_id = self.agent._tool_call_id_to_uuid(action.tool_call_id or "")
        if action.status == "error":
            self.agent._on_tool_error(action.content, run_id=run_id, author=author)
            return

        if action.status != "success":
            message = f"Unknown tool action status: {action.status}"
            message += f"\nAssistant: {self.agent.agent_name}, request_uuid: {self.agent.request_uuid}"
            message += "\nExpected 'success' or 'error'"
            logger.warning(message)
        self.agent._on_tool_end(action.content, run_id=run_id, author=author)

    def process_agent_streaming(
        self,
        token: str,
        chunks_collector: list[str],
        run_id: str,
        author: str | None = None,
    ) -> None:
        self.agent._on_llm_new_token(token, run_id, author)
        self.agent.__class__.process_output(token, chunks_collector)
