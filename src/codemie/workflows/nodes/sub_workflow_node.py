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

import json
from typing import Optional

from codemie.configs import config
from codemie.configs.customer_config import customer_config
from codemie.service.workflow_pool import workflow_pool
from codemie.workflows.exceptions import (
    FeatureDisabledError,
    SubWorkflowExecutionError,
    WorkflowNestingDepthExceededError,
)
from codemie.core.thought_queue import ThoughtQueue
from codemie.core.workflow_models import WorkflowConfig, WorkflowExecutionStatusEnum, WorkflowState
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.service.workflow_service import WorkflowService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.constants import CONTEXT_STORE_VARIABLE, PREVIOUS_EXECUTION_STATE_ID, USER_INPUT
from codemie.workflows.nodes.base_node import BaseNode, ExecutionAbortedException


class SubWorkflowNode(BaseNode):
    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        workflow_state: WorkflowState,
        node_name: Optional[str] = "",
        execution_id: Optional[str] = None,
        workflow_config: Optional[WorkflowConfig] = None,
        *args,
        **kwargs,
    ):
        super().__init__(
            callbacks,
            workflow_execution_service,
            thought_queue,
            node_name,
            execution_id,
            workflow_state,
            workflow_config,
            *args,
            **kwargs,
        )
        self.sub_workflow_id: str = workflow_state.workflow_id

    def get_task(self, state_schema, *args, **kwargs) -> str:
        return f"Executing sub-workflow {self.sub_workflow_id}"

    def execute(self, state_schema, execution_context: dict):
        if not customer_config.is_feature_enabled("subWorkflow"):
            raise FeatureDisabledError(
                "Sub-workflow node is disabled. Enable the 'features:subWorkflow' flag in customer-config."
            )

        user = self.workflow_execution_service.user
        parent_exec = WorkflowService.find_workflow_execution_by_id(self.execution_id)

        if parent_exec and parent_exec.active_sub_execution_id:
            child_execution = self._resume_child(parent_exec, state_schema, user)
        else:
            child_execution = self._create_child(parent_exec, state_schema, user)

        return self._handle_child_outcome(child_execution)

    def _resume_child(self, parent_exec, state_schema, user):
        from codemie.workflows.workflow import WorkflowExecutor

        child_execution = WorkflowService.find_workflow_execution_by_id(parent_exec.active_sub_execution_id)
        if not child_execution:
            raise ValueError(f"Sub-workflow execution {parent_exec.active_sub_execution_id} not found on resume")
        child_config = WorkflowService().get_workflow(child_execution.workflow_id, user)
        if not child_config:
            raise ValueError(f"Sub-workflow {child_execution.workflow_id} not found on resume")

        resume_input = json.dumps(state_schema.get(CONTEXT_STORE_VARIABLE, {}))
        child_executor = WorkflowExecutor.create_executor(
            child_config,
            resume_input,
            user,
            execution_id=child_execution.execution_id,
            thought_queue=ThoughtQueue(),
            resume_execution=True,
        )
        child_executor.stream()
        return child_execution

    def _create_child(self, parent_exec, state_schema, user):
        from codemie.workflows.workflow import WorkflowExecutor

        child_config = WorkflowService().get_workflow(self.sub_workflow_id, user)
        if not child_config:
            raise ValueError(f"Sub-workflow {self.sub_workflow_id} not found")

        effective_max_depth = child_config.max_nesting_level or config.SUBWORKFLOW_MAX_NESTING_DEPTH
        current_depth = WorkflowService.get_nesting_depth(self.execution_id)
        if current_depth >= effective_max_depth:
            raise WorkflowNestingDepthExceededError(current_depth, effective_max_depth)

        child_input = self._render_input(state_schema)
        child_execution = WorkflowService.create_workflow_execution(
            child_config,
            user.as_user_model(),
            child_input,
            parent_execution_id=self.execution_id,
            attributes=parent_exec.attributes if parent_exec else None,
        )

        graph = workflow_pool.acquire(self.sub_workflow_id, child_config)
        try:
            child_executor = WorkflowExecutor.create_executor(
                child_config,
                child_input,
                user,
                execution_id=child_execution.execution_id,
                thought_queue=ThoughtQueue(),
                compiled_graph=graph,
            )
            if parent_exec:
                parent_exec.active_sub_execution_id = child_execution.execution_id
                parent_exec.save()
            child_executor.stream()
        finally:
            workflow_pool.release(self.sub_workflow_id, child_config, graph)

        return child_execution

    def _handle_child_outcome(self, child_execution):
        from codemie.core.exceptions import InterruptedException

        finished_child = WorkflowService.find_workflow_execution_by_id(child_execution.execution_id)
        if not finished_child:
            raise SubWorkflowExecutionError(child_execution.execution_id)

        if finished_child.overall_status == WorkflowExecutionStatusEnum.INTERRUPTED:
            # Do NOT clear active_sub_execution_id — parent needs it when it is resumed.
            raise InterruptedException(
                message="Sub-workflow was interrupted",
                interrupted_state=self.node_name,
            )

        # Terminal: clear the reference on the parent row.
        parent_exec = WorkflowService.find_workflow_execution_by_id(self.execution_id)
        if parent_exec:
            parent_exec.active_sub_execution_id = None
            parent_exec.save()

        if finished_child.overall_status == WorkflowExecutionStatusEnum.ABORTED:
            raise ExecutionAbortedException("Sub-workflow was aborted")

        if finished_child.overall_status == WorkflowExecutionStatusEnum.FAILED:
            raise SubWorkflowExecutionError(child_execution.execution_id)

        return WorkflowService.find_last_execution_state_output(child_execution.execution_id) or ""

    def post_process_output(self, state_schema, task, output) -> str:
        return str(output)

    def _render_input(self, state_schema) -> str:
        prev_state_id = state_schema.get(PREVIOUS_EXECUTION_STATE_ID)
        if not prev_state_id:
            # First node after START — no preceding DB execution state exists.
            return state_schema.get(USER_INPUT) or ""

        # Preceding real node exists — pass its output as the child's input.
        prev_output = WorkflowService.find_execution_state_output(prev_state_id)
        return prev_output or state_schema.get(USER_INPUT) or ""
