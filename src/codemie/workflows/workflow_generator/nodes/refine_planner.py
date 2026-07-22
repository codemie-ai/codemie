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

from typing import Optional

from codemie.core.dependecies import get_llm_by_credentials
from codemie.templates.agents.workflow_generator import REFINE_PLANNING_PROMPT
from codemie.workflows.workflow_generator.schemas import RefinePlan
from codemie.workflows.workflow_generator.state import WorkflowGeneratorState
from codemie.workflows.workflow_generator import state_keys as sk


class RefinePlannerNode:
    def __init__(self, llm_model: str, request_id: Optional[str] = None):
        self.llm_model = llm_model
        self.request_id = request_id

    def _format_tools(self, available_tools: list) -> str:
        lines: list[str] = []
        for toolkit in available_tools:
            for tool in toolkit.get("tools", []):
                name = tool.get("name", "")
                desc = tool.get("description", "")
                if name:
                    lines.append(f"  {name}: {desc}")
        return "\n".join(lines) if lines else "  (none)"

    def __call__(self, state: WorkflowGeneratorState) -> dict:
        existing_yaml: str = state[sk.EXISTING_YAML_CONFIG]
        refine_prompt: str = state.get(sk.REFINE_PROMPT) or ""
        available_tools: list = state.get(sk.AVAILABLE_TOOLS) or []

        prompt = REFINE_PLANNING_PROMPT.format(
            existing_yaml_config=existing_yaml,
            refine_prompt=refine_prompt,
            available_tools=self._format_tools(available_tools),
        )

        llm = get_llm_by_credentials(
            llm_model=self.llm_model,
            temperature=0.0,
            streaming=False,
            request_id=self.request_id,
        )
        plan: RefinePlan = llm.with_structured_output(RefinePlan).invoke(prompt)

        return {
            sk.INTENT: plan.intent,
            sk.STEP_PLANS: plan.plans,
            sk.CURRENT_NODE_INDEX: 0,
            sk.PREVIOUS_NODE: None,
        }
