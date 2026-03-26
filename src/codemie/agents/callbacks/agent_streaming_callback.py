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
from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.callbacks import StreamingStdOutCallbackHandler
from langchain_core.outputs import LLMResult
from typing import Dict, Any, List


from codemie.chains.base import StreamedGenerationResult, Thought, ThoughtOutputFormat, ThoughtAuthorType
from codemie.core.constants import ToolNamePrefix
from codemie.configs import logger
from codemie.configs.logger import set_logging_info
from codemie.core.thread import ThreadedGenerator
from codemie.core.utils import extract_text_from_llm_output
from codemie.core.thought_queue import ThoughtQueue
from codemie.core.constants import OUTPUT_FORMAT
from codemie.service.mcp.models import MCPToolInvocationResponse


class AgentStreamingCallback(StreamingStdOutCallbackHandler):
    GENERIC_TOOL_NAME = "CodeMie Thoughts"

    def __init__(self, gen: ThoughtQueue | ThreadedGenerator):
        super().__init__()
        self.gen = gen
        self.parent_id = None
        self.context = None
        self._current_thought = None

    @property
    def current_thought(self):
        return self._current_thought or None

    def set_current_thought(
        self, tool_name: str = '', input_text: str = '', output_format: ThoughtOutputFormat = ThoughtOutputFormat.TEXT
    ):
        if self._current_thought:
            self._current_thought.in_progress = False

            self.gen.send(
                StreamedGenerationResult(
                    thought=self.current_thought,
                    context=self.context,
                ).model_dump_json()
            )

        is_agent_tool = tool_name.startswith(ToolNamePrefix.AGENT.value)
        if is_agent_tool:
            tool_name = tool_name[len(ToolNamePrefix.AGENT.value) :]
        tool_name = tool_name.replace('_', ' ').title()
        author_type = ThoughtAuthorType.Agent.value if is_agent_tool else ThoughtAuthorType.Tool.value

        self._current_thought = Thought(
            id=str(uuid.uuid4()),
            author_name=tool_name,
            parent_id=self.parent_id,
            author_type=author_type,
            output_format=output_format,
            input_text=input_text,
            message='',
            in_progress=True,
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

        self.gen.send(
            StreamedGenerationResult(
                thought=self.current_thought,
                context=self.context,
            ).model_dump_json()
        )

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Run when LLM ends running."""
        self.current_thought.in_progress = False
        self.current_thought.message = ""

        self.gen.send(
            StreamedGenerationResult(
                thought=self.current_thought,
                context=self.context,
            ).model_dump_json()
        )

        self.reset_current_thought()

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """Run when LLM errors."""
        self._debug(f"Error in LLM response generation: {error}")

        if not self.current_thought:
            self.set_current_thought(tool_name=self.GENERIC_TOOL_NAME)

        self.current_thought.message = self._escape_message(str(error))
        self.current_thought.error = True
        self.current_thought.in_progress = False

        execution_error: str = "guardrails" if "content blocked" in str(error).lower() else "stacktrace"

        self.gen.send(
            StreamedGenerationResult(
                thought=self.current_thought, context=self.context, execution_error=execution_error
            ).model_dump_json()
        )

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

        self.gen.send(
            StreamedGenerationResult(
                thought=self.current_thought,
                context=self.context,
            ).model_dump_json()
        )

    def on_agent_action(self, action: AgentAction, **kwargs: Any) -> Any:
        """Run on agent action."""

    def on_agent_finish(self, finish: AgentFinish, **kwargs: Any) -> None:
        """Run on agent end."""
        self._debug(f"On Agent end: {finish}")

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """Run when tool ends running."""
        output = self._tool_result_preprocessing(output)

        message = f"{output} \n\n"
        self.current_thought.message = self._escape_message(message)
        self.current_thought.in_progress = False

        self.gen.send(
            StreamedGenerationResult(
                thought=self.current_thought,
                context=self.context,
            ).model_dump_json()
        )
        self.reset_current_thought()

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """Run when tool errors."""
        self._debug(f"Error in tool calling: {error}")
        self.current_thought.message = self._escape_message(str(error))
        self.current_thought.error = True
        self.current_thought.in_progress = False

        self.gen.send(
            StreamedGenerationResult(
                thought=self.current_thought,
                context=self.context,
            ).model_dump_json()
        )
        self.reset_current_thought()

    def on_text(self, text: str, **kwargs: Any) -> None:
        """Run on arbitrary text."""
        self._debug(f"On Text: {text}")

    def _debug(self, msg: str) -> None:
        """Debug with logging info"""
        set_logging_info(
            uuid=self.gen.context.request_uuid,
            user_id=self.gen.context.user_id,
        )
        logger.debug(msg)

    def _escape_message(self, message: str) -> str:
        """Replace '}{', with '}{\u2002' so frontend can split it properly"""
        text = extract_text_from_llm_output(message)
        return text.replace("}{", "}_{")

    def _tool_result_preprocessing(self, tool_result: Any) -> Any:
        """Preprocess the tool result before sending it to the generator."""
        if isinstance(tool_result, MCPToolInvocationResponse):
            result = "\n".join(str(item) for item in tool_result.content)
        else:
            result = tool_result
        return result
