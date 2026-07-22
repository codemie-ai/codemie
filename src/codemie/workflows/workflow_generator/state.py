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

from __future__ import annotations

from typing import Any, Optional, TypedDict


class WorkflowGeneratorState(TypedDict):
    nl_query: str
    user: Any  # codemie.rest_api.security.user.User
    project: str
    available_tools: list  # list[dict] from ToolsInfoService.get_tools_info()
    intent: Optional[Any]  # WorkflowIntent | None
    step_plans: Optional[list]  # list[StepPlan] | None
    current_node_index: int  # index into intent.steps for sequential generation; 0 = not started
    previous_node: Optional[Any]  # MappedNode | None — last generated node, fed to next generator call
    node_plan: Optional[Any]  # NodeMappingPlan | None
    generated_config: Optional[Any]  # GeneratedWorkflowConfig | None
    validation_errors: list  # list[str]
    validation_attempts: int
    failed_step_ids: list  # list[str] — step IDs to regenerate; [] on first run or clean validation
    result: Optional[Any]  # CreateWorkflowRequest | None
    error: Optional[str]
    existing_yaml_config: Optional[str]  # set by the refine path; absent on the generate path
    refine_prompt: Optional[str]  # user's refinement instruction; absent on the generate path
