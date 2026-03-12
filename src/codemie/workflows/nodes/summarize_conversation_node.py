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

from typing import Any, Type

from langchain_core.messages import HumanMessage, RemoveMessage
from langgraph.constants import END
from langgraph.types import Command
from pydantic import BaseModel

from codemie.configs import logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.workflow_models import WorkflowConfig
from codemie.core.thought_queue import ThoughtQueue
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.templates.langgraph.workflow_prompts import result_summarizer_prompt
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.constants import MAX_TOKENS_LIMIT, NEXT_KEY, MESSAGES_VARIABLE
from codemie.workflows.memory_utils import _create_message_batches
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.base_node import BaseNode, StateSchemaType
from codemie.workflows.utils import get_messages_from_state_schema, should_summarize_memory, prepare_messages


class SummarizeConversationNodeConfigSchema(BaseModel):
    """Configuration schema for SummarizeConversationNode (no config parameters)."""

    pass


class SummarizeConversationNode(BaseNode[AgentMessages]):
    config_schema = SummarizeConversationNodeConfigSchema

    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        *args,
        **kwargs,
    ):
        super().__init__(callbacks, workflow_execution_service, thought_queue, *args, **kwargs)
        self.request_id = workflow_execution_service.workflow_execution_id

    def execute(self, state_schema: AgentMessages, execution_context: dict):
        messages = get_messages_from_state_schema(state_schema=state_schema)
        llm = get_llm_by_credentials(request_id=self.request_id)
        response = llm.invoke(messages + [HumanMessage(content=result_summarizer_prompt)])
        return response.content

    def get_task(self, state_schema: AgentMessages, *arg, **kwargs):
        return "Summarizing workflow conversation because it's too long"


class SummarizeConversationCommandNode(BaseNode[AgentMessages]):
    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        workflow_config: WorkflowConfig,
        *args,
        **kwargs,
    ):
        super().__init__(
            callbacks, workflow_execution_service, thought_queue, *args, workflow_config=workflow_config, **kwargs
        )
        self.request_id = workflow_execution_service.workflow_execution_id

    def execute(self, state_schema: AgentMessages, execution_context: dict):
        messages = get_messages_from_state_schema(state_schema=state_schema)
        summary = ""
        try:
            total_tokens, should_summarize = should_summarize_memory(self.workflow_config, messages)

            if not should_summarize:
                return None

            logger.info(f"Summarizing workflow conversation because it's too long, next: {state_schema.get(NEXT_KEY)}")
            # If total tokens exceed MAX_TOKENS_LIMIT, process in batches
            if total_tokens > MAX_TOKENS_LIMIT:
                messages_to_process = messages[1:]

                message_batches = _create_message_batches(messages=messages_to_process, max_tokens=MAX_TOKENS_LIMIT)

                # Process each batch and collect summaries
                batch_summaries = []
                llm = get_llm_by_credentials(request_id=self.request_id)
                for batch in message_batches:
                    response = llm.invoke(batch + [HumanMessage(content=result_summarizer_prompt)])
                    batch_summaries.append(str(response.content))

                # Combine all summaries
                summary = "\n\nCombined Summary:\n" + "\n".join(batch_summaries)
            else:
                llm = get_llm_by_credentials(request_id=self.request_id)
                response = llm.invoke(messages + [HumanMessage(content=result_summarizer_prompt)])
                summary = str(response.content)

            return summary
        except Exception as e:
            logger.error(f"Error in SummarizeConversationCommandNode: {e}")
            return summary

    def get_task(self, *arg, **kwargs):
        return "Summarizing workflow conversation because it's too long"

    def finalize_and_update_state(
        self, raw_output: Any, processed_output: str, success: bool, state_schema: Type[StateSchemaType]
    ) -> Command | None:
        # This node might have been called by mistake because of an issue,
        # so we should not update the state if the raw_output is None
        if not raw_output:
            return Command(goto=END)

        messages = get_messages_from_state_schema(state_schema=state_schema)
        updated_messages = [messages[0]]
        updated_messages.extend([RemoveMessage(id=m.id) for m in messages[1:]])
        next_nodes = state_schema.get(NEXT_KEY)
        next_node = next_nodes[-1]
        logger.debug(
            f"Summarizing workflow conversation redirects to next nodes: {next_node}, raw_output: {raw_output}"
        )
        update_value = {MESSAGES_VARIABLE: updated_messages + prepare_messages([str(raw_output)], True)}
        return Command(goto=next_node, update=update_value)
