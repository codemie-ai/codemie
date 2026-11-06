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
import uuid
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, ToolMessage

from codemie.agents.supervisor.constants import METADATA_KEY_HANDOFF_BACK, METADATA_KEY_HANDOFF_DESTINATION
from codemie.agents.tool_confirmation.stream_steps import ConfirmationStepRouter
from codemie.chains.base import ThoughtOutputFormat
from codemie.configs.logger import logger
from codemie.core.constants import (
    OUTPUT_FORMAT,
    TOOL_TYPE,
)
from codemie.core.utils import extract_text_from_llm_output, unpack_json_strings
from codemie.service.monitoring.tool_run_context import IDENTITY_METADATA_KEYS

# What the monitoring callback attributes a tool call by. Deliberately not the whole tool metadata: its
# ``output_format`` would change how the thought of the call is rendered.
_ATTRIBUTION_KEYS = (*IDENTITY_METADATA_KEYS, TOOL_TYPE)


def get_truncation_indicator(response_metadata: dict[str, Any] | None) -> str | None:
    """Return a truncation indicator when the response was cut at the output-token limit."""
    if not response_metadata:
        return None
    # LiteLLM proxy / OpenAI format
    finish_reason = response_metadata.get("finish_reason")
    if finish_reason in ("length", "max_tokens"):
        return f"finish_reason={finish_reason}"
    # Native Claude format (Bedrock uses 'stopReason' camelCase)
    stop_reason = response_metadata.get("stop_reason", response_metadata.get("stopReason"))
    if stop_reason == "max_tokens":
        return f"stop_reason={stop_reason}"
    return None


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

    def on_thought_failed(self, run_id: str, author: str | None = None) -> None:
        for callback in self.agent.callbacks:
            handler = getattr(callback, "on_thought_failed", None)
            if handler is None:
                continue
            try:
                handler(run_id=run_id, author=author)
            except Exception as error:
                self._log_error(f"On thought failed callback {callback} error: {error}")

    def on_llm_error(self, error: BaseException, run_id: str, author: str | None = None) -> None:
        for callback in self.agent.callbacks:
            try:
                callback.on_llm_error(error, run_id=run_id, author=author)
            except Exception as callback_error:
                self._log_error(f"On llm error callback {callback} error: {callback_error}")
        self.agent._current_llm_run_id = None

    def attribution_metadata(self, tool_name: str) -> dict[str, Any]:
        """The run attribution the agent stamped on the tool (see ``_configure_tools``); empty for a tool the agent
        does not own. The stream hands the callbacks only the tool name, unlike a LangChain-driven tool run."""
        for tool in getattr(self.agent, "tools", None) or []:
            if getattr(tool, "name", None) == tool_name:
                metadata = getattr(tool, "metadata", None) or {}
                return {key: metadata[key] for key in _ATTRIBUTION_KEYS if key in metadata}
        return {}

    def on_tool_start(
        self,
        tool_name: str,
        input_str: str,
        run_id: UUID | None = None,
        author: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        serialized = {"name": tool_name}
        callback_run_id = run_id or uuid.uuid4()
        if metadata is None:
            metadata = self.attribution_metadata(tool_name)
        for callback in self.agent.callbacks:
            try:
                callback.on_tool_start(serialized, input_str, run_id=callback_run_id, author=author, metadata=metadata)
            except Exception as error:
                self._log_error(f"On tool start callback {callback} error: {error}")

    def on_tool_end(self, output: Any, run_id: UUID, author: str | None = None, artifact=None) -> None:
        for callback in self.agent.callbacks:
            try:
                callback.on_tool_end(output, run_id=run_id, author=author, artifact=artifact)
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
        serialized = {"name": display_name or destination, "tool_name": destination}
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
    # Cap the per-response header cache so an interrupted stream cannot grow it without bound.
    _MAX_CACHED_RESPONSE_HEADERS = 64

    def __init__(self, agent: Any) -> None:
        self.agent = agent
        # Response headers per streamed message id. Only the first chunk of a response carries
        # them, so they are stashed here and re-attached to the chunk that finalizes the call.
        self._response_headers: dict[str, dict[str, Any]] = {}
        # The call-identity routing of the tool and handoff steps (EPMCDME-16463).
        self._steps = ConfirmationStepRouter(agent, self._start_supervisor_steps, self._start_tool_steps)

    def _remember_response_headers(self, message: Any) -> None:
        """Stash HTTP response headers seen on a streamed chunk (first chunk only carries them)."""
        message_id = getattr(message, "id", None)
        rm = getattr(message, "response_metadata", None)
        if not message_id or not isinstance(rm, dict):
            return
        headers = rm.get("headers")
        if isinstance(headers, dict) and headers and message_id not in self._response_headers:
            if len(self._response_headers) >= self._MAX_CACHED_RESPONSE_HEADERS:
                self._response_headers.clear()
            self._response_headers[message_id] = headers

    def _restore_response_headers(self, message: Any) -> None:
        """Re-attach the stashed headers so LLM-end callbacks can read router/classifier signals."""
        headers = self._response_headers.pop(getattr(message, "id", None), None)
        rm = getattr(message, "response_metadata", None)
        if headers and isinstance(rm, dict) and not rm.get("headers"):
            rm["headers"] = headers

    def parse_message_type(self, value: Any, chunks_collector: list[str]) -> None:
        message, metadata = value
        self._remember_response_headers(message)

        if self.agent.is_valid_ai_message(message):
            token = extract_text_from_llm_output(message.content)
            self.agent._process_agent_streaming(token, chunks_collector, message.id)

        # Use a separate `if` (not `elif`) so that a chunk carrying both content
        # and a stop reason also triggers LLM-end finalization.
        if (
            self.agent.is_finish_reason_stop(message) or self.agent.is_finish_reason_truncated(message)
        ) and metadata.get("langgraph_node") == "agent":
            self.agent._detect_terminal_truncation(message)
            self._restore_response_headers(message)
            self.agent._on_llm_end(response=message, run_id=message.id)

    def _finalize_truncated_text(self, message: Any, author: str | None = None) -> bool:
        """Finalize a text-only response cut at the output-token limit and log it; tool calls keep raising."""
        if not isinstance(message, AIMessage) or self.agent.is_finish_reason_tool_calls(message):
            return False
        indicator = get_truncation_indicator(message.response_metadata)
        if not indicator:
            return False
        if message.invalid_tool_calls and message.id:
            # The check below fails this run, and _on_llm_end drops the open thought from storage, so
            # flag it first.
            self.agent._callback_bridge.on_thought_failed(message.id, author=author)
        self._restore_response_headers(message)
        self.agent._on_llm_end(response=message, run_id=message.id, author=author)
        if message.invalid_tool_calls:
            # Arguments cut mid-JSON land in invalid_tool_calls; fail like a truncated tool call
            self.agent._safe_check_for_truncation(message, author=author)
        usage = message.usage_metadata or {}
        logger.warning(
            f"truncated_response model={self.agent.llm_model} indicator={indicator} "
            f"output_tokens={usage.get('output_tokens', 'unknown')} action=kept"
        )
        return True

    def parse_update_type(self, value: dict[str, Any]) -> None:
        if self._steps.consume_identity_chunk(value, namespace_key=""):
            return
        if "agent" in value:
            self._parse_agent_update(value["agent"]["messages"][-1])
        elif "tools" in value:
            for action in value["tools"]["messages"]:
                if isinstance(action, ToolMessage):
                    self.agent._replay_tool_start_on_resume(action)
                    logger.debug(f"Tool {action.name} call result: {action.content}")
                    self.agent._parse_tool_message(action)

    def _parse_agent_update(self, last_message: Any) -> None:
        if self.agent.is_finish_reason_tool_calls(last_message):
            self.agent._safe_check_for_truncation(last_message)
            # Always restore headers and call _on_llm_end for tool-calling turns so the
            # callback's routing tracker is updated before _on_tool_start fires. When the LLM
            # makes a direct tool call with no text content the streaming callback still
            # needs routing metadata to stamp routed_model onto the tool thought.
            self._restore_response_headers(last_message)
            self.agent._on_llm_end(response=last_message, run_id=last_message.id)
            self._steps.defer((), last_message, None, "", supervisor=False)
        elif self.agent.is_finish_reason_stop(last_message):
            # Simple chat completion (no tools): finalize the LLM thought.
            # The "messages" stream may not emit a dedicated stop chunk, so
            # the "updates" stream is the reliable trigger for this path.
            self.agent._on_llm_end(response=last_message, run_id=last_message.id)
        else:
            # Text-only response cut at max_tokens: no stop reason, so finalize it here.
            self._finalize_truncated_text(last_message)

    def parse_supervisor_message_type(
        self,
        value: Any,
        chunks_collector: list[str],
        author: str | None = None,
    ) -> None:
        message, metadata = value
        self._remember_response_headers(message)
        if self.agent.is_valid_ai_message(message) and not message.response_metadata.get(METADATA_KEY_HANDOFF_BACK):
            token = extract_text_from_llm_output(message.content)
            self.agent._process_agent_streaming(token, chunks_collector, message.id, author=author)

        # Separate `if` so a chunk with both content and stop reason still finalizes.
        if self.agent.is_finish_reason_stop(message) or self.agent.is_finish_reason_truncated(message):
            # Only the agent node's answer sets the flag; a later structured-output stop must not reset it.
            if metadata.get("langgraph_node") == "agent":
                self.agent._detect_terminal_truncation(message)
            self._restore_response_headers(message)
            self.agent._on_llm_end(response=message, run_id=message.id, author=author)

    def _drop_echoed_subagent_updates(self, value: dict[str, Any], author: str | None) -> dict[str, Any] | None:
        """Drop the top-level (no-namespace) echo of a subagent subgraph's own completed step.

        LangGraph re-emits the branch's full state diff at the parent level once the
        Send-dispatched branch finishes. Its messages were already dispatched under the
        branch-specific author; reprocessing them here would double-count tool results
        under the shared author=None key.

        Drop only the echoed subagent keys: unrelated top-level keys can ride in the
        same update tick and must still be processed. See R3-CR-006.

        Returns ``None`` when nothing remains to process.
        """
        if author is not None or not any(key in self.agent.subagent_descriptions for key in value):
            return value
        filtered = {key: item for key, item in value.items() if key not in self.agent.subagent_descriptions}
        return filtered or None

    def parse_supervisor_update_type(
        self, value: dict[str, Any], author: str | None = None, namespace: tuple[str, ...] = ()
    ) -> None:
        if self._steps.consume_identity_chunk(value, namespace_key=self._steps.namespace_key(namespace)):
            return
        value = self._drop_echoed_subagent_updates(value, author)
        if value is None:
            return

        state_update = next((item for item in value.values() if isinstance(item, dict)), None)
        if state_update is None:
            return

        messages = state_update.get("messages", [])
        if not messages:
            return
        last_message = messages[-1]

        # Finalize and log a truncated text-only answer; the branches below still route it.
        self._finalize_truncated_text(last_message, author=author)

        if isinstance(last_message, AIMessage) and self.agent.is_finish_reason_tool_calls(last_message):
            self._dispatch_supervisor_tool_calls(last_message, author=author, namespace=namespace)
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
            self._parse_supervisor_tool_result(last_message, author=author, namespace=namespace)

    def _dispatch_supervisor_tool_calls(
        self, last_message: AIMessage, author: str | None, namespace: tuple[str, ...]
    ) -> None:
        if namespace:
            self.handle_supervisor_tool_calls(last_message, author=author, namespace=namespace)
        else:
            self.handle_supervisor_tool_calls(last_message, author=author)

    def _parse_supervisor_tool_result(
        self, last_message: ToolMessage, author: str | None, namespace: tuple[str, ...]
    ) -> None:
        logger.debug(f"Tool {last_message.name} call result: {last_message.content}")
        location_key = self._steps.location_key(namespace)
        pending_events = getattr(self.agent, "tool_pending_events", None)
        if isinstance(pending_events, dict) and pending_events:
            self.agent._replay_tool_start_on_resume(last_message, author=author, location_key=location_key)
        if location_key:
            self.agent._parse_tool_message(last_message, author=author, location_key=location_key)
        else:
            self.agent._parse_tool_message(last_message, author=author)

    def handle_supervisor_tool_calls(
        self, last_message: AIMessage, author: str | None = None, namespace: tuple[str, ...] = ()
    ) -> None:
        self.agent._safe_check_for_truncation(last_message, author=author)
        if extract_text_from_llm_output(last_message.content):
            # Same as the agent path: restore the headers so routing signals survive.
            self._restore_response_headers(last_message)
            self.agent._on_llm_end(response=last_message, run_id=last_message.id, author=author)

        self._steps.defer(namespace, last_message, author, self._steps.location_key(namespace), supervisor=True)

    def _start_supervisor_steps(self, tool_calls: list[dict[str, Any]], author: str | None, location_key: str) -> None:
        handoff_calls = [tc for tc in tool_calls if self.agent._check_is_handoff_tool(tc["name"])]
        regular_calls = [tc for tc in tool_calls if not self.agent._check_is_handoff_tool(tc["name"])]
        if handoff_calls:
            if location_key:
                self.agent._queue_supervisor_handoffs(handoff_calls, author=author, location_key=location_key)
            else:
                self.agent._queue_supervisor_handoffs(handoff_calls, author=author)
        if regular_calls:
            self._start_tool_steps(regular_calls, author=author, location_key=location_key, with_author=True)

    def _start_tool_steps(
        self, tool_calls: list[dict[str, Any]], author: str | None, location_key: str, with_author: bool
    ) -> None:
        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_args = json.dumps(unpack_json_strings(tool_call["args"]), ensure_ascii=False)
            run_id = self._steps.step_run_id(tool_call.get("id", ""), location_key)
            logger.debug(f"Calling Tool: {tool_name} with input {tool_args}")
            if with_author:
                self.agent._on_tool_start(tool_name, tool_args, run_id=run_id, author=author)
            else:
                self.agent._on_tool_start(tool_name, tool_args, run_id=run_id)

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
            namespace = getattr(context, "namespace", ())
            self.parse_supervisor_update_type(context.value, author=context.author, namespace=namespace)

    def process_chunk_for_supervisor(self, chunk: Any, chunks_collector: list[str]) -> None:
        context = self.agent._supervisor_chunk_context_type.from_chunk(chunk)
        if self._steps.is_interrupt_update(context.chunk_type, context.value):
            # The gate's interrupt is streamed at its own namespace and at the root; either one starts its steps.
            self._steps.consume_identity_chunk(context.value, namespace_key="")
            return
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

    def finish_stream(self) -> None:
        """The stream ended: start the steps still waiting for call identities that never came."""
        self._steps.flush_deferred()

    def process_chunk(self, chunk: Any, chunks_collector: list[str]) -> None:
        if self.agent.subagents:
            self.process_chunk_for_supervisor(chunk, chunks_collector)
        else:
            self.process_chunk_for_agent(chunk, chunks_collector)

    def parse_tool_message(self, action: ToolMessage, author: str | None = None, location_key: str = "") -> None:
        run_id = self._steps.step_run_id(action.tool_call_id or "", location_key)
        if action.status == "error":
            self.agent._on_tool_error(action.content, run_id=run_id, author=author)
            return

        if action.status != "success":
            message = f"Unknown tool action status: {action.status}"
            message += f"\nAssistant: {self.agent.agent_name}, request_uuid: {self.agent.request_uuid}"
            message += "\nExpected 'success' or 'error'"
            logger.warning(message)
        artifact = getattr(action, "artifact", None)
        self.agent._on_tool_end(action.content, run_id=run_id, author=author, artifact=artifact)

    def process_agent_streaming(
        self,
        token: str,
        chunks_collector: list[str],
        run_id: str,
        author: str | None = None,
    ) -> None:
        self.agent._on_llm_new_token(token, run_id, author)
        self.agent.__class__.process_output(token, chunks_collector)
