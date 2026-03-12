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

from enum import StrEnum
from typing import List, Optional

from fastapi import Body
from pydantic import BaseModel, Field

from codemie.service.llm_service.llm_service import llm_service

# Constants for field descriptions
SEVERITY_DESCRIPTION = (
    "Severity level of this recommendation. "
    "'critical' indicates serious issues that significantly impact quality; "
    "'optional' indicates suggestions for improvement. "
    "This is informational only - users can bypass all recommendations."
)


class AssistantGeneratorRequest(BaseModel):
    """Request model for generating assistant details."""

    text: str = Field(..., description="User input text to generate assistant details from")
    include_tools: bool = Field(True, description="Whether to include tools suggestion")
    llm_model: Optional[str] = Field(
        default_factory=lambda: llm_service.default_llm_model, description="Optional LLM model to use for generation"
    )


class AssistantGeneratorResponse(BaseModel):
    """Response model for generated assistant details."""

    name: str = Field(..., description="Generated assistant name")
    description: str = Field(..., description="Generated assistant description")
    categories: list[str] = Field(
        default_factory=list, description="A list of the assistant's primary areas or domain use cases."
    )
    conversation_starters: List[str] = Field(..., description="List of conversation starter suggestions")
    system_prompt: str = Field(..., description="Comprehensive system prompt for the assistant")
    toolkits: List = Field(
        default=[], description="List of toolkits with their tools that should be used by the assistant"
    )


class PromptGeneratorRequest(BaseModel):
    """Request model for generating assistant details."""

    text: Optional[str] = Field(default=None, description="User input text to generate assistant details from")
    system_prompt: Optional[str] = Field(None, description="System prompt which user may create")
    llm_model: Optional[str] = Field(
        default_factory=lambda: llm_service.default_llm_model, description="Optional LLM model to use for generation"
    )


class PromptGeneratorResponse(BaseModel):
    system_prompt: str = Field(..., description="Comprehensive system prompt for the assistant")


class AssistantContext(BaseModel):
    name: str = Field(...)
    context_type: str = Field(...)


class RefineRequest(BaseModel):
    """Request model for assistant refinement/validation by LLM."""

    name: Optional[str] = Field(default=None, description="Draft assistant name")
    description: Optional[str] = Field(default=None, description="Draft assistant description")
    conversation_starters: Optional[List[str]] = Field(
        default=None, description="Conversation starters / sample prompts"
    )
    system_prompt: Optional[str] = Field(default=None, description="System prompt / assistant prompt")
    toolkits: Optional[List[dict]] = Field(default=None, description="List of selected tool identifiers")
    context: Optional[List[AssistantContext]] = Field(default=None, description="List of selected contexts")
    categories: list[str] | None = Field(
        default=None,
        description="A list of classifications that define the assistant's primary areas of focus or domain use cases.",
    )
    refine_prompt: Optional[str] = Field(
        default=None,
        description="User's custom refinement instructions to guide the LLM in improving the assistant",
    )
    llm_model: Optional[str] = Field(
        default_factory=lambda: llm_service.default_llm_model,
        description="Optional LLM model to use for validation/refinement",
    )
    include_tools: bool = Field(default=True)
    include_context: bool = Field(default=True)
    include_categories: bool = Field(default=True)
    project: str | None = Field(default=None)


RefineRequestBody = Body(
    ...,
    openapi_examples={
        "email assistant": {
            "value": {
                "name": "Email",
                "description": "Helps with emails",
                "system_prompt": "You help write emails",
                "conversation_starters": ["Write email", "Check inbox"],
                "categories": ["engineering"],
                "toolkits": [
                    {"toolkit": "VCS", "tools": [{"name": "github", "label": "Github"}]},
                    {"toolkit": "Notification", "tools": [{"name": "Email", "label": "Email"}]},
                ],
                "context": [
                    {"name": "email_template", "context_type": "knowledge_base"},
                    {"name": "projectcodebase", "context_type": "code"},
                ],
                "llm_model": llm_service.default_llm_model,
                "include_context": True,
                "include_tools": True,
                "include_categories": True,
            },
        },
        "email assistant unchanged": {
            "value": {
                "name": "Professional Email Assistant",
                "conversation_starters": [
                    "Help me write a professional business email",
                    "Suggest an email template for a meeting follow-up",
                    "Review my email draft for clarity and tone",
                    "Compose an email to request information from a client",
                ],
                "toolkits": [{"toolkit": "Notification", "tools": [{"name": "Email", "label": "Email"}]}],
                "context": [{"name": "email_template", "context_type": "knowledge_base"}],
                "llm_model": llm_service.default_llm_model,
            }
        },
        "jira": {
            "value": {
                "name": "Jira",
                "toolkits": [],
                "context": [{"name": "unknow", "context_type": "unknow"}],
                "project": "codemie",
            }
        },
    },
)


class RecommendationAction(StrEnum):
    Change = "change"
    Delete = "delete"
    Keep = "keep"


class RecommendationSeverity(StrEnum):
    """Severity level for validation recommendations during marketplace publishing.

    This is informational only - helps users understand the importance of recommendations.
    Both critical and optional recommendations can be bypassed via 'Publish Anyway' button.
    """

    CRITICAL = "critical"  # Serious issues that significantly impact quality
    OPTIONAL = "optional"  # Suggestions for improvement, less critical


class FieldRecommendation(BaseModel):
    """A single recommendation targeted at a specific assistant field."""

    name: str = Field(
        ...,
        description=(
            "Identifier of the field this recommendation targets. "
            "Examples: 'description', 'system_prompt', 'conversation_starters', 'toolkits', 'datasources', 'categories'"
        ),
    )
    action: RecommendationAction = Field(
        ...,
        description=(
            "Suggested action for the field. One of: 'change', 'delete', 'keep'. "
            "If 'change', include the intended modification in the 'reason' or supply a corresponding action."
        ),
    )
    recommended: Optional[str | list[str]] = Field(
        default=None,
        description="LLM-recommended replacement or correction. Prefer concise, final text.",
    )
    reason: Optional[str] = Field(
        None,
        description=(
            "Explanation for this recommendation. Include the problem, impact of the change"
            "(e.g., 'remove ambiguous terms'). Keep to 1-3 sentences."
        ),
    )
    severity: RecommendationSeverity = Field(
        default=RecommendationSeverity.CRITICAL,
        description=SEVERITY_DESCRIPTION,
    )


class ToolRecommendation(BaseModel):
    """Recommendation about a single tool within a toolkit."""

    name: str = Field(
        ..., description="Tool identifier (e.g., 'Email', 'github'). Must match the name used in the assistant config."
    )

    action: RecommendationAction = Field(
        ...,
        description=(
            "Suggested action for the tool. One of: 'change', 'delete', 'keep'. "
            "If 'change', include the intended modification in the 'reason' or supply a corresponding action."
        ),
    )
    reason: Optional[str] = Field(
        None,
        description=(
            "Short justification. For 'change', include the recommended new behavior or config; "
            "for 'delete', cite reasons (irrelevance, risk, redundancy); for 'keep', state why it is necessary."
        ),
    )
    severity: RecommendationSeverity = Field(
        default=RecommendationSeverity.CRITICAL,
        description=SEVERITY_DESCRIPTION,
    )


class ToolkitRecommendation(BaseModel):
    """Recommendations scoped to a toolkit (a named collection of tools)."""

    toolkit: str = Field(..., description="Toolkit identifier as provided in the assistant.")
    tools: List[ToolRecommendation] = Field(
        ...,
        description=(
            "List of per-tool recommendations that apply to tools inside this toolkit. "
            "Each entry describes the action and justification for that tool."
        ),
    )


class ContextRecommendation(BaseModel):
    """Recommendation for a datasource referenced by the assistant."""

    name: str = Field(..., description="Identifier or name of the context (e.g., 'email_templates', 'knowledge_base').")
    action: RecommendationAction = Field(
        ..., description="Suggested action for the context: 'change', 'delete', or 'keep'."
    )
    reason: Optional[str] = Field(
        None,
        description=(
            "Explanation of the action. If action is 'change', describe what to change (content, schema, access)."
            "If 'delete', describe risks or redundancy."
        ),
    )
    severity: RecommendationSeverity = Field(
        default=RecommendationSeverity.CRITICAL,
        description=SEVERITY_DESCRIPTION,
    )


class RefineGeneratorResponse(BaseModel):
    """Response model containing LLM-generated recommendations."""

    fields: List[FieldRecommendation] = Field(
        default_factory=list,
        description=(
            "Per-field recommendations generated by the LLM. Each item suggests a direct replacement or correction "
            "and a short justification."
        ),
    )

    toolkits: List[ToolkitRecommendation] = Field(
        default_factory=list,
        description="Per-toolkit recommendations. Include only toolkits that require action ('change'/'delete'/'keep')",
    )
    context: List[ContextRecommendation] = Field(
        default_factory=list,
        description="Per-datasource recommendations indicating whether to keep, change, or delete datasources.",
    )
