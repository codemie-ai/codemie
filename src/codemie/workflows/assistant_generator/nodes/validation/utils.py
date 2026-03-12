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

"""Utility functions for validation nodes."""

from typing import Any

from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.assistant_generator import ContextRecommendation, RecommendationAction
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.workflows.assistant_generator.models.validation_state import AssistantValidationState
from codemie_tools.base.models import ToolKit


def format_metadata_validation_prompt(state: AssistantValidationState) -> dict[str, Any]:
    """Format variables for metadata validation prompt.

    Args:
        state: Current workflow state

    Returns:
        Dictionary of template variables
    """
    assistant = state["assistant"]

    return {
        "name": assistant.name,
        "description": assistant.description,
        "system_prompt": assistant.system_prompt,
    }


def format_system_prompt_validation_prompt(state: AssistantValidationState) -> dict[str, Any]:
    """Format variables for system prompt validation prompt.

    Args:
        state: Current workflow state

    Returns:
        Dictionary of template variables
    """
    assistant = state["assistant"]

    return {
        "name": assistant.name,
        "description": assistant.description,
        "categories": ", ".join(assistant.categories) if assistant.categories else "Not specified",
        "conversation_starters": assistant.conversation_starters if assistant.conversation_starters else [],
        "system_prompt": assistant.system_prompt,
    }


def get_assistant_tool_names(assistant: Assistant) -> set[str]:
    """Extract current tool names from assistant.

    Args:
        assistant: Assistant being validated

    Returns:
        Set of current tool names
    """
    if not hasattr(assistant, "toolkits") or not assistant.toolkits:
        return set()

    return {tool.name for toolkit in assistant.toolkits for tool in toolkit.tools}


def get_configured_context_names(assistant: Assistant) -> list[str]:
    """Extract configured context names from assistant.

    Args:
        assistant: Assistant being validated

    Returns:
        List of configured context names
    """
    return [ctx.name for ctx in (assistant.context or [])]


def get_validated_context_info(
    assistant: Assistant, user: User, configured_context: list[str]
) -> tuple[list[dict], set[str]]:
    """Validate configured context and return info with descriptions.

    Validates that configured context exists in the database using the same logic
    as add_assistant_context. Returns ONLY context that exists in the database.

    Args:
        assistant: Assistant being validated
        user: User requesting validation
        configured_context: List of configured context names

    Returns:
        Tuple of (validated_context_info, validated_names_set)
        - validated_context_info: List of dicts with repo_name, index_type, description
        - validated_names_set: Set of validated context names
    """
    if not configured_context:
        return [], set()

    project_name = assistant.project or user.current_project
    context_index_infos = IndexInfo.filter_for_user_repo_names(
        user=user, project_name=project_name, repo_names=configured_context
    )

    validated_context = [
        {
            "repo_name": idx.repo_name,
            "index_type": idx.index_type,
            "description": idx.description,
        }
        for idx in context_index_infos
    ]
    validated_names = {idx.repo_name for idx in context_index_infos}

    return validated_context, validated_names


def format_context_for_prompt(validated_context: list[dict]) -> str:
    """Format validated context info for LLM prompt.

    Args:
        validated_context: List of dicts with repo_name, index_type, description

    Returns:
        Formatted string with context descriptions for prompt
    """
    return (
        "\n".join(
            f"{idx}. Context name: {ctx['repo_name']}\n\t"
            f"Context type: {ctx['index_type']}\n\t"
            f"Context description: {ctx['description'] or 'No description'}"
            for idx, ctx in enumerate(validated_context, start=1)
        )
        if validated_context
        else "No datasources available"
    )


def format_rag_toolkits_for_prompt(rag_toolkits: list[ToolKit]) -> str:
    """Format RAG candidate toolkits for LLM prompt.

    Args:
        rag_toolkits: List of ToolKit objects from RAG lookup

    Returns:
        Formatted string with toolkit and tool names for prompt
    """
    return (
        "No RAG candidates found"
        if not rag_toolkits
        else "\n".join(
            line
            for toolkit in rag_toolkits
            for line in [
                f"\n**{toolkit.toolkit}**:",
                *[
                    f"  - {tool.name} ({tool.label if hasattr(tool, 'label') else 'No label'})"
                    for tool in toolkit.tools
                ],
            ]
        )
    )


def format_existing_tools_for_prompt(assistant: Assistant) -> str:
    """Format existing tool names for LLM prompt.

    Args:
        assistant: Assistant being validated

    Returns:
        Comma-separated string of existing tool names or "None"
    """
    current_tool_names = get_assistant_tool_names(assistant)
    return ", ".join(current_tool_names) if current_tool_names else "None"


def format_configured_context_for_prompt(assistant: Assistant) -> str:
    """Format configured context for LLM prompt.

    Args:
        assistant: Assistant being validated

    Returns:
        Formatted string describing configured datasources
    """
    return (
        "No context configured"
        if not assistant.context
        else f"Configured datasources: {', '.join(ctx.name for ctx in assistant.context)}"
    )


# =============================================================================
# TOOLKIT LOOKUP UTILITIES
# =============================================================================


def get_toolkit_name(toolkit) -> str:
    """Extract toolkit name from toolkit object (handles both str and enum).

    Args:
        toolkit: Toolkit object or toolkit attribute

    Returns:
        Toolkit name as string
    """
    if hasattr(toolkit, 'value'):
        return toolkit.value
    return str(toolkit)


def find_toolkit_for_tool(tool_name: str, toolkits: list) -> str | None:
    """Find the toolkit name that contains a specific tool.

    Args:
        tool_name: Name of the tool to find
        toolkits: List of ToolKit objects (from RAG or assistant)

    Returns:
        Toolkit name or None if not found
    """
    for toolkit in toolkits:
        if any(tool.name == tool_name for tool in toolkit.tools):
            return get_toolkit_name(toolkit.toolkit)
    return None


def find_existing_toolkit_for_tool(tool_name: str, assistant: Assistant) -> str | None:
    """Find the toolkit name in existing assistant toolkits.

    Args:
        tool_name: Name of the tool to find
        assistant: Assistant being validated

    Returns:
        Toolkit name or None if not found
    """
    if not hasattr(assistant, "toolkits") or not assistant.toolkits:
        return None

    return find_toolkit_for_tool(tool_name, assistant.toolkits)


# =============================================================================
# CONTEXT RECOMMENDATION BUILDER
# =============================================================================


def create_context_recommendation(
    name: str, action: RecommendationAction, context_map: dict, reason_template: str, severity
) -> ContextRecommendation:
    """Build context recommendation with description.

    Args:
        name: Context name
        action: Recommendation action (Change/Delete)
        context_map: Map of context names to context info
        reason_template: Reason template with {name} and {desc} placeholders
        severity: Severity level from context validation result

    Returns:
        ContextRecommendation object
    """
    context_desc = ""
    if name in context_map:
        ctx_info = context_map[name]
        context_desc = f" ({ctx_info['index_type']}): {ctx_info['description'] or 'No description'}"

    return ContextRecommendation(
        name=name,
        action=action,
        reason=reason_template.format(name=name, desc=context_desc),
        severity=severity,
    )
