# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
from uuid import UUID
from typing import Dict, Any, List

from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.callbacks import StreamingStdOutCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from codemie.agents.callbacks.utils.name_resolver import (
    NameResolver,
    NoOpNameResolver,
    resolve_tool_display_name,
)
from codemie.agents.callbacks.utils.parent_context import CallbackParentTracker
from codemie.agents.callbacks.callback_utils import (
    _build_tool_metadata,
    _build_tool_message,
    _classify_execution_error,
    _escape_callback_message,
    _truncate_for_log,
    _update_tool_replay_metadata,
)
from codemie.chains.base import StreamedGenerationResult, Thought, ThoughtOutputFormat, ThoughtAuthorType
from codemie.core.constants import OUTPUT_FORMAT, ToolNamePrefix
from codemie.configs import logger
from codemie.configs.logger import current_user_email, set_logging_info
from codemie.core.routing_info import RoutingInfo, compose_routing_info, default_routing_extractors
from codemie.core.thread import ThreadedGenerator
from codemie.core.thought_queue import ThoughtQueue
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.mcp.models import MCPToolInvocationResponse


class ThoughtInMemoryStorage(dict[str, Thought]):
    parent_id: str | None

    def __init__(self, parent_id: str | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.parent_id = parent_id

    def create_thought(
        self,
        run_id: UUID,
        tool_name: str,
        input_text: str = '',
        output_format: ThoughtOutputFormat | None = None,
        by_run_id: bool = False,
    ) -> Thought:
        """Create and store a new in-progress thought for run_id."""
        output_format = output_format or ThoughtOutputFormat.TEXT
        is_agent_tool = tool_name.startswith(ToolNamePrefix.AGENT.value)
        if is_agent_tool:
            tool_name = tool_name[len(ToolNamePrefix.AGENT.value) :]
        tool_name = tool_name.replace('_', ' ').title()
        author_type = ThoughtAuthorType.Agent.value if is_agent_tool else ThoughtAuthorType.Tool.value

        thought_id = str(run_id) if by_run_id else str(uuid.uuid4())

        thought = Thought(
            id=thought_id,
            author_name=tool_name,
            parent_id=self.parent_id,
            author_type=author_type,
            output_format=output_format,
            input_text=input_text,
            message='',
            in_progress=True,
            metadata=_build_tool_metadata(tool_name, input_text) if input_text else {},
        )
        self[str(run_id)] = thought
        return thought

    def update_thought(self, run_id: UUID | str, **fields: Any) -> Thought | None:
        """Update thought fields by run_id and emit the current state."""
        thought = self.get(str(run_id))
        if thought is None:
            return None
        for key, value in fields.items():
            setattr(thought, key, value)
        return thought

    def delete_thought(self, run_id: UUID | str) -> None:
        """Remove a thought from storage without emitting."""
        self.pop(str(run_id), None)


class AgentStreamingCallback(StreamingStdOutCallbackHandler):
    GENERIC_TOOL_NAME = "CodeMie Thoughts"

    def __init__(self, gen: ThoughtQueue | ThreadedGenerator, name_resolver: NameResolver | None = None):
        super().__init__()
        self.gen = gen
        self.name_resolver: NameResolver = name_resolver or NoOpNameResolver()
        self.context = None
        self._parent_tracker = CallbackParentTracker()
        # Per-author storage: None key is the default (no author).
        self._storages: dict[str | None, ThoughtInMemoryStorage] = {
            None: ThoughtInMemoryStorage(parent_id=self._parent_tracker.get())
        }
        # Routing info (routed model / classifier cost) from the most recent Switchyard- or
        # LiteLLM-router-routed call. Updated in on_llm_end, stamped onto thoughts in on_tool_start.
        self._last_routing: RoutingInfo | None = None

    @property
    def parent_id(self) -> str | None:
        return self._parent_tracker.default_parent_id

    @parent_id.setter
    def parent_id(self, value: str | UUID | None) -> None:
        parent_thought_id = str(value) if value is not None else None
        self._parent_tracker.default_parent_id = parent_thought_id

    def _get_storage(self, author: str | None = None) -> ThoughtInMemoryStorage:
        """Return (lazily creating) the ThoughtInMemoryStorage for *author*."""
        if author not in self._storages:
            self._storages[author] = ThoughtInMemoryStorage(parent_id=self._parent_tracker.get(author))
        return self._storages[author]

    @property
    def thoughts_storage(self) -> ThoughtInMemoryStorage:
        """Backward-compatible accessor for the default (author=None) storage."""
        return self._storages[None]

    def _send_thought(self, thought: Thought, execution_error: str | None = None) -> None:
        self.gen.send(
            StreamedGenerationResult(
                thought=thought,
                context=self.context,
                execution_error=execution_error,
            ).model_dump_json()
        )

    def on_llm_start(
        self, serialized: Dict[str, Any], prompts: List[str], *, run_id: UUID, author: str | None = None, **kwargs: Any
    ) -> None:
        storage = self._get_storage(author)
        thought = storage.create_thought(run_id=run_id, tool_name=self.GENERIC_TOOL_NAME)
        self._send_thought(thought)

    def _update_last_routing(self, info: RoutingInfo) -> None:
        """Merge *info* into ``_last_routing`` when it carries at least one field."""
        if not info.is_empty():
            self._last_routing = info.merged_over(self._last_routing) if self._last_routing else info

    def _extract_response_routing(self, response: "LLMResult | AIMessage | str") -> tuple[str | None, float | None]:
        """Extract routed-model and classifier-cost from *response*, updating ``_last_routing``."""
        info = RoutingInfo()
        if isinstance(response, AIMessage) or (not isinstance(response, str) and hasattr(response, "generations")):
            info = compose_routing_info(response, default_routing_extractors())
        self._update_last_routing(info)
        return info.routed_model, info.classifier_cost_usd

    def _build_routing_update_fields(
        self,
        response_tier: str | None,
        response_cost: float | None,
        existing_thought: "Thought | None",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return (routing_fields, metadata_updates) to be merged into the thought update."""
        metadata: dict[str, Any] = (
            dict(existing_thought.metadata) if existing_thought and existing_thought.metadata else {}
        )
        routing_fields: dict[str, Any] = {}
        if response_tier is not None:
            routing_fields["routed_model"] = response_tier
            metadata["llm_tier"] = response_tier
            label = llm_service.get_model_label(response_tier)
            if label:
                routing_fields["routed_model_label"] = label
        if response_cost is not None:
            routing_fields["classifier_cost_usd"] = response_cost
            metadata["classifier_cost_usd"] = response_cost
        return routing_fields, metadata

    def on_llm_new_token(self, token: str, *, run_id: UUID, author: str | None = None, **kwargs: Any) -> None:
        storage = self._get_storage(author)
        if not storage.get(str(run_id)):
            storage.create_thought(run_id=run_id, tool_name=self.GENERIC_TOOL_NAME)
        thought = storage.update_thought(run_id, message=self._escape_message(token))
        if thought is None:
            return
        self._send_thought(thought)

    def on_llm_end(
        self,
        response: LLMResult | AIMessage | str,
        *,
        run_id: UUID,
        author: str | None = None,
        **kwargs: Any,
    ) -> None:
        # Extract the routed model and classifier cost from the response itself. We
        # intentionally keep this separate from ``_last_routing`` so that we do not
        # stamp a stale base name onto the thought before the real routed model is known.
        response_tier, response_cost = self._extract_response_routing(response)

        storage = self._get_storage(author)
        existing_thought = storage.get(str(run_id))
        update_kwargs: dict[str, Any] = {"in_progress": False}

        # A redundant finalize is a second on_llm_end for a run_id that was already
        # finalized (in_progress False) by a previous call. Without new routing
        # metadata to add, there is nothing left to send.
        already_finalized = existing_thought is not None and existing_thought.in_progress is False

        # Only stamp the routed model when it was actually present in the
        # response. This avoids briefly showing a stale base name when the
        # LangGraph "messages" final chunk arrives before the "updates" chunk
        # that carries the model metadata.
        model_resolved = response_tier is not None
        routing_fields, metadata = self._build_routing_update_fields(response_tier, response_cost, existing_thought)
        # Derived from the raw inputs, not from `metadata`: _build_routing_update_fields
        # seeds `metadata` from existing_thought's already-stored metadata, so using the
        # metadata dict here would treat carried-over prior routing info as "new".
        has_new_routing = response_tier is not None or response_cost is not None

        if already_finalized and not has_new_routing:
            storage.delete_thought(run_id)
            return

        existing_message = getattr(existing_thought, "message", "") or ""
        update_kwargs["message"] = self._resolve_llm_end_message(already_finalized, existing_message, response)

        if routing_fields:
            update_kwargs["routing"] = RoutingInfo(**routing_fields)
        if metadata:
            update_kwargs["metadata"] = metadata

        thought = storage.update_thought(run_id, **update_kwargs)
        if thought is None:
            return
        self._send_thought(thought)
        # Keep the thought around if we haven't resolved the model yet; a
        # later LangGraph update with metadata will finish it. A redundant
        # call always leaves the thought removed afterward.
        if model_resolved or isinstance(response, str) or already_finalized:
            storage.delete_thought(run_id)

    def _resolve_llm_end_message(
        self,
        already_finalized: bool,
        existing_message: str,
        response: LLMResult | AIMessage | str,
    ) -> str:
        """Resolve the thought text for on_llm_end.

        Avoids duplicating streamed text: if tokens were already emitted, or this is a
        redundant finalize carrying only new routing metadata, there is no new text to add.
        """
        if already_finalized or existing_message:
            return ""
        if not isinstance(response, AIMessage):
            return ""
        content = response.content
        if isinstance(content, list):
            content = " ".join(str(block) for block in content)
        return self._escape_message(str(content))

    def on_llm_error(self, error: BaseException, *, run_id: UUID, author: str | None = None, **kwargs: Any) -> None:
        self._debug(f"Error in LLM response generation: {error}")
        storage = self._get_storage(author)
        if not storage.get(str(run_id)):
            storage.create_thought(run_id=run_id, tool_name=self.GENERIC_TOOL_NAME)
        execution_error = _classify_execution_error(error)
        thought = storage.update_thought(
            run_id,
            message=self._escape_message(str(error)),
            error=True,
            in_progress=False,
        )
        if thought is None:
            return
        self._send_thought(thought, execution_error)
        storage.delete_thought(run_id)

    # ── Chain callbacks ────────────────────────────────────────────────────

    def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs: Any) -> None:
        self._debug(f"On Chain start: {inputs}")

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs: Any) -> None:
        # No thought needed to send on chain end. For now
        pass

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        # No thought needed to send on chain error. For now
        pass

    # ── Tool callbacks ─────────────────────────────────────────────────────

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_thought_id: UUID | None = None,
        author: str | None = None,
        **kwargs: Any,
    ) -> None:
        storage = self._get_storage(author)
        output_format = kwargs.get('metadata', {}).get(OUTPUT_FORMAT)
        tool_name = serialized['name']
        tool_display_name = resolve_tool_display_name(tool_name, self.name_resolver)
        thought = storage.create_thought(
            run_id=run_id,
            tool_name=tool_display_name,
            input_text=input_str,
            output_format=output_format,
            by_run_id=True,
        )
        last_routing = self._last_routing
        if last_routing is not None and last_routing.routed_model is not None and thought.metadata is not None:
            thought.metadata["llm_tier"] = last_routing.routed_model
        if last_routing is not None and not last_routing.is_empty():
            label = (
                llm_service.get_model_label(last_routing.routed_model)
                if last_routing.routed_model is not None
                else None
            )
            new_routing = RoutingInfo(
                routed_model=last_routing.routed_model,
                routed_model_label=label,
                classifier_cost_usd=last_routing.classifier_cost_usd,
            )
            thought.routing = new_routing.merged_over(thought.routing) if thought.routing else new_routing
        logger.debug(
            f"Streaming callback tool start. Tool={thought.author_name or serialized['name']}, "
            f"Input={_truncate_for_log(input_str)}, ReplayMetadata={thought.metadata}"
        )
        self._send_thought(thought)

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        author: str | None = None,
        **kwargs: Any,
    ) -> None:
        storage = self._get_storage(author)
        output = self._tool_result_preprocessing(output)
        thought = storage.get(str(run_id))
        if thought is None:
            return

        message = _build_tool_message(output)
        thought = storage.update_thought(
            run_id,
            message=self._escape_message(message),
            in_progress=False,
        )
        if thought:
            _update_tool_replay_metadata(thought.metadata, output, is_error=False)
            artifact = kwargs.get("artifact")
            if artifact and isinstance(artifact, list) and thought.metadata:
                image_data = [
                    {"data": item["data"], "mime_type": item["mime_type"]}
                    for item in artifact
                    if isinstance(item, dict) and "data" in item and "mime_type" in item
                ]
                if image_data:
                    thought.metadata["image_artifacts"] = image_data
        logger.debug(
            f"Streaming callback tool end. Tool={getattr(thought, 'author_name', None)}, "
            f"Output={_truncate_for_log(str(output))}, ReplayMetadata={getattr(thought, 'metadata', None)}"
        )
        self._send_thought(thought)
        storage.delete_thought(run_id)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        author: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._debug(f"Error in tool calling: {error}")
        storage = self._get_storage(author)
        thought = storage.get(str(run_id))
        if thought is None:
            return
        message = self._escape_message(str(error))
        execution_error = _classify_execution_error(error)
        thought = storage.update_thought(
            run_id,
            message=message,
            in_progress=False,
            error=True,
        )
        if thought:
            _update_tool_replay_metadata(thought.metadata, error, is_error=True)
        logger.debug(
            f"Streaming callback tool error. Tool={getattr(thought, 'author_name', None)}, "
            f"Error={_truncate_for_log(str(error))}, ReplayMetadata={getattr(thought, 'metadata', None)}"
        )
        self._send_thought(thought, execution_error)
        storage.delete_thought(run_id)

    def on_agent_action(self, action: AgentAction, **kwargs: Any) -> Any:
        # No thought needed to send on agent action. For now
        pass

    def on_agent_finish(self, finish: AgentFinish, **kwargs: Any) -> None:
        self._debug(f"On Agent end: {finish}")

    def on_text(self, text: str, **kwargs: Any) -> None:
        self._debug(f"On Text: {text}")

    # ── Helpers ────────────────────────────────────────────────────────────

    def _debug(self, msg: str) -> None:
        set_logging_info(
            uuid=self.gen.context.request_uuid,
            user_id=self.gen.context.user_id,
            conversation_id=getattr(self.gen, "conversation_id", None) or "-",
            user_email=current_user_email.get(),
        )
        logger.debug(msg)

    def _escape_message(self, message: str) -> str:
        return _escape_callback_message(message)

    def _tool_result_preprocessing(self, tool_result: Any) -> Any:
        """Preprocess the tool result before sending it to the generator."""
        if isinstance(tool_result, MCPToolInvocationResponse):
            return "\n".join(str(item) for item in tool_result.content)
        return tool_result

    def set_context(self, context: dict, parent_thought_id: str | UUID | None, author: str | None = None):
        # Only update the instance-level context/parent_id for the default (supervisor) author.
        # Subagent authors must not overwrite the top-level context used by supervisor thoughts,
        # otherwise the handoff thought is opened with context=None but closed with context={},
        # and the UI fails to recognise the closing event as belonging to the same thought.
        if author is None:
            self.context = context
        normalized_parent_id = str(parent_thought_id) if parent_thought_id is not None else None
        self._parent_tracker.set(normalized_parent_id, author=author)
        if author in self._storages:
            # Update parent_id in-place so thoughts already in this storage are preserved.
            self._storages[author].parent_id = self._parent_tracker.get(author)
        else:
            self._storages[author] = ThoughtInMemoryStorage(parent_id=self._parent_tracker.get(author))
