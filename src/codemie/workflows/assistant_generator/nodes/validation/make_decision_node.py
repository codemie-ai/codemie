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

"""Decision node - aggregates validation results and builds recommendations."""

from collections import defaultdict

from pydantic import BaseModel, Field

from codemie.configs.logger import logger
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.assistant_generator import (
    FieldRecommendation,
    RecommendationAction,
    RefineGeneratorResponse,
    ToolkitRecommendation,
    ToolRecommendation,
)
from codemie.templates.agents.assistant_validation_prompts import build_friendly_message_generation_prompt
from codemie.workflows.assistant_generator.models.validation_models import (
    MetadataValidationResult,
    ValidationResult,
)
from codemie.workflows.assistant_generator.models.validation_state import AssistantValidationState
from codemie.workflows.assistant_generator.nodes.validation.base_validation_node import BaseValidationNode
from codemie.workflows.assistant_generator.nodes.validation.utils import (
    create_context_recommendation,
    find_existing_toolkit_for_tool,
    find_toolkit_for_tool,
)


class FriendlyMessages(BaseModel):
    field_messages: list[str] = Field(default_factory=list)
    tool_messages: list[str] = Field(default_factory=list)
    context_messages: list[str] = Field(default_factory=list)


class MakeDecisionNode(BaseValidationNode):
    """Aggregate validation results and make final decision"""

    def __call__(self, state: AssistantValidationState) -> dict:
        """Execute decision logic.

        Args:
            state: Current workflow state

        Returns:
            Dictionary with only the fields updated by this node
        """
        # Check if all validation phases passed
        all_valid = self._all_phases_valid(state)

        if all_valid:
            logger.info("Validation decision: ACCEPT")
            return {
                "decision": "accept",
                "recommendations": RefineGeneratorResponse(
                    fields=[],
                    toolkits=[],
                    context=[],
                ),
            }
        else:
            return {
                "decision": "reject",
                "recommendations": self._build_recommendations(state),
            }

    def _all_phases_valid(self, state: AssistantValidationState) -> bool:
        """Check if all validation phases are valid.

        Args:
            state: Current workflow state

        Returns:
            True if all phases are valid, False otherwise
        """
        return bool(
            state["metadata_result"]
            and state["metadata_result"].is_valid
            and state["system_prompt_result"]
            and state["system_prompt_result"].is_valid
            and state["tools_result"]
            and state["tools_result"].is_valid
            and state["context_result"]
            and state["context_result"].is_valid
        )

    def _build_recommendations(self, state: AssistantValidationState) -> RefineGeneratorResponse:
        """Build recommendations from validation results.

        Args:
            state: Current workflow state

        Returns:
            RefineGeneratorResponse with all recommendations
        """
        # Build recommendations
        recommendations = RefineGeneratorResponse(
            fields=[],
            toolkits=[],
            context=[],
        )

        if state["metadata_result"] and not state["metadata_result"].is_valid:
            # Gather recommendations from each validation phase
            self._handle_field_recommendations(
                state["metadata_result"],
                recommendations,
            )

        if state["system_prompt_result"] and not state["system_prompt_result"].is_valid:
            self._handle_system_prompt_recommendations(
                state["system_prompt_result"],
                recommendations,
            )

        self._handle_tools_recommendations(state, recommendations)
        self._handle_context_recommendations(state, recommendations)

        # Enhance with user-friendly messages via LLM
        logger.info("Generating user-friendly messages for all recommendations...")
        return self._enhance_recommendations_with_friendly_messages(state, recommendations)

    def _handle_system_prompt_recommendations(
        self,
        system_prompt_result: ValidationResult,
        recommendations: RefineGeneratorResponse,
    ) -> None:
        """Handle system prompt recommendations.

        Args:
            state: Current workflow state
            recommendations: Recommendations object to populate
        """
        # System prompt recommendations
        recommendations.fields.append(
            FieldRecommendation(
                name="system_prompt",
                action=RecommendationAction.Change,
                recommended=system_prompt_result.recommendation or "Please improve the system prompt",
                reason="; ".join(system_prompt_result.issues)
                if system_prompt_result.issues
                else "Quality issues detected",
                severity=system_prompt_result.severity,
            )
        )

    def _handle_field_recommendations(
        self,
        metadata_result: MetadataValidationResult,
        recommendations: RefineGeneratorResponse,
    ) -> None:
        """Handle metadata and system prompt field recommendations.

        Args:
            metadata_result: Metadata validation result
            system_prompt_result: System prompt validation result
            recommendations: Recommendations object to populate
            reasoning_parts: List to append reasoning messages to
        """
        # Metadata recommendations (Name, Description only - Categories excluded)
        for field in metadata_result.fields:
            if field.is_valid:
                continue

            # Skip categories - we don't recommend category changes
            if field.field_name == "categories":
                continue

            # Format recommended value based on field type
            recommended_value = str(field.recommendation)

            recommendations.fields.append(
                FieldRecommendation(
                    name=field.field_name,
                    action=RecommendationAction.Change,
                    recommended=recommended_value,
                    reason="; ".join(field.issues)
                    if field.issues
                    else f"Quality issues detected in {field.field_name}",
                    severity=field.severity,
                )
            )

    def _process_tool_additions(
        self,
        tools_to_include: list[str],
        recommended_toolkits: list,
        toolkit_map: dict[str, list[ToolRecommendation]],
        severity,
    ) -> None:
        """Process tool additions and populate toolkit_map.

        Args:
            tools_to_include: Tools approved for addition by LLM
            current_tools: Currently configured tools
            has_equivalent: Map of capability to existing equivalent tool
            recommended_toolkits: Toolkits from RAG recommendations
            toolkit_map: Map to populate with tool recommendations
            tools_to_skip_deletion: Set to track tools that should not be deleted
            severity: Severity level from tools validation result
        """
        for tool_name in tools_to_include:
            if toolkit_name := find_toolkit_for_tool(tool_name, recommended_toolkits):
                toolkit_map[toolkit_name].append(
                    ToolRecommendation(
                        name=tool_name,
                        action=RecommendationAction.Change,
                        reason="Add the essential tool to enable assistant capabilities",
                        severity=severity,
                    )
                )

    def _process_tool_deletions(
        self,
        recommended_deletions: list[str],
        assistant: Assistant,
        toolkit_map: dict[str, list[ToolRecommendation]],
        severity,
    ) -> None:
        """Process tool deletions and populate toolkit_map.

        Args:
            recommended_deletions: Tools recommended for deletion
            assistant: Assistant being validated
            toolkit_map: Map to populate with tool recommendations
            severity: Severity level from tools validation result
        """

        for tool_name in recommended_deletions:
            if toolkit_name := find_existing_toolkit_for_tool(tool_name, assistant):
                toolkit_map[toolkit_name].append(
                    ToolRecommendation(
                        name=tool_name,
                        action=RecommendationAction.Delete,
                        reason="Tool could be removed, as it may not be necessary",
                        severity=severity,
                    )
                )

    def _handle_tools_recommendations(
        self,
        state: AssistantValidationState,
        recommendations: RefineGeneratorResponse,
    ) -> None:
        """Handle tools recommendations.

        Args:
            state: Current workflow state
            recommendations: Recommendations object to populate
        """
        if not state["tools_result"]:
            return

        # Build toolkit recommendations
        toolkit_map = defaultdict(list[ToolRecommendation])

        # Get severity from tools validation result
        tools_severity = state["tools_result"].severity

        # Process tool additions (use recommended_additions from tools_result)
        self._process_tool_additions(
            tools_to_include=state["tools_result"].recommended_additions,
            recommended_toolkits=state["tools_result"].recommended_toolkits,
            toolkit_map=toolkit_map,
            severity=tools_severity,
        )

        # Process tool deletions
        self._process_tool_deletions(
            recommended_deletions=state["tools_result"].recommended_deletions,
            assistant=state["assistant"],
            toolkit_map=toolkit_map,
            severity=tools_severity,
        )

        # Convert toolkit_map to recommendations
        recommendations.toolkits.extend(
            ToolkitRecommendation(toolkit=toolkit_name, tools=tool_recs)
            for toolkit_name, tool_recs in toolkit_map.items()
        )

    def _process_context_to_update(
        self,
        context_to_update: list[str],
        context_map: dict[str, dict],
        recommendations: RefineGeneratorResponse,
        severity,
    ) -> None:
        """Process contexts that need update/attention.

        Args:
            context_to_update: List of context names that need attention
            available_context: List of all available context names
            context_map: Map of context names to context info
            recommendations: Recommendations object to populate
            reasoning: Validation reasoning to determine specific issues
            severity: Severity level from context validation result
        """
        for context_name in context_to_update:
            context_rec = create_context_recommendation(
                context_name,
                RecommendationAction.Change,
                context_map,
                "Context '{name}' needs attention. Check validation reasoning for details.",
                severity,
            )
            recommendations.context.append(context_rec)

    def _handle_context_recommendations(
        self,
        state: AssistantValidationState,
        recommendations: RefineGeneratorResponse,
    ) -> None:
        """Handle context recommendations.

        Args:
            state: Current workflow state
            recommendations: Recommendations object to populate
        """
        context_result = state["context_result"]

        if not context_result or context_result.is_valid:
            return

        # Get context info with descriptions from state
        context_info = state["context_info"] or []
        context_map = {ctx["repo_name"]: ctx for ctx in context_info}

        # Get severity from context validation result
        context_severity = context_result.severity

        # Process all contexts that need update using unified method
        self._process_context_to_update(
            context_result.context_to_update,
            context_map,
            recommendations,
            context_severity,
        )

    def _enhance_recommendations_with_friendly_messages(
        self, state: AssistantValidationState, recommendations: RefineGeneratorResponse
    ) -> RefineGeneratorResponse:
        """Generate user-friendly messages for all recommendations in a single LLM call.

        Args:
            state: Current workflow state (contains assistant and user)
            recommendations: The recommendations to enhance

        Returns:
            Enhanced recommendations with user-friendly reason messages
        """
        # Skip if no recommendations to enhance
        if not self._has_recommendations(recommendations):
            return recommendations

        assistant = state["assistant"]
        try:
            # Generate friendly messages via LLM
            friendly_messages = self._generate_friendly_messages_via_llm(
                assistant, recommendations, state, user=state.get("user")
            )

            # Apply friendly messages to recommendations
            self._apply_field_messages(recommendations, friendly_messages.field_messages)
            self._apply_tool_messages(recommendations, friendly_messages.tool_messages)
            self._apply_context_messages(recommendations, friendly_messages.context_messages)

            logger.info(
                f"Generated {len(friendly_messages.field_messages)} field messages, "
                f"{len(friendly_messages.tool_messages)} tool messages, "
                f"{len(friendly_messages.context_messages)} context messages"
            )

        except Exception as e:
            logger.warning(f"Failed to generate user-friendly messages with LLM: {e}")
            # Keep the original technical reasons as fallback

        return recommendations

    def _has_recommendations(self, recommendations: RefineGeneratorResponse) -> bool:
        """Check if there are any recommendations to enhance.

        Args:
            recommendations: Recommendations to check

        Returns:
            True if there are recommendations, False otherwise
        """
        return bool(recommendations.fields or recommendations.toolkits or recommendations.context)

    def _generate_friendly_messages_via_llm(
        self, assistant, recommendations: RefineGeneratorResponse, state: AssistantValidationState, user=None
    ):
        """Generate friendly messages using LLM.

        Args:
            assistant: The assistant being validated
            recommendations: Recommendations to generate messages for
            state: Current workflow state (for accessing validation results)
            user: User object for metrics tracking (optional)

        Returns:
            FriendlyMessages with field, tool, and context messages
        """

        prompt = build_friendly_message_generation_prompt(
            assistant_name=assistant.name,
            assistant_description=assistant.description,
            assistant_categories=', '.join(assistant.categories) if assistant.categories else 'None',
            field_recommendations=recommendations.fields,
            toolkit_recommendations=recommendations.toolkits,
            context_recommendations=recommendations.context,
            metadata_result=state.get("metadata_result"),
            system_prompt_result=state.get("system_prompt_result"),
            tools_result=state.get("tools_result"),
            context_result=state.get("context_result"),
        )

        return self.invoke_llm_with_retry(prompt, FriendlyMessages, user=user)

    def _apply_field_messages(self, recommendations: RefineGeneratorResponse, field_messages: list[str]) -> None:
        """Apply field messages to field recommendations.

        Args:
            recommendations: Recommendations to update
            field_messages: Friendly messages for fields
        """
        if field_messages and len(field_messages) == len(recommendations.fields):
            for field_rec, message in zip(recommendations.fields, field_messages, strict=True):
                field_rec.reason = message

    def _apply_tool_messages(self, recommendations: RefineGeneratorResponse, tool_messages: list[str]) -> None:
        """Apply tool messages to tool recommendations.

        Args:
            recommendations: Recommendations to update
            tool_messages: Friendly messages for tools
        """
        if not tool_messages:
            return

        total_tools = sum(len(tk.tools) for tk in recommendations.toolkits)
        if len(tool_messages) == total_tools:
            all_tools = (tool for toolkit in recommendations.toolkits for tool in toolkit.tools)
            for tool_rec, message in zip(all_tools, tool_messages, strict=True):
                tool_rec.reason = message

    def _apply_context_messages(self, recommendations: RefineGeneratorResponse, context_messages: list[str]) -> None:
        """Apply context messages to context recommendations.

        Args:
            recommendations: Recommendations to update
            context_messages: Friendly messages for context
        """
        if context_messages and len(context_messages) == len(recommendations.context):
            for ctx_rec, message in zip(recommendations.context, context_messages, strict=True):
                ctx_rec.reason = message
