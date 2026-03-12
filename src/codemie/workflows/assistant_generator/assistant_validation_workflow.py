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

"""AI-Based Marketplace Assistant Validation Workflow.

This workflow validates assistants before marketplace publishing using LangGraph.
It performs parallel validation: metadata, system prompt, tools (via RAG), and context all run simultaneously.
"""

from typing import Literal

from langgraph.graph import END, StateGraph

from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.assistant_generator import RefineGeneratorResponse
from codemie.rest_api.security.user import User
from codemie.templates.agents.assistant_validation_prompts import (
    METADATA_VALIDATION_TEMPLATE,
    SYSTEM_PROMPT_VALIDATION_TEMPLATE,
)
from codemie.workflows.assistant_generator.models.validation_models import (
    MetadataValidationResult,
    ValidationResult,
)
from codemie.workflows.assistant_generator.models.validation_state import AssistantValidationState
from codemie.workflows.assistant_generator.nodes.validation.clarification_analysis_node import (
    ClarificationAnalysisNode,
)
from codemie.workflows.assistant_generator.nodes.validation.generic_validation_node import GenericValidationNode
from codemie.workflows.assistant_generator.nodes.validation.make_decision_node import MakeDecisionNode
from codemie.workflows.assistant_generator.nodes.validation.utils import (
    format_metadata_validation_prompt,
    format_system_prompt_validation_prompt,
)
from codemie.workflows.assistant_generator.nodes.validation.validate_context_node import ValidateContextNode
from codemie.workflows.assistant_generator.nodes.validation.validate_tools_node import ValidateToolsNode


def _fan_out_to_all_validations(state: AssistantValidationState) -> list[str]:
    """Fan-out router that triggers all validations in parallel.

    Args:
        state: Current workflow state

    Returns:
        List of all validation node names to execute in parallel
    """
    return ["validate_metadata", "validate_system_prompt", "validate_tools", "validate_context"]


class AssistantValidationWorkflow:
    """LangGraph workflow for AI-based assistant validation with parallel execution.

    Workflow structure (with CLARIFICATION + PARALLEL execution):
    1. Clarification analysis node → Analyzes spec, generates & answers questions
    2. Start node → Fan-out to all validations (run in parallel):
       - Metadata validation
       - System Prompt validation
       - Tools validation (RAG + LLM) - ENHANCED with clarifications
       - Context validation - ENHANCED with clarifications
    3. Decision node: Aggregate all validation results → ACCEPT or REJECT

    Performance: ~8-10 seconds added for clarification, but significantly improves validation accuracy.
    """

    def __init__(self, llm_model: str, request_id: str | None = None):
        """Initialize workflow with clarification analysis and parallel execution support.

        Args:
            llm_model: LLM model to use for validation
            request_id: Request ID for logging/tracing
        """
        self.llm_model = llm_model
        self.request_id = request_id

        # Initialize clarification analysis node (NEW)
        self.clarification_analysis_node = ClarificationAnalysisNode(llm_model, request_id)

        # Initialize validation nodes
        self.validate_metadata_node = GenericValidationNode(
            llm_model=llm_model,
            request_id=request_id,
            phase_name="metadata",
            result_field="metadata_result",
            next_phase="decision",  # Go directly to decision after completion
            output_model=MetadataValidationResult,
            prompt_template=METADATA_VALIDATION_TEMPLATE.template,
            prompt_formatter=format_metadata_validation_prompt,
        )

        self.validate_system_prompt_node = GenericValidationNode(
            llm_model=llm_model,
            request_id=request_id,
            phase_name="system_prompt",
            result_field="system_prompt_result",
            next_phase="decision",  # Go directly to decision after completion
            output_model=ValidationResult,
            prompt_template=SYSTEM_PROMPT_VALIDATION_TEMPLATE.template,
            prompt_formatter=format_system_prompt_validation_prompt,
        )

        self.validate_tools_node = ValidateToolsNode(llm_model, request_id)
        self.validate_context_node = ValidateContextNode(llm_model, request_id)
        self.make_decision_node = MakeDecisionNode(llm_model, request_id)

        # Build graph with parallel execution
        self.graph = self._build_graph()

    def _build_graph(self):
        """Build LangGraph workflow with CLARIFICATION + PARALLEL validation execution.

        Uses clarification-enhanced pattern:
        Clarification → Start → [All Validations in Parallel] → Decision → END

        Clarification node analyzes specification first, then all validation nodes
        (metadata, system_prompt, tools, context) run in parallel with clarification context,
        then converge at the decision node for final aggregation.

        The decision node uses defer=True to ensure it waits for ALL parallel validation
        branches to complete before executing. Without defer=True, the decision node would
        execute multiple times (once per incoming edge from each validation node).

        Returns:
            Compiled StateGraph
        """
        # Use Pydantic model as state schema for LangGraph
        workflow = StateGraph(AssistantValidationState)

        # Add clarification analysis node (NEW - runs first)
        workflow.add_node("clarification_analysis", self.clarification_analysis_node)

        # Add start node for fan-out (pass-through node)
        workflow.add_node("start", lambda state: state)

        # Add all validation nodes
        workflow.add_node("validate_metadata", self.validate_metadata_node)
        workflow.add_node("validate_system_prompt", self.validate_system_prompt_node)
        workflow.add_node("validate_tools", self.validate_tools_node)
        workflow.add_node("validate_context", self.validate_context_node)

        # Add decision node with defer=True to synchronize parallel branches
        # defer=True ensures decision waits for all 4 validation nodes to complete
        workflow.add_node("decision", self.make_decision_node, defer=True)

        # Set entry point to clarification analysis (NEW)
        workflow.set_entry_point("clarification_analysis")

        # Clarification flows to start node (NEW)
        workflow.add_edge("clarification_analysis", "start")

        # Fan-out: Start node triggers all validations in parallel
        workflow.add_conditional_edges(
            "start",
            _fan_out_to_all_validations,
        )

        # All validation nodes converge to decision node
        # These edges ensure decision waits for all validations to complete
        workflow.add_edge("validate_metadata", "decision")
        workflow.add_edge("validate_system_prompt", "decision")
        workflow.add_edge("validate_tools", "decision")
        workflow.add_edge("validate_context", "decision")

        # Decision node completes the workflow
        workflow.add_edge("decision", END)

        return workflow.compile()

    def validate(
        self,
        assistant: Assistant,
        user: User,
        request_id: str | None = None,
    ) -> tuple[Literal["accept", "reject"], RefineGeneratorResponse]:
        """Execute validation workflow with clarification analysis and parallel execution.

        Workflow steps:
        1. Clarification analysis: Generate and answer questions about specification
        2. Parallel validation: All phases (metadata, system_prompt, tools, context) run in parallel
           with clarification context
        3. Decision: Aggregate all validation results

        Args:
            assistant: Assistant to validate
            user: User attempting to publish
            request_id: Request ID for logging

        Returns:
            Tuple of (decision, recommendations)
            - decision: "accept" or "reject"
            - recommendations: RefineGeneratorResponse with improvement suggestions
        """
        # Initialize state using TypedDict
        initial_state: AssistantValidationState = {
            "assistant": assistant,
            "has_sub_assistants": len(assistant.assistant_ids) > 0 if assistant.assistant_ids else False,
            "user": user,
            "request_id": request_id or self.request_id or "unknown",
            "clarifications": None,
            "metadata_result": None,
            "system_prompt_result": None,
            "tools_result": None,
            "context_result": None,
            "context_info": None,
            "decision": None,
            "recommendations": None,
        }

        # Execute workflow (LangGraph executes fan-out nodes in parallel automatically)
        final_state = self.graph.invoke(initial_state)

        # Extract results from TypedDict
        decision = final_state.get("decision") or "reject"
        recommendations = final_state.get("recommendations") or RefineGeneratorResponse(
            fields=[],
            toolkits=[],
            context=[],
        )

        return decision, recommendations
