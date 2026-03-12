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

from typing import Any, Type, Optional

from deprecated import deprecated
from langchain_core.prompts import SystemMessagePromptTemplate, MessagesPlaceholder, ChatPromptTemplate
from langgraph.types import Command
from pydantic import BaseModel

from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.utils import calculate_tokens
from codemie.core.thought_queue import ThoughtQueue
from codemie.configs import logger
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.templates.langgraph.workflow_prompts import (
    supervisor_prompt,
    supervisor_suffix_prompt,
    supervisor_prompt_prefix,
)
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.constants import SUPERVISOR_FINISH_STEP, MESSAGES_VARIABLE, NEXT_KEY
from codemie.workflows.models import SupervisorAgentMessages, NextAction
from codemie.workflows.nodes.base_node import BaseNode
from codemie.service.llm_service.llm_service import LLMService
from codemie.workflows.utils import get_messages_from_state_schema
from codemie.workflows.constants import (
    MESSAGES_LIMIT,
    MESSAGES_TOKENS_LIMIT,
    SUMMARIZE_MEMORY_NODE,
    RESULT_FINALIZER_NODE,
    END_NODE,
)


class SupervisorNodeConfigSchema(BaseModel):
    """Configuration schema for SupervisorNode (no config parameters)."""

    pass


@deprecated
class SupervisorNode(BaseNode[SupervisorAgentMessages]):
    config_schema = SupervisorNodeConfigSchema

    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        messages_limit_before_summarization: Optional[int] = None,
        tokens_limit_before_summarization: Optional[int] = None,
        enable_summarization_node: bool = True,
        *args,
        **kwargs,
    ):
        super().__init__(callbacks, workflow_execution_service, thought_queue, *args, **kwargs)
        self.members = kwargs.get('members')
        self.team_details = kwargs.get('team_details')
        self.supervisor_prompt = kwargs.get('supervisor_prompt')

        self.messages_limit_before_summarization = messages_limit_before_summarization
        self.tokens_limit_before_summarization = tokens_limit_before_summarization
        self.enable_summarization_node = enable_summarization_node

    def execute(self, state_schema: SupervisorAgentMessages, execution_context: dict) -> Any:
        options = [SUPERVISOR_FINISH_STEP] + self.members
        messages = get_messages_from_state_schema(state_schema=state_schema)
        supervisor_system_prompt = self.supervisor_prompt if self.supervisor_prompt else supervisor_prompt
        prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessagePromptTemplate.from_template(supervisor_prompt_prefix + supervisor_system_prompt),
                MessagesPlaceholder(variable_name=MESSAGES_VARIABLE),
                SystemMessagePromptTemplate.from_template(supervisor_suffix_prompt),
            ]
        ).partial(options=str(options), members=", ".join(self.members), team=self.team_details)
        llm = get_llm_by_credentials(llm_model=LLMService.BASE_NAME_GPT_41, request_id=self.execution_id)
        logger.info(f"SupervisorNode: using {llm.model_name}")
        supervisor_chain = prompt | llm.with_structured_output(NextAction)
        return supervisor_chain.invoke({MESSAGES_VARIABLE: messages})

    def finalize_and_update_state(
        self, raw_output: Any, processed_output: str, success: bool, state_schema: Type[SupervisorAgentMessages]
    ):
        result = super().finalize_and_update_state(raw_output, processed_output, success, state_schema)
        result[NEXT_KEY] = [raw_output.next]
        goto = self.evaluate_route(result)
        return Command(
            goto=goto,
            update=result,
        )

    def evaluate_route(self, state_schema: SupervisorAgentMessages) -> str:
        messages = get_messages_from_state_schema(state_schema=state_schema)
        next_candidate = state_schema.get(NEXT_KEY, "")[-1]

        if next_candidate == SUPERVISOR_FINISH_STEP:
            if self.enable_summarization_node:
                return RESULT_FINALIZER_NODE
            else:
                return END_NODE

        next_state = next_candidate

        logger.debug(f"Evaluate_route. Candidate={next_candidate}. NextState={next_state}")

        # Condition to check if we need to summarize the conversation memory
        messages_limit = (
            self.messages_limit_before_summarization if self.messages_limit_before_summarization else MESSAGES_LIMIT
        )
        tokens_limit = (
            self.tokens_limit_before_summarization if self.tokens_limit_before_summarization else MESSAGES_TOKENS_LIMIT
        )
        if len(messages) > messages_limit or calculate_tokens(str(messages)) > tokens_limit:
            return SUMMARIZE_MEMORY_NODE

        return next_state

    def post_process_output(self, state_schema: SupervisorAgentMessages, task, output) -> str:
        return f"Supervisor decision: - Next action: {output.next}\nTask: {output.task}\nReasoning: {output.reasoning}"

    def get_task(self, state_schema: SupervisorAgentMessages, *arg, **kwargs):
        return "Decide about next step"
