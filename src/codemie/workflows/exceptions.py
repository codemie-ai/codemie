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

"""Workflow-scoped exceptions for the sub-workflow node feature."""


class FeatureDisabledError(Exception):
    def __init__(self, message: str = "Feature is disabled"):
        self.message = message
        super().__init__(message)


class WorkflowNestingDepthExceededError(Exception):
    def __init__(self, current_depth: int, max_depth: int):
        self.current_depth = current_depth
        self.max_depth = max_depth
        super().__init__(f"Nesting depth {current_depth} exceeds maximum allowed depth {max_depth}")


class IteratorInputNotFoundError(Exception):
    def __init__(self, iter_key: str, state_id: str, detail: str = ""):
        self.iter_key = iter_key
        self.state_id = state_id
        super().__init__(
            f"Iterator input list '{iter_key}' was not found in the input of state '{state_id}'. "
            f"Check iter_key and the previous step output. {detail}"
        )


class SubWorkflowExecutionError(Exception):
    def __init__(self, execution_id: str, message: str = "Sub-workflow execution failed"):
        self.execution_id = execution_id
        self.message = message
        super().__init__(f"Sub-workflow {execution_id}: {message}")
