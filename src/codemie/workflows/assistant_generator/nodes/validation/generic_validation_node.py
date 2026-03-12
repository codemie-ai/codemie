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

"""Generic validation node that can handle multiple validation phases."""

from typing import Callable, Any
from pydantic import BaseModel

from codemie.workflows.assistant_generator.nodes.validation.base_validation_node import BaseValidationNode
from codemie.workflows.assistant_generator.models.validation_state import AssistantValidationState


class GenericValidationNode(BaseValidationNode):
    """Generic validation node that can be configured for different validation phases.

    This node eliminates code duplication by providing a configurable validation pattern
    that can handle metadata, system_prompt, tools, and context validation phases.
    """

    def __init__(
        self,
        llm_model: str,
        request_id: str | None,
        phase_name: str,
        result_field: str,
        next_phase: str,
        output_model: type[BaseModel],
        prompt_template: str,
        prompt_formatter: Callable[[AssistantValidationState], dict[str, Any]],
    ):
        """Initialize generic validation node.

        Args:
            llm_model: LLM model to use for validation
            request_id: Request ID for logging/tracing
            phase_name: Name of this validation phase (e.g., "metadata", "system_prompt")
            result_field: State field to store validation result (e.g., "metadata_result")
            next_phase: Next phase name (unused in parallel workflow, kept for compatibility)
            output_model: Pydantic model for structured LLM output
            prompt_template: Template string for LLM prompt (with format placeholders)
            prompt_formatter: Function that extracts prompt variables from state
        """
        super().__init__(llm_model, request_id)
        self.phase_name = phase_name
        self.result_field = result_field
        self.next_phase: str = next_phase
        self.output_model = output_model
        self.prompt_template = prompt_template
        self.prompt_formatter = prompt_formatter

    def __call__(self, state: AssistantValidationState) -> dict:
        """Execute validation for this phase.

        Args:
            state: Current workflow state

        Returns:
            Dictionary with only the fields updated by this node
        """

        # Build prompt using phase-specific formatter
        prompt_vars = self.prompt_formatter(state)
        prompt = self.prompt_template.format(**prompt_vars)

        # Invoke LLM with structured output, passing user from state for metrics
        result = self.invoke_llm_with_retry(prompt, self.output_model, user=state.get("user"))

        # Return only the fields this node updates
        return {
            self.result_field: result,
        }
