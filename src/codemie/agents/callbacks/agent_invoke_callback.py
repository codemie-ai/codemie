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

from codemie.agents.callbacks.callback_utils import (
    _build_tool_metadata,
    _summarize_tool_output,
    _truncate_for_log,
)
from codemie.chains.base import Thought, ThoughtOutputFormat, ThoughtAuthorType
from codemie.configs import logger
from codemie.core.utils import extract_text_from_llm_output
from codemie.core.constants import OUTPUT_FORMAT
from codemie.service.conversation.history_projection_service import (
    TOOL_STATUS_COMPLETED,
    TOOL_STATUS_ERROR,
)


class AgentInvokeCallback(StreamingStdOutCallbackHandler):
    """
    A callback handler for managing and processing thoughts during agent interactions.

    This class extends StreamingStdOutCallbackHandler to track and process thoughts
    generated during LLM operations, tool executions, and agent actions. It maintains
    a list of thoughts with their relationships and manages the current active thought.
    """

    GENERIC_TOOL_NAME = "CodeMie Thoughts"

    def __init__(self):
        super().__init__()
        self.parent_id: Optional[str] = None
        self._current_thought: Optional[Thought] = None
        self.thoughts: List[Dict[str, Any]] = []

    @property
    def current_thought(self):
        return self._current_thought or None

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
        self, tool_name: str = '', input_text: str = '', output_format: ThoughtOutputFormat = ThoughtOutputFormat.TEXT
    ):
        if self._current_thought:
            self._current_thought.in_progress = False

            self.thought_processing(self.current_thought)

        tool_name = tool_name.replace('_', ' ').title()
        self._current_thought = Thought(
            id=str(uuid.uuid4()),
            author_name=tool_name,
            parent_id=self.parent_id,
            author_type=ThoughtAuthorType.Tool.value,
            output_format=output_format,
            input_text=input_text,
            message='',
            in_progress=True,
            metadata=_build_tool_metadata(tool_name, input_text) if input_text else {},
        )

    def reset_current_thought(self):
        self._current_thought = None

    def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any) -> None:
        """Run when LLM starts running."""
        self.set_current_thought(tool_name=self.GENERIC_TOOL_NAME)

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        """Run on new LLM token. Only available when streaming is enabled."""
        if not self.current_thought:
            self.set_current_thought(tool_name=self.GENERIC_TOOL_NAME)
        self.current_thought.message = self._escape_message(token)

        self.thought_processing(self.current_thought)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Run when LLM ends running."""
        self.current_thought.in_progress = False
        self.thought_processing(self.current_thought)
        self.reset_current_thought()

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """Run when LLM errors."""
        self._debug(f"Error in LLM response generation: {error}")

        if not self.current_thought:
            self.set_current_thought(tool_name=self.GENERIC_TOOL_NAME)

        self.current_thought.message = self._escape_message(str(error))
        self.current_thought.error = True
        self.current_thought.in_progress = False

        self.thought_processing(self.current_thought)

        self.reset_current_thought()

    def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs: Any) -> None:
        """Run when chain starts running."""
        self._debug(f"On Chain start: {inputs}")

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs: Any) -> None:
        """Run when chain ends running."""

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        """Run when chain errors."""

    def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> None:
        output_format = kwargs.get('metadata', {}).get(OUTPUT_FORMAT)
        self.set_current_thought(tool_name=serialized['name'], input_text=input_str, output_format=output_format)

        self.thought_processing(self.current_thought)

    def on_agent_action(self, action: AgentAction, **kwargs: Any) -> Any:
        """Run on agent action."""

    def on_agent_finish(self, finish: AgentFinish, **kwargs: Any) -> None:
        """Run on agent end."""
        self._debug(f"On Agent end: {finish}")

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        """Run when tool ends running."""
        message = f"{output} \n\n"
        self.current_thought.message = self._escape_message(message)
        self.current_thought.in_progress = False
        replay_metadata = getattr(self.current_thought, "metadata", None)
        if replay_metadata:
            tool_name = replay_metadata.get("tool_name", "").lower()
            replay_metadata["status"] = TOOL_STATUS_COMPLETED
            replay_metadata["result_summary"] = _summarize_tool_output(tool_name, str(output))
        logger.debug(
            f"Invoke callback tool end. Tool={self.current_thought.author_name}, "
            f"Output={_truncate_for_log(str(output))}, ReplayMetadata={replay_metadata}"
        )

        self.thought_processing(self.current_thought)
        self.reset_current_thought()

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """Run when tool errors."""
        self._debug(f"Error in tool calling: {error}")
        self.current_thought.message = self._escape_message(str(error))
        self.current_thought.error = True
        self.current_thought.in_progress = False
        replay_metadata = getattr(self.current_thought, "metadata", None)
        if replay_metadata:
            tool_name = replay_metadata.get("tool_name", "").lower()
            replay_metadata["status"] = TOOL_STATUS_ERROR
            replay_metadata["result_summary"] = _summarize_tool_output(tool_name, str(error))
        logger.debug(
            f"Invoke callback tool error. Tool={self.current_thought.author_name}, "
            f"Error={_truncate_for_log(str(error))}, ReplayMetadata={replay_metadata}"
        )

        self.thought_processing(self.current_thought)
        self.reset_current_thought()

    def on_text(self, text: str, **kwargs: Any) -> None:
        """Run on arbitrary text."""
        self._debug(f"On Text: {text}")

    def _debug(self, msg: str) -> None:
        """Debug with logging info"""
        logger.debug(msg)

    def _escape_message(self, message: str) -> str:
        """Replace '}{', with '}{\u2002' so frontend can split it properly"""
        text = extract_text_from_llm_output(message)
        return text.replace("}{", "}_{")
