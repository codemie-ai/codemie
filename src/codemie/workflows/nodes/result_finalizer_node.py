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

import time
import random
from typing import Optional

from langchain_core.messages import HumanMessage
from openai import InternalServerError, RateLimitError
from pydantic import BaseModel

from codemie.configs import logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.thought_queue import ThoughtQueue
from codemie.core.workflow_models.workflow_config import WorkflowConfig
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.templates.langgraph.workflow_prompts import result_summarizer_prompt
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.base_node import BaseNode
from codemie.workflows.utils import get_messages_from_state_schema


class ResultFinalizerException(Exception):
    pass


class ResultFinalizerNodeConfigSchema(BaseModel):
    """Configuration schema for ResultFinalizerNode (no config parameters)."""

    pass


class ResultFinalizerNode(BaseNode[AgentMessages]):
    config_schema = ResultFinalizerNodeConfigSchema

    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        workflow_config: Optional[WorkflowConfig] = None,
        *args,
        **kwargs,
    ):
        super().__init__(
            callbacks,
            workflow_execution_service,
            thought_queue,
            *args,
            workflow_config=workflow_config,
            **kwargs,
        )
        self.request_id = workflow_execution_service.workflow_execution_id

    def execute(self, state_schema: AgentMessages, execution_context: dict):
        messages = get_messages_from_state_schema(state_schema=state_schema)
        llm = get_llm_by_credentials(request_id=self.request_id)

        prompt_content = self._validate_prompt()
        if not prompt_content:
            return {"final_summary": "Unable to generate summary due to configuration error."}

        valid_messages = self._filter_valid_messages(messages)
        if not valid_messages:
            self._log_no_valid_messages(len(messages))
            return {"final_summary": "No conversation to summarize."}

        return self._invoke_llm_with_retry(llm, valid_messages, prompt_content, len(messages))

    def _validate_prompt(self) -> str | None:
        """Validate and return the prompt content, or None if invalid."""
        prompt_content = result_summarizer_prompt.strip() if result_summarizer_prompt else ""
        if not prompt_content:
            logger.error("result_summarizer_prompt is empty or contains only whitespace")
            return None
        return prompt_content

    def _filter_valid_messages(self, messages: list) -> list:
        """Filter out messages with empty or None content to avoid API errors."""
        valid_messages = []
        for msg in messages:
            if self._is_valid_message(msg):
                valid_messages.append(msg)
        return valid_messages

    def _is_valid_message(self, msg) -> bool:
        """Check if a message has valid content."""
        if not hasattr(msg, 'content'):
            return False

        if isinstance(msg.content, str):
            stripped = msg.content.strip()
            # Check for empty strings or strings that are just empty JSON objects/arrays
            return bool(stripped) and stripped not in ('{}', '[]', 'null', 'None')

        if isinstance(msg.content, list) and len(msg.content) > 0:
            return self._has_valid_list_content(msg.content)

        return False

    def _has_valid_list_content(self, content: list) -> bool:
        """Check if list content has valid text."""
        for item in content:
            if isinstance(item, dict) and item.get('text', '').strip():
                return True
            if not isinstance(item, dict) and str(item).strip():
                return True
        return False

    def _log_no_valid_messages(self, total_messages: int) -> None:
        """Log warning when no valid messages are found."""
        logger.warning(
            "Cannot summarize: no valid messages with content found",
            extra={
                "total_messages": total_messages,
                "valid_messages": 0,
                "request_id": self.request_id,
            },
        )

    def _invoke_llm_with_retry(self, llm, valid_messages: list, prompt_content: str, total_messages: int) -> dict:
        """Invoke LLM with retry logic for transient errors."""
        max_retries = 3
        backoff_factor = 2

        for retry_count in range(max_retries):
            try:
                return self._attempt_llm_invocation(llm, valid_messages, prompt_content, total_messages)
            except (InternalServerError, RateLimitError, ConnectionError, TimeoutError) as retryable_error:
                self._handle_retryable_error(retryable_error, retry_count, max_retries, backoff_factor)
            except Exception as non_retryable_error:
                self._handle_non_retryable_error(non_retryable_error, total_messages)

        raise ResultFinalizerException(
            f"Max {max_retries} retries exceeded. Failed to get a response from LLM provider. "
            f"Please retry with disabled enable_summarization_node or contact Codemie support team."
        )

    def _attempt_llm_invocation(self, llm, valid_messages: list, prompt_content: str, total_messages: int) -> dict:
        """Attempt to invoke the LLM with the given messages."""
        summarization_messages = valid_messages + [HumanMessage(content=prompt_content)]

        logger.debug(
            "Invoking LLM for summarization",
            extra={
                "total_messages": total_messages,
                "valid_messages": len(valid_messages),
                "final_message_count": len(summarization_messages),
                "prompt_length": len(prompt_content),
                "request_id": self.request_id,
            },
        )

        response = llm.invoke(summarization_messages)
        return {"final_summary": response.content}

    def _handle_retryable_error(
        self, error: Exception, retry_count: int, max_retries: int, backoff_factor: int
    ) -> None:
        """Handle retryable errors with exponential backoff."""
        logger.error(f"Call to LLM API failed on {error}")
        logger.error(f"Retrying {retry_count + 1}/{max_retries}...")
        sleep_time = backoff_factor * (2 ** (retry_count + 1) + random.uniform(0, 1))
        time.sleep(sleep_time)

    def _handle_non_retryable_error(self, error: Exception, messages_count: int) -> None:
        """Handle non-retryable errors by logging and raising exception."""
        logger.error(
            f"Call to LLM API failed on {error}",
            exc_info=True,
            extra={
                "messages_count": messages_count,
                "request_id": self.request_id,
            },
        )
        raise ResultFinalizerException(
            "Failed to get a response from LLM provider. "
            "Please retry with disabled enable_summarization_node or contact Codemie support team."
        )

    def get_task(self, state_schema: AgentMessages, *arg, **kwargs):
        return "Summarizing workflow conversation and results"

    def post_process_output(self, state_schema, task, output: dict) -> str:
        return output.get("final_summary", "")
