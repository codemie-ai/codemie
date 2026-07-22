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

from langgraph.constants import END
from langgraph.graph import StateGraph

from codemie.workflows.workflow_generator.nodes.config_assembly import ConfigAssemblyNode
from codemie.workflows.workflow_generator.nodes.node_generator import NodeGeneratorNode
from codemie.workflows.workflow_generator.nodes.node_generator_router import NodeGeneratorRouterNode
from codemie.workflows.workflow_generator.nodes.node_regenerator import NodeRegeneratorNode
from codemie.workflows.workflow_generator.nodes.refine_planner import RefinePlannerNode
from codemie.workflows.workflow_generator.nodes.tools_selector import ToolsSelectorNode
from codemie.workflows.workflow_generator.nodes.validation import ValidationNode
from codemie.workflows.workflow_generator.state import WorkflowGeneratorState
from codemie.workflows.workflow_generator import state_keys as sk


def _route_from_router(state: WorkflowGeneratorState) -> str:
    if state.get(sk.ERROR):
        return END
    intent = state.get(sk.INTENT)
    if state.get(sk.CURRENT_NODE_INDEX, 0) < len(intent.steps if intent else []):
        return "generate"
    return "assemble"


def _route_after_validation(state: WorkflowGeneratorState) -> str:
    if state.get(sk.ERROR):
        return END
    if state.get(sk.VALIDATION_ERRORS):
        return "node_regenerator"
    return END


class WorkflowRefinerGraph:
    def __init__(self, llm_model: str, request_id: Optional[str] = None):
        self.llm_model = llm_model
        self.request_id = request_id
        self._refine_planner_node = RefinePlannerNode(llm_model, request_id)
        self._router_node = NodeGeneratorRouterNode()
        self._generator_node = NodeGeneratorNode(llm_model, request_id)
        self._regenerator_node = NodeRegeneratorNode(llm_model, request_id)
        self._config_assembly_node = ConfigAssemblyNode()
        self._tools_selector_node = ToolsSelectorNode(llm_model, request_id)
        self._validation_node = ValidationNode()
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(WorkflowGeneratorState)

        workflow.add_node("refine_planner", self._refine_planner_node)
        workflow.add_node("node_generator_router", self._router_node)
        workflow.add_node("node_generator", self._generator_node)
        workflow.add_node("node_regenerator", self._regenerator_node)
        workflow.add_node("config_assembly", self._config_assembly_node)
        workflow.add_node("tools_selector", self._tools_selector_node)
        workflow.add_node("validation", self._validation_node)

        workflow.set_entry_point("refine_planner")
        workflow.add_edge("refine_planner", "node_generator_router")
        workflow.add_conditional_edges(
            "node_generator_router",
            _route_from_router,
            {
                "generate": "node_generator",
                "assemble": "config_assembly",
                END: END,
            },
        )
        workflow.add_edge("node_generator", "tools_selector")
        workflow.add_edge("tools_selector", "node_generator_router")
        workflow.add_edge("config_assembly", "validation")
        workflow.add_conditional_edges(
            "validation",
            _route_after_validation,
            {
                "node_regenerator": "node_regenerator",
                END: END,
            },
        )
        workflow.add_edge("node_regenerator", "config_assembly")

        return workflow.compile()

    def run(self, initial_state: WorkflowGeneratorState) -> WorkflowGeneratorState:
        return self.graph.invoke(initial_state)
