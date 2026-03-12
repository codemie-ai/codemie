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
Pydantic models for assistant validation workflow.

These models define the structured outputs from LLM validation and verification nodes.
"""

from pydantic import BaseModel, Field
from typing import Optional
from codemie_tools.base.models import ToolKit
from codemie.rest_api.models.assistant_generator import RecommendationSeverity

# Constants for field descriptions
SEVERITY_LEVEL_DESCRIPTION = (
    "Severity level: 'critical' for serious issues, 'optional' for recommendations. This is informational only."
)


class ValidationResult(BaseModel):
    """System prompt validation output"""

    is_valid: bool = Field(description="Is the system prompt valid?")
    issues: list[str] = Field(default_factory=list, description="List of specific issues found")
    recommendation: Optional[str] = Field(default=None, description="Improved text (if is_valid=false)")
    severity: RecommendationSeverity = Field(
        default=RecommendationSeverity.CRITICAL,
        description=SEVERITY_LEVEL_DESCRIPTION,
    )


class MetadataFieldValidation(BaseModel):
    """Validation result for a single metadata field"""

    field_name: str = Field(description="Name of the field (name, description, categories)")
    is_valid: bool = Field(description="Is the field valid?")
    issues: list[str] = Field(default_factory=list, description="List of specific issues found")
    recommendation: Optional[str | list[str]] = Field(
        default=None, description="Improved value (str for name/description, list[str] for categories)"
    )
    severity: RecommendationSeverity = Field(
        default=RecommendationSeverity.CRITICAL,
        description=SEVERITY_LEVEL_DESCRIPTION,
    )


class MetadataValidationResult(BaseModel):
    """Metadata validation output for Name, Description, and Categories"""

    fields: list[MetadataFieldValidation] = Field(default_factory=list, description="Validation results for each field")
    is_valid: bool = Field(description="Are all metadata fields valid?")
    overall_reasoning: str = Field(description="Overall assessment of metadata quality")


class ToolsValidationResult(BaseModel):
    """Tools validation from RAG"""

    is_valid: bool = Field(description="Are the tools valid?")
    recommended_toolkits: list[ToolKit] = Field(default_factory=list, description="Full RAG results")
    recommended_additions: list[str] = Field(default_factory=list, description="Tool names to add")
    recommended_deletions: list[str] = Field(default_factory=list, description="Tool names to remove")
    tools_to_keep: list[str] = Field(default_factory=list, description="Tool names that are correct")
    rag_query: str = Field(description="Query used (for logging)")
    reasoning: str = Field(description="Reasoning for tool recommendations")
    severity: RecommendationSeverity = Field(
        default=RecommendationSeverity.CRITICAL,
        description=SEVERITY_LEVEL_DESCRIPTION,
    )


class ContextValidationResult(BaseModel):
    """Context validation output with LLM-based intelligent recommendations"""

    is_valid: bool = Field(description="Is the context configuration valid?")
    context_to_update: list[str] = Field(
        default_factory=list,
        description="Context names that need attention/UPDATE (includes: invalid context that doesn't exist, "
        "wrong data source type, wrong data source content, or unnecessary context not mentioned in system prompt)",
    )
    available_context: list[str] = Field(default_factory=list, description="All available context names for user")
    reasoning: str = Field(description="Reasoning for context validation and recommendations")
    severity: RecommendationSeverity = Field(
        default=RecommendationSeverity.CRITICAL,
        description=SEVERITY_LEVEL_DESCRIPTION,
    )


class ToolsDecisionResult(BaseModel):
    """LLM-based final decision on which tools to include/exclude based on assistant metadata.

    This replaces verification approach - LLM makes the FINAL decision by analyzing:
    - Assistant metadata (name, description, categories, conversation_starters, system_prompt)
    - RAG candidate tools (suggestions from semantic search)
    - Available tools in system (to identify missing tools)
    """

    tools_to_include: list[str] = Field(
        default_factory=list, description="Tool names to ADD (filtered from RAG + missing tools)"
    )
    tools_to_exclude: list[str] = Field(
        default_factory=list, description="Tool names to REJECT (not relevant to capabilities)"
    )
    reasoning: str = Field(description="Explanation of which tools included/excluded and why")
