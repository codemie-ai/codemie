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

"""Tools validation node using RAG and LLM decision-making."""

from codemie.configs.logger import logger
from codemie.rest_api.models.assistant import Assistant
from codemie.service.tools.toolkit_lookup_service import ToolkitLookupService
from codemie.templates.agents.assistant_validation_prompts import TOOLS_DECISION_TEMPLATE
from codemie.workflows.assistant_generator.models.validation_models import (
    ToolsDecisionResult,
    ToolsValidationResult,
)
from codemie.workflows.assistant_generator.models.validation_state import AssistantValidationState
from codemie.workflows.assistant_generator.nodes.validation.base_validation_node import BaseValidationNode
from codemie.workflows.assistant_generator.nodes.validation.utils import (
    format_configured_context_for_prompt,
    format_existing_tools_for_prompt,
    format_rag_toolkits_for_prompt,
    get_assistant_tool_names,
)
from codemie_tools.base.models import ToolKit


tool_validation_orchestrator_result = ToolsValidationResult(
    is_valid=True,
    rag_query="N/A - Orchestrator assistant detected, skipping tools validation.",
    reasoning=(
        "This is an orchestrator assistant that delegates tasks to sub-assistants. "
        "Orchestrator assistants do not need their own tools because they route requests to specialized sub-assistants, "
        "each of which has their own tool configurations. Tools validation is skipped as it's not applicable to orchestrators - "
        "the sub-assistants handle all tool-based capabilities."
    ),
)


class ValidateToolsNode(BaseValidationNode):
    """Use RAG and LLM decision-making to find and validate relevant tools"""

    def __call__(self, state: AssistantValidationState) -> dict:
        """Execute tools validation using RAG + LLM decision.

        Args:
            state: Current workflow state

        Returns:
            Dictionary with only the fields updated by this node
        """
        # Orchestrator assistants (with sub-assistants) bypass tool validation
        # IMPORTANT: Tool recommendations are NOT supported for orchestrators
        # We assume orchestrator assistants are correctly configured - sub-assistants handle all tool-based capabilities
        # Validation always returns is_valid=True without performing RAG lookup or LLM-based tool analysis
        if state.get("has_sub_assistants"):
            return {
                "tools_result": tool_validation_orchestrator_result,
            }

        assistant = state["assistant"]

        clarification_tool_summary = ""
        if state.get("clarifications") and state["clarifications"]:
            clarification_tool_summary = state["clarifications"].clarification_summary_markdown
        else:
            clarification_tool_summary = (
                "## Clarification Analysis\n\n"
                "No clarification analysis available. Generate RAG query based on assistant specification only.\n\n"
            )

        query = clarification_tool_summary

        rag_limit = 10

        recommended_toolkits = ToolkitLookupService.get_tools_by_query(query=query, limit=rag_limit)

        decision_result: ToolsDecisionResult = self._make_tools_decision_with_llm(
            assistant, recommended_toolkits, state
        )

        current_tools = get_assistant_tool_names(assistant)
        tools_to_add = set(decision_result.tools_to_include) - current_tools
        tools_to_keep = set(decision_result.tools_to_include) & current_tools
        tools_to_delete = current_tools - set(decision_result.tools_to_include)

        result = ToolsValidationResult(
            is_valid=len(tools_to_add) == 0 and len(tools_to_delete) == 0,
            recommended_toolkits=recommended_toolkits,
            recommended_additions=list(tools_to_add),
            recommended_deletions=list(tools_to_delete),
            tools_to_keep=list(tools_to_keep),
            rag_query=query,
            reasoning=decision_result.reasoning,
        )

        logger.info(
            f"Tools validation completed: is_valid={result.is_valid}, "
            f"to_add={list(tools_to_add)}, to_delete={list(tools_to_delete)}"
        )

        return {
            "tools_result": result,
        }

    def _make_tools_decision_with_llm(
        self, assistant: Assistant, rag_toolkits: list[ToolKit], state: AssistantValidationState
    ) -> ToolsDecisionResult:
        """Use LLM to make final decision on tool selection with clarification context.

        Args:
            assistant: Assistant being validated
            rag_toolkits: Toolkits recommended by RAG
            state: Current workflow state (contains clarifications)

        Returns:
            ToolsDecisionResult with final tool selection
        """
        # Get clarification summary if available
        clarification_summary = ""
        if state.get("clarifications") and state["clarifications"]:
            clarification_summary = state["clarifications"].clarification_summary_markdown
        else:
            clarification_summary = (
                "No clarification analysis available - proceed with strict explicit capability analysis."
            )

        # Build prompt using TOOLS_DECISION_TEMPLATE with clarifications
        prompt = TOOLS_DECISION_TEMPLATE.format(
            clarification_summary=clarification_summary,
            assistant_name=assistant.name,
            assistant_description=assistant.description,
            assistant_categories=", ".join(assistant.categories) if assistant.categories else "Not specified",
            conversation_starters="; ".join(assistant.conversation_starters)
            if assistant.conversation_starters
            else "None",
            system_prompt_full=assistant.system_prompt,
            existing_tools=format_existing_tools_for_prompt(assistant),
            configured_context=format_configured_context_for_prompt(assistant),
            rag_candidate_tools=format_rag_toolkits_for_prompt(rag_toolkits),
        )

        # Invoke LLM with structured output, passing user from state for metrics
        decision = self.invoke_llm_with_retry(prompt, ToolsDecisionResult, user=state.get("user"))

        logger.info(
            f"Tools decision: tools_to_include={decision.tools_to_include}, "
            f"tools_to_exclude={decision.tools_to_exclude}"
        )

        return decision
