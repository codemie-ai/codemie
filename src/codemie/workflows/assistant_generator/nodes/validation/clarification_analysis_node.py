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

"""
Clarification Analysis Node for assistant validation workflow.

This node automatically generates and answers clarification questions about the assistant
specification to improve validation accuracy and tool/context selection.
"""

from codemie.configs.logger import logger
from codemie.rest_api.models.assistant import Assistant
from codemie.templates.agents.assistant_clarification_prompts import (
    COMBINED_CLARIFICATION_ANALYSIS_PROMPT,
    CLARIFICATION_SUMMARY_TEMPLATE,
)
from codemie.workflows.assistant_generator.models.clarification_models import (
    AnswersGeneration,
    ClarificationAnalysis,
)
from codemie.workflows.assistant_generator.models.validation_state import AssistantValidationState
from codemie.workflows.assistant_generator.nodes.validation.base_validation_node import BaseValidationNode
from codemie.workflows.assistant_generator.nodes.validation.utils import (
    format_configured_context_for_prompt,
    format_existing_tools_for_prompt,
)


class ClarificationAnalysisNode(BaseValidationNode):
    """Generate and answer clarification questions automatically to improve validation accuracy.

    This node performs a single-step LLM-based analysis that generates clarification questions
    AND provides evidence-based answers in one call, improving efficiency and consistency.

    The results provide enriched context for downstream validation nodes to make
    better decisions about tool and context selection.
    """

    def __call__(self, state: AssistantValidationState) -> dict:
        """Execute combined clarification analysis (questions + answers in one step).

        Args:
            state: Current workflow state with assistant specification

        Returns:
            Dictionary with clarifications field containing complete analysis
        """
        assistant = state["assistant"]

        logger.info(f"Starting combined clarification analysis for assistant: {assistant.name}")

        # Single-step: Generate questions AND answers in one LLM call
        analysis_result = self._generate_clarifications(assistant, user=state.get("user"))

        # Inject mandatory datasource validation question if context is configured
        clarifications_list = list(analysis_result.clarifications) if analysis_result.clarifications else []

        # Format clarification summary for validation prompts (using utility function)
        summary_markdown = CLARIFICATION_SUMMARY_TEMPLATE.render(clarifications=analysis_result.clarifications)

        logger.info(f"Clarification analysis complete: {len(clarifications_list)} questions analyzed")

        # Return clarification analysis to state
        return {
            "clarifications": ClarificationAnalysis(
                questions_generated=len(clarifications_list),
                questions=clarifications_list,
                clarification_summary_markdown=summary_markdown,
            )
        }

    def _generate_clarifications(self, assistant: Assistant, user=None) -> AnswersGeneration:
        """Generate clarification questions AND answers in a single LLM call.

        This combined approach is more efficient and ensures consistency between
        questions and answers.

        Args:
            assistant: Assistant being validated
            user: User object for metrics tracking (optional)

        Returns:
            AnswersGeneration with questions and evidence-based answers
        """
        prompt = COMBINED_CLARIFICATION_ANALYSIS_PROMPT.format(
            name=assistant.name,
            description=assistant.description,
            categories=", ".join(assistant.categories) if assistant.categories else "None",
            system_prompt=assistant.system_prompt,
            conversation_starters="; ".join(assistant.conversation_starters)
            if assistant.conversation_starters
            else "None",
            configured_tools=format_existing_tools_for_prompt(assistant),
            configured_context=format_configured_context_for_prompt(assistant),
        )
        return self.invoke_llm_with_retry(prompt, AnswersGeneration, user=user)
