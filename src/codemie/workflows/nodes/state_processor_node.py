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

from typing import Any, List, Optional, Type
from codemie.configs import logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.thought_queue import ThoughtQueue
from codemie.core.utils import extract_text_from_llm_output
from codemie.core.workflow_models import (
    WorkflowState,
    CustomWorkflowNode,
    WorkflowExecutionStatusEnum,
    WorkflowExecutionStateWithThougths,
)
from codemie.service.workflow_execution import WorkflowExecutionService, WorkflowExecutionStatesIndexService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.jinja_template_renderer import TemplateRenderer
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.base_node import BaseNode, StateSchemaType
from codemie.workflows.utils import extract_json_content
from langchain_core.messages import HumanMessage, AIMessage
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, retry_if_exception_type, wait_exponential


class StateProcessorNodeConfigSchema(BaseModel):
    """Configuration schema for StateProcessorNode."""

    output_template: str = Field(
        ...,
        json_schema_extra={
            "type": "text",
            "required": True,
            "description": "Jinja template for output processing",
        },
    )
    workflow_execution_id: Optional[str] = Field(
        None,
        json_schema_extra={
            "type": "str",
            "required": False,
            "description": "Workflow execution ID (optional)",
        },
    )
    states_status_filter: Optional[List[str]] = Field(
        None,
        json_schema_extra={
            "type": "list",
            "required": False,
            "description": "Filter states by status (default: [NOT_STARTED, IN_PROGRESS, SUCCEEDED])",
            "values": ["NOT_STARTED", "IN_PROGRESS", "SUCCEEDED", "FAILED"],
        },
    )
    state_id: Optional[str] = Field(
        None,
        json_schema_extra={
            "type": "str",
            "required": False,
            "description": "Specific state ID to process (optional)",
        },
    )


class StateProcessorNode(BaseNode[AgentMessages]):
    """
    StateProcessorNode class processes states in a workflow using a language model (LLM) to generate summaries or
    other outputs based on the processed states.

    The class uses Jinja for template rendering to format the output based on the provided template.

    Example Configuration:
    - id: summary_of_summary
      custom_node_id: state_processor_node
      model: gpt-4.1-mini
      config:
        state_id: branch_comparator
        output_template: |
          # Summary
          {% for item in items %}
          ## Filename: {{ item.file_path }}
          {{ item.conclusion }}
          {% endfor %}

    Configuration Details:
    - state_id (optional): Used to filter output from a specific branch.
    If specified, only states with the given ID will be used.
    - output_template: A Jinja template string for rendering the output.
    """

    config_schema = StateProcessorNodeConfigSchema

    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        workflow_state: WorkflowState,
        *args,
        **kwargs,
    ):
        super().__init__(callbacks, workflow_execution_service, thought_queue, *args, **kwargs)
        self.workflow_state: WorkflowState = workflow_state
        self.request_id = workflow_execution_service.workflow_execution_id

    def execute(self, state_schema: AgentMessages, execution_context: dict) -> Any:
        custom_node: CustomWorkflowNode = execution_context.get("custom_node")
        output_template: str = custom_node.config.get('output_template')
        workflow_execution_id: Optional[str] = custom_node.config.get('workflow_execution_id', None)
        states_status_filter: Optional[List[str]] = custom_node.config.get(
            'states_status_filter',
            [
                WorkflowExecutionStatusEnum.NOT_STARTED.value,
                WorkflowExecutionStatusEnum.IN_PROGRESS.value,
                WorkflowExecutionStatusEnum.SUCCEEDED.value,
            ],
        )

        states = self._fetch_states(
            workflow_execution_id or self.execution_id, custom_node.config.get('state_id'), states_status_filter
        )
        llm = get_llm_by_credentials(request_id=self.request_id, llm_model=custom_node.model)
        processed_batches = []
        for state in states:
            if state.output:
                response = llm.invoke(
                    [AIMessage(content=[{'type': 'text', 'text': f"TASK: {state.task}. OUTPUT: {state.output}"}])]
                    + [HumanMessage(content=self.get_task(state_schema, self.args, self.kwargs))]
                )
                response_extracted = extract_json_content(extract_text_from_llm_output(response.content))
                if response_extracted:
                    processed_batches.append(response_extracted)
                    logger.debug(f"Response extracted: {response_extracted}")

        return TemplateRenderer.render_template_batch(template_str=output_template, json_str_list=processed_batches)

    def post_process_output(self, state_schema: Type[StateSchemaType], task, output) -> str:
        return output

    def get_task(self, state_schema: AgentMessages, *arg, **kwargs):
        return self.workflow_state.task

    @retry(
        stop=stop_after_attempt(5), wait=wait_exponential(1), retry=retry_if_exception_type(ValueError), reraise=True
    )
    def _fetch_states(
        self, execution_id: str, state_name: str, states_status_filter: list[str] | None
    ) -> List[WorkflowExecutionStateWithThougths]:
        states_data = WorkflowExecutionStatesIndexService.run(
            execution_id=execution_id,
            per_page=10000,
            include_thoughts=False,
            state_name_prefix=state_name,
            states_status_filter=states_status_filter,
        )
        states = states_data.get("data", [])
        logger.debug(f"States count retrieved: {len(states)}")
        not_completed_statuses = (WorkflowExecutionStatusEnum.NOT_STARTED, WorkflowExecutionStatusEnum.IN_PROGRESS)
        if not states or any(state.status in not_completed_statuses for state in states):
            raise ValueError("No Completed States, retrying...")
        return states
