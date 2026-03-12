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

import json
from base64 import b64encode, b64decode
from typing import Optional, Iterable

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    CheckpointTuple,
    Checkpoint,
    CheckpointMetadata,
)
from langchain_core.runnables import RunnableConfig
from codemie.configs import logger

from codemie.core.workflow_models import WorkflowExecution, WorkflowExecutionCheckpoint
from codemie.workflows.constants import CONTEXT_STORE_VARIABLE


class CheckpointSaver(BaseCheckpointSaver):
    """
    Implementation of the BaseCheckpointSaver that uses Postgres as the storage backend.
    Relies on WorkflowExecution > WorkflowExecutionCheckpoint data model for storing the checkpoints.
    """

    def _serialize(self, obj) -> str:
        """Serialize object using serde.dumps_typed and encode for string storage."""
        type_str, data_bytes = self.serde.dumps_typed(obj)
        return json.dumps({"type": type_str, "data": b64encode(data_bytes).decode('utf-8')})

    def _deserialize(self, data_str: str):
        """Deserialize string back to object using serde.loads_typed."""
        data_dict = json.loads(data_str)
        type_str = data_dict["type"]
        data_bytes = b64decode(data_dict["data"])
        return self.serde.loads_typed((type_str, data_bytes))

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple] | None:
        """Returns the CheckpointTuple for the given config."""
        execution_id = config["configurable"]["thread_id"]
        timestamp = config["configurable"].get("thread_ts", None)

        workflow_execution = self._find_workflow_execution(execution_id)

        if not workflow_execution:
            return None

        checkpoints = self._find_checkpoints(workflow_execution, timestamp)

        if len(checkpoints):
            checkpoint = checkpoints[-1]
            return CheckpointTuple(
                config=config,
                checkpoint=self._deserialize(checkpoint.data),
                metadata=self._deserialize(checkpoint.metadata),
            )

        return None

    def list(self, config: RunnableConfig) -> Iterable[CheckpointTuple]:
        execution_id = config["configurable"]["thread_id"]
        timestamp = config["configurable"].get("thread_ts", None)

        workflow_execution = self._find_workflow_execution(execution_id)

        if not workflow_execution:
            return

        checkpoints = self._find_checkpoints(workflow_execution, timestamp)

        for checkpoint in checkpoints:
            yield CheckpointTuple(
                config=config,
                checkpoint=self._deserialize(checkpoint.data),
                metadata=self._deserialize(checkpoint.metadata),
            )

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, *args):
        execution_config = self._find_workflow_execution(config["configurable"]["thread_id"])
        execution_config.checkpoints.append(
            WorkflowExecutionCheckpoint(
                timestamp=checkpoint['ts'],
                data=self._serialize(checkpoint),
                metadata=self._serialize(metadata),
            )
        )
        execution_config.update(refresh=True)

        return {"configurable": {"thread_id": config["configurable"]["thread_id"], "thread_ts": checkpoint['ts']}}

    def put_writes(self, *args):
        """Placeholder, required for the interface."""
        pass

    def update_last_checkpoint(self, execution_id: str, output: str, output_key: str | None = None):
        """Custom method, allows to update last checkpoint output.

        Updates both the message content AND the context store within the checkpoint.
        This ensures that when the workflow resumes, both the conversation history
        and any output_key variables (e.g., {{prfaq}}) are properly updated.

        Args:
            execution_id: The workflow execution ID
            output: The new output value
            output_key: Optional key to update in CONTEXT_STORE_VARIABLE (from WorkflowState.next.output_key)
        """

        workflow_execution = self._find_workflow_execution(execution_id)

        if not workflow_execution:
            raise ValueError(f"Workflow execution {execution_id} not found")

        checkpoints = self._find_checkpoints(workflow_execution)

        if not checkpoints:
            raise ValueError(f"No checkpoints found for execution {execution_id}")

        checkpoint = checkpoints.pop()
        data = self._deserialize(checkpoint.data)

        content = data["channel_values"]["messages"][-1].content[0]
        if isinstance(content, str):
            data["channel_values"]["messages"][-1].content = output
        elif isinstance(content, dict) and content.get("text"):
            content["text"] = output
        else:
            raise ValueError("Unknown checkpoint format")

        # Also update the CONTEXT_STORE_VARIABLE if output_key is provided
        # This ensures {{variable}} resolution works after resuming from interruption
        if output_key:
            if CONTEXT_STORE_VARIABLE in data["channel_values"]:
                old_value = data["channel_values"][CONTEXT_STORE_VARIABLE].get(output_key, "None")
                logger.debug(f"Updating context_store[{output_key}]: '{old_value}' -> '{output}'")
                data["channel_values"][CONTEXT_STORE_VARIABLE][output_key] = output
            else:
                logger.warning(
                    f"output_key={output_key} provided but {CONTEXT_STORE_VARIABLE} not found "
                    + f"in '{execution_id}' workflow execution"
                )

        checkpoint.data = self._serialize(data)

        workflow_execution.checkpoints.append(checkpoint)
        workflow_execution.update(refresh=True)

    def _find_workflow_execution(self, execution_id: str) -> Optional[WorkflowExecution]:
        result = WorkflowExecution.get_by_execution_id(execution_id)

        if not len(result):
            return None

        return result[0]

    def _find_checkpoints(self, workflow_execution: WorkflowExecution, timestamp: Optional[str] = None):
        if not timestamp:
            return workflow_execution.checkpoints

        return [checkpoint for checkpoint in workflow_execution.checkpoints if checkpoint.timestamp <= timestamp]
