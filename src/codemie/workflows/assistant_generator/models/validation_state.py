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
State schema for assistant validation workflow with parallel execution support.
"""

from typing import Annotated, Any, Literal, Optional

from typing_extensions import TypedDict

from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.assistant_generator import RefineGeneratorResponse
from codemie.rest_api.security.user import User
from codemie.workflows.assistant_generator.models.clarification_models import ClarificationAnalysis
from codemie.workflows.assistant_generator.models.validation_models import (
    ContextValidationResult,
    MetadataValidationResult,
    ToolsValidationResult,
    ValidationResult,
)


def _merge_last_value(left: Any, right: Any) -> Any:
    """Reducer that takes the last non-None value.

    Used for fields that can be updated by parallel nodes without conflicts.
    Each node updates its own specific result field.
    """
    return right if right is not None else left


class AssistantValidationState(TypedDict):
    """State for assistant validation workflow with parallel execution support.

    Uses TypedDict with Annotated reducers to handle concurrent updates from parallel nodes.
    Each validation node updates only its specific result field, avoiding conflicts.

    Workflow: start → [metadata, system_prompt, tools, context] in parallel → decision
    """

    # ============================================================================
    # INPUT FIELDS (Set at workflow initialization, never updated)
    # ============================================================================

    assistant: Assistant  # Assistant being validated (read-only after init)
    has_sub_assistants: bool

    user: User  # User requesting validation (read-only after init)
    request_id: str  # Unique identifier for this validation request

    # ============================================================================
    # CLARIFICATION ANALYSIS (populated before validation by clarification node)
    # ============================================================================

    # Clarification analysis with questions, answers, and guidance
    clarifications: Annotated[Optional[ClarificationAnalysis], _merge_last_value]

    # ============================================================================
    # VALIDATION RESULTS PER PHASE (each node updates its own field)
    # Each validation node updates ONLY its specific result field, no conflicts
    # ============================================================================

    # Metadata validation (name, description, categories)
    metadata_result: Annotated[Optional[MetadataValidationResult], _merge_last_value]

    # System prompt validation
    system_prompt_result: Annotated[Optional[ValidationResult], _merge_last_value]

    # Tools validation (RAG-based)
    tools_result: Annotated[Optional[ToolsValidationResult], _merge_last_value]

    # Context validation (knowledge bases, datasources)
    context_result: Annotated[Optional[ContextValidationResult], _merge_last_value]
    context_info: Annotated[Optional[list[dict]], _merge_last_value]

    # ============================================================================
    # FINAL OUTPUT (populated by decision node only)
    # ============================================================================

    decision: Annotated[Optional[Literal["accept", "reject"]], _merge_last_value]
    recommendations: Annotated[Optional[RefineGeneratorResponse], _merge_last_value]
