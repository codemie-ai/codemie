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

from typing import List

from codemie.core.workflow_models import WorkflowExecution, WorkflowExecutionState, WorkflowExecutionStateThought


class WorkflowThoughtsListService:
    """List Workflow Execution Thoughts either all or by parent ids"""

    @classmethod
    def run(cls, execution: WorkflowExecution, parent_ids=None) -> List[WorkflowExecutionStateThought]:
        if parent_ids is None:
            parent_ids = []
        if parent_ids:
            root_thoughts = WorkflowExecutionStateThought.get_all(ids=parent_ids)
        else:
            states = WorkflowExecutionState.get_all_by_fields({"execution_id.keyword": execution.execution_id})
            state_ids = [state.id for state in states]
            root_thoughts = WorkflowExecutionStateThought.get_root(state_ids, include_children_field=True)

        return cls.include_children(thoughts=root_thoughts)

    @staticmethod
    def include_children(thoughts: List[WorkflowExecutionStateThought]):
        """Recursively find and fill child thoughts"""
        for thought in thoughts:
            base_child_thoughts = WorkflowExecutionStateThought.get_all_by_parent_ids(parent_ids=[thought.id])
            child_thoughts = WorkflowThoughtsListService.include_children(thoughts=base_child_thoughts)
            thought.children = child_thoughts

        return thoughts
