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

"""Context validation node with LLM-based intelligent analysis."""

from codemie.configs.logger import logger
from codemie.templates.agents.assistant_validation_prompts import CONTEXT_VALIDATION_TEMPLATE
from codemie.workflows.assistant_generator.models.validation_models import ContextValidationResult
from codemie.workflows.assistant_generator.models.validation_state import AssistantValidationState
from codemie.workflows.assistant_generator.nodes.validation.base_validation_node import BaseValidationNode
from codemie.workflows.assistant_generator.nodes.validation.utils import (
    format_context_for_prompt,
    get_configured_context_names,
    get_validated_context_info,
)


class ValidateContextNode(BaseValidationNode):
    """Validate context availability and requirements using LLM"""

    def __call__(self, state: AssistantValidationState) -> dict:
        """Execute intelligent context validation.

        Args:
            state: Current workflow state

        Returns:
            Dictionary with only the fields updated by this node
        """
        assistant = state["assistant"]

        # Get configured context names using utility function
        configured_context = get_configured_context_names(assistant)

        # If no context configured, return success result immediately
        if not configured_context:
            logger.info(f"No context configured for assistant: {assistant.name} - returning success result")
            result = ContextValidationResult(
                is_valid=True,
                context_to_update=[],
                available_context=[],
                reasoning="No context configured - validation passes by default.",
            )
            return {
                "context_info": [],
                "context_result": result,
            }

        # Validate configured context exists (returns only what exists in database)
        validated_context, validated_names = get_validated_context_info(assistant, state["user"], configured_context)

        # Available context = validated context (what actually exists and is attached)
        available_names = list(validated_names)

        clarification = state.get("clarifications")

        # Build prompt with clarifications (focus on system prompt analysis, not tools)
        prompt = CONTEXT_VALIDATION_TEMPLATE.format(
            clarification_kb_summary=clarification.clarification_summary_markdown
            if clarification
            else "No clarification analysis available",
            assistant_name=assistant.name,
            assistant_description=assistant.description,
            system_prompt=assistant.system_prompt,
            configured_context=configured_context,
            available_context=format_context_for_prompt(validated_context),
        )

        # Invoke LLM with structured output, passing user from state for metrics
        result = self.invoke_llm_with_retry(prompt, ContextValidationResult, user=state.get("user"))

        # Ensure available_context is populated correctly
        result.available_context = available_names

        logger.info(
            f"Context validation completed: is_valid={result.is_valid}, "
            f"context_to_update={result.context_to_update}, "
            f"available_context={result.available_context}"
        )

        # Return only the fields this node updates
        return {
            "context_info": validated_context,
            "context_result": result,
        }
