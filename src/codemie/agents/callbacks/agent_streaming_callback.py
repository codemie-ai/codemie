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
from langchain_core.outputs import LLMResult

from codemie.agents.callbacks.utils.name_resolver import (
    NameResolver,
    NoOpNameResolver,
    resolve_tool_display_name,
)
from codemie.agents.callbacks.callback_utils import (
    _build_tool_metadata,
    _summarize_tool_output,
    _truncate_for_log,
)
from codemie.chains.base import StreamedGenerationResult, Thought, ThoughtOutputFormat, ThoughtAuthorType
from codemie.core.constants import OUTPUT_FORMAT, ToolNamePrefix
from codemie.configs import logger
from codemie.configs.logger import set_logging_info
from codemie.core.thread import ThreadedGenerator
from codemie.core.utils import extract_text_from_llm_output
from codemie.core.thought_queue import ThoughtQueue
from codemie.service.mcp.models import MCPToolInvocationResponse
from codemie.service.conversation.history_projection_service import (
    TOOL_STATUS_COMPLETED,
    TOOL_STATUS_ERROR,
)


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
        self.parent_id = None
        self.context = None
        # Per-author storage: None key is the default (no author).
        self._storages: dict[str | None, ThoughtInMemoryStorage] = {None: ThoughtInMemoryStorage()}

    def _get_storage(self, author: str | None = None) -> ThoughtInMemoryStorage:
        """Return (lazily creating) the ThoughtInMemoryStorage for *author*."""
        if author not in self._storages:
            self._storages[author] = ThoughtInMemoryStorage(parent_id=self.parent_id)
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

    def on_llm_new_token(self, token: str, *, run_id: UUID, author: str | None = None, **kwargs: Any) -> None:
        storage = self._get_storage(author)
        if not storage.get(str(run_id)):
            storage.create_thought(run_id=run_id, tool_name=self.GENERIC_TOOL_NAME)
        thought = storage.update_thought(run_id, message=self._escape_message(token))
        if thought is None:
            return
        self._send_thought(thought)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, author: str | None = None, **kwargs: Any) -> None:
        storage = self._get_storage(author)
        thought = storage.update_thought(run_id, message='', in_progress=False)
        if thought is None:
            return
        self._send_thought(thought)
        storage.delete_thought(run_id)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, author: str | None = None, **kwargs: Any) -> None:
        self._debug(f"Error in LLM response generation: {error}")
        storage = self._get_storage(author)
        if not storage.get(str(run_id)):
            storage.create_thought(run_id=run_id, tool_name=self.GENERIC_TOOL_NAME)
        execution_error: str = "guardrails" if "content blocked" in str(error).lower() else "stacktrace"
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

        message = f"{output} \n\n"
        thought = storage.update_thought(
            run_id,
            message=self._escape_message(message),
            in_progress=False,
        )
        if thought and thought.metadata:
            tool_name = thought.metadata.get("tool_name", "").lower()
            thought.metadata["status"] = TOOL_STATUS_COMPLETED
            thought.metadata["result_summary"] = _summarize_tool_output(tool_name, str(output))
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
        execution_error: str = "guardrails" if "content blocked" in str(error).lower() else "stacktrace"
        thought = storage.update_thought(
            run_id,
            message=message,
            in_progress=False,
            error=True,
        )
        if thought and thought.metadata:
            tool_name = thought.metadata.get("tool_name", "").lower()
            thought.metadata["status"] = TOOL_STATUS_ERROR
            thought.metadata["result_summary"] = _summarize_tool_output(tool_name, str(error))
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
        )
        logger.debug(msg)

    def _escape_message(self, message: str) -> str:
        """Replace '}{' with '}_{' so frontend can split it properly."""
        text = extract_text_from_llm_output(message)
        return text.replace("}{", "}_{")

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
            self.parent_id = parent_thought_id
        if author in self._storages:
            # Update parent_id in-place so thoughts already in this storage are preserved.
            self._storages[author].parent_id = parent_thought_id
        else:
            self._storages[author] = ThoughtInMemoryStorage(parent_id=parent_thought_id)
