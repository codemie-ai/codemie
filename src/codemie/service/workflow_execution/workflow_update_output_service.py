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

from codemie.workflows.checkpoint_saver import CheckpointSaver
from codemie.core.workflow_models import (
    WorkflowExecutionState,
    WorkflowExecutionStateThought,
    WorkflowExecution,
)
from codemie.service.workflow_service import WorkflowService
from codemie.configs import logger


class WorkflowUpdateOutputService:
    """Update the output of the last state of a workflow execution"""

    @classmethod
    def run(cls, execution_id: str, state_id: str, new_output: str):
        cls(execution_id, state_id, new_output).execute()

    def __init__(self, execution_id: str, state_id: str, new_output: str):
        self.execution_id = execution_id
        self.state_id = state_id
        self.new_output = new_output

    def execute(self):
        """Update the state, root thought and checkpoint output"""
        self._update_state()
        self._update_thought()
        CheckpointSaver().update_last_checkpoint(
            self.execution_id,
            output=self.new_output,
            output_key=self._get_output_key(),
        )

    def _update_state(self):
        """Update the state output"""
        state = WorkflowExecutionState.get_by_id(self.state_id)
        state.output = self.new_output
        state.save()

    def _update_thought(self):
        """Update the root thought; it should be single and match the state output"""
        thought = WorkflowExecutionStateThought.get_by_fields({"execution_state_id": self.state_id, "parent_id": None})

        if not thought:
            return

        thought.content = self.new_output
        thought.save()

    def _get_output_key(self) -> str | None:
        """Get the output_key from the workflow configuration for the current state.

        Returns:
            The output_key if configured for this state's transition, None otherwise
        """
        try:
            # Get the execution state to find the state name
            state: WorkflowExecutionState = WorkflowExecutionState.get_by_id(self.state_id)  # pyright: ignore
            if not state:
                logger.warning(f"Could not find state {self.state_id} for updating interrupted state output")
                return None

            # Get the workflow execution to find the workflow_id
            execution_results: list[WorkflowExecution] = WorkflowExecution.get_by_execution_id(self.execution_id)  # pyright: ignore
            if not execution_results:
                logger.warning(f"Could not find execution {self.execution_id} for updating interrupted state output")
                return None

            workflow_execution = execution_results[0]

            # Get the workflow configuration
            workflow_config = WorkflowService().get_workflow(workflow_execution.workflow_id)
            if not workflow_config or not workflow_config.states:
                logger.warning(
                    f"Could not find workflow config or states for workflow {workflow_execution.workflow_id} "
                    + "when updating interrupted state output"
                )
                return None

            # Find the state configuration by name
            for workflow_state in workflow_config.states:
                if workflow_state.id == state.name and workflow_state.next and workflow_state.next.output_key:
                    logger.debug(
                        f"Found output_key={workflow_state.next.output_key} " + f"for interrupted state {state.name}"
                    )
                    return workflow_state.next.output_key

            logger.debug(
                f"No output_key configured for interrupted state {state.name}, " + "context_store will not be updated"
            )
            return None

        except Exception as e:
            logger.error(f"Error retrieving output_key for interrupted state {self.state_id}: {e}")
            return None
