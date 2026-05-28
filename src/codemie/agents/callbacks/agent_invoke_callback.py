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
from typing import Dict, Any, List, Optional

from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.callbacks import StreamingStdOutCallbackHandler
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
    _escape_callback_message,
    _truncate_for_log,
    _update_tool_replay_metadata,
)
from codemie.chains.base import Thought, ThoughtOutputFormat, ThoughtAuthorType
from codemie.configs import logger
from codemie.core.constants import OUTPUT_FORMAT


class AgentInvokeCallback(StreamingStdOutCallbackHandler):
    """
    A callback handler for managing and processing thoughts during agent interactions.

    This class extends StreamingStdOutCallbackHandler to track and process thoughts
    generated during LLM operations, tool executions, and agent actions. It maintains
    a list of thoughts with their relationships and manages the current active thought.
    """

    GENERIC_TOOL_NAME = "CodeMie Thoughts"

    def __init__(self, name_resolver: NameResolver | None = None):
        super().__init__()
        self.name_resolver = name_resolver or NoOpNameResolver()
        self._parent_tracker = CallbackParentTracker()
        self._current_thoughts: Dict[str | None, Dict[str, Thought]] = {}
        self._latest_run_keys: Dict[str | None, str] = {}
        self.thoughts: List[Dict[str, Any]] = []

    @property
    def parent_id(self) -> Optional[str]:
        return self._parent_tracker.default_parent_id

    @parent_id.setter
    def parent_id(self, value: Optional[str]) -> None:
        self._parent_tracker.default_parent_id = value

    @property
    def current_thought(self):
        return self._get_current_thought()

    @staticmethod
    def _get_run_key(run_id: Any | None) -> str:
        return str(run_id) if run_id is not None else "__default__"

    def _get_author_thoughts(self, author: str | None = None) -> Dict[str, Thought]:
        return self._current_thoughts.setdefault(author, {})

    def _get_current_thought(self, author: str | None = None, run_id: Any | None = None) -> Optional[Thought]:
        author_thoughts = self._get_author_thoughts(author)
        if run_id is not None:
            return author_thoughts.get(self._get_run_key(run_id))

        latest_run_key = self._latest_run_keys.get(author)
        if latest_run_key is not None:
            return author_thoughts.get(latest_run_key)

        if len(author_thoughts) == 1:
            return next(iter(author_thoughts.values()))

        return None

    def _get_parent_id(self, author: str | None = None) -> Optional[str]:
        return self._parent_tracker.get(author)

    def thought_processing(self, thought: Optional[Thought]) -> None:
        """
        Process and store a thought, either updating an existing one or adding a new one.

        Args:
            thought: A Thought object containing the thought information to process.
                    Can be None, in which case no processing occurs.
        """
        if thought:
            existing_thought = next((item for item in self.thoughts if item['id'] == thought.id), None)

            if existing_thought:
                existing_thought['message'] += thought.message
                existing_thought['children'] += thought.children
                if thought.error:
                    existing_thought['error'] = thought.error
                if thought.metadata:
                    existing_thought['metadata'] = {**existing_thought.get('metadata', {}), **thought.metadata}
                existing_thought['in_progress'] = thought.in_progress
                existing_thought['output_format'] = thought.output_format
            else:
                thought_object = {
                    'id': thought.id,
                    'message': thought.message,
                    'input_text': thought.input_text,
                    'author_name': thought.author_name,
                    'children': thought.children,
                    'author_type': thought.author_type,
                    'parent_id': thought.parent_id,
                    'error': thought.error,
                    'metadata': thought.metadata,
                    'in_progress': thought.in_progress,
                    'output_format': thought.output_format,
                }
                self.thoughts.append(thought_object)

    def set_current_thought(
        self,
        tool_name: str = '',
        input_text: str = '',
        output_format: ThoughtOutputFormat = ThoughtOutputFormat.TEXT,
        author: str | None = None,
        run_id: Any | None = None,
    ):
        run_key = self._get_run_key(run_id)
        author_thoughts = self._get_author_thoughts(author)
        current_thought = author_thoughts.get(run_key)
        if current_thought:
            current_thought.in_progress = False
            self.thought_processing(current_thought)

        # Resolve display name (handles handoff tool name mapping)
        display_name = resolve_tool_display_name(tool_name, self.name_resolver)

        author_thoughts[run_key] = Thought(
            id=str(uuid.uuid4()),
            author_name=display_name,
            parent_id=self._get_parent_id(author),
            author_type=ThoughtAuthorType.Tool.value,
            output_format=output_format,
            input_text=input_text,
            message='',
            in_progress=True,
            metadata=_build_tool_metadata(tool_name, input_text) if input_text else {},
        )
        self._latest_run_keys[author] = run_key

    def reset_current_thought(self, author: str | None = None, run_id: Any | None = None):
        author_thoughts = self._current_thoughts.get(author)
        if not author_thoughts:
            return

        run_key = self._get_run_key(run_id) if run_id is not None else self._latest_run_keys.get(author, "__default__")
        author_thoughts.pop(run_key, None)

        if not author_thoughts:
            self._current_thoughts.pop(author, None)
            self._latest_run_keys.pop(author, None)
            return

        if self._latest_run_keys.get(author) == run_key:
            self._latest_run_keys[author] = next(reversed(author_thoughts))

    def set_context(self, context: dict, parent_thought_id: str | None, author: str | None = None):
        self._parent_tracker.set(parent_thought_id, author=author)

    def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any) -> None:
        """Run when LLM starts running."""
        author = kwargs.get("author")
        run_id = kwargs.get("run_id")
        self.set_current_thought(tool_name=self.GENERIC_TOOL_NAME, author=author, run_id=run_id)

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        """Run on new LLM token. Only available when streaming is enabled."""
        author = kwargs.get("author")
        run_id = kwargs.get("run_id")
        current_thought = self._get_current_thought(author, run_id=run_id)
        if not current_thought:
            self.set_current_thought(tool_name=self.GENERIC_TOOL_NAME, author=author, run_id=run_id)
            current_thought = self._get_current_thought(author, run_id=run_id)
        current_thought.message = self._escape_message(token)

        self.thought_processing(current_thought)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Run when LLM ends running."""
        author = kwargs.get("author")
        run_id = kwargs.get("run_id")
        current_thought = self._get_current_thought(author, run_id=run_id)
        if not current_thought:
            return
        current_thought.message = ''
        current_thought.in_progress = False
        self.thought_processing(current_thought)
        self.reset_current_thought(author, run_id=run_id)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """Run when LLM errors."""
        self._debug(f"Error in LLM response generation: {error}")
        author = kwargs.get("author")
        run_id = kwargs.get("run_id")

        current_thought = self._get_current_thought(author, run_id=run_id)
        if not current_thought:
            self.set_current_thought(tool_name=self.GENERIC_TOOL_NAME, author=author, run_id=run_id)
            current_thought = self._get_current_thought(author, run_id=run_id)

        current_thought.message = self._escape_message(str(error))
        current_thought.error = True
        current_thought.in_progress = False

        self.thought_processing(current_thought)

        self.reset_current_thought(author, run_id=run_id)

    def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs: Any) -> None:
        """Run when chain starts running."""
        self._debug(f"On Chain start: {inputs}")

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs: Any) -> None:
        """Run when chain ends running."""

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        """Run when chain errors."""

    def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> None:
        output_format = kwargs.get('metadata', {}).get(OUTPUT_FORMAT)
        author = kwargs.get("author")
        run_id = kwargs.get("run_id")
        self.set_current_thought(
            tool_name=serialized['name'],
            input_text=input_str,
            output_format=output_format,
            author=author,
            run_id=run_id,
        )

        self.thought_processing(self._get_current_thought(author, run_id=run_id))

    def on_agent_action(self, action: AgentAction, **kwargs: Any) -> Any:
        """Run on agent action."""

    def on_agent_finish(self, finish: AgentFinish, **kwargs: Any) -> None:
        """Run on agent end."""
        self._debug(f"On Agent end: {finish}")

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        """Run when tool ends running."""
        author = kwargs.get("author")
        run_id = kwargs.get("run_id")
        current_thought = self._get_current_thought(author, run_id=run_id)
        if not current_thought:
            return
        message = _build_tool_message(output)
        current_thought.message = self._escape_message(message)
        current_thought.in_progress = False
        replay_metadata = getattr(current_thought, "metadata", None)
        _update_tool_replay_metadata(replay_metadata, output, is_error=False)
        logger.debug(
            f"Invoke callback tool end. Tool={current_thought.author_name}, "
            f"Output={_truncate_for_log(str(output))}, ReplayMetadata={replay_metadata}"
        )

        self.thought_processing(current_thought)
        self.reset_current_thought(author, run_id=run_id)

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """Run when tool errors."""
        self._debug(f"Error in tool calling: {error}")
        author = kwargs.get("author")
        run_id = kwargs.get("run_id")
        current_thought = self._get_current_thought(author, run_id=run_id)
        if not current_thought:
            return
        current_thought.message = self._escape_message(str(error))
        current_thought.error = True
        current_thought.in_progress = False
        replay_metadata = getattr(current_thought, "metadata", None)
        _update_tool_replay_metadata(replay_metadata, error, is_error=True)
        logger.debug(
            f"Invoke callback tool error. Tool={current_thought.author_name}, "
            f"Error={_truncate_for_log(str(error))}, ReplayMetadata={replay_metadata}"
        )

        self.thought_processing(current_thought)
        self.reset_current_thought(author, run_id=run_id)

    def on_text(self, text: str, **kwargs: Any) -> None:
        """Run on arbitrary text."""
        self._debug(f"On Text: {text}")

    def _debug(self, msg: str) -> None:
        logger.debug(msg)

    def _escape_message(self, message: str) -> str:
        return _escape_callback_message(message)
