# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from typing import List, Optional

from pydantic import BaseModel, Field

from codemie.rest_api.models.skill import MAX_CONTENT_LENGTH
from codemie.service.assistant_generator_service import AssistantToolkit
from codemie.service.llm_service.llm_service import llm_service


class SkillRefineRequest(BaseModel):
    """Request model for refining skill details with AI."""

    name: Optional[str] = Field(default=None, description="Current skill name")
    description: Optional[str] = Field(default=None, description="Current skill description")
    instructions: Optional[str] = Field(default=None, description="Current skill instructions")
    categories: Optional[List[str]] = Field(default=None, description="Current skill categories")
    toolkits: Optional[List[AssistantToolkit]] = Field(default=None, description="Current skill toolkits")
    refine_prompt: Optional[str] = Field(
        default=None,
        description="User's custom refinement instructions to guide the LLM",
    )
    llm_model: Optional[str] = Field(
        default_factory=lambda: llm_service.default_llm_model,
        description="Optional LLM model to use for refinement",
    )


class SkillGeneratorRequest(BaseModel):
    """Request model for generating skill details."""

    text: str = Field(..., description="User input text describing the desired skill")
    include_tools: bool = Field(True, description="Whether to include toolkit suggestions")
    llm_model: Optional[str] = Field(
        default_factory=lambda: llm_service.default_llm_model, description="Optional LLM model to use for generation"
    )


class SkillGeneratorResponse(BaseModel):
    """Response model for generated skill details."""

    name: str = Field(..., max_length=64, description="Generated skill name in kebab-case format")
    description: str = Field(
        ..., max_length=1000, description="Generated skill description using best-practices phrasing"
    )
    instructions: str = Field(
        ..., max_length=MAX_CONTENT_LENGTH, description="Generated skill instructions in Markdown format"
    )
    categories: List[str] = Field(
        default_factory=list, description="A list of the skill's primary areas or domain use cases (max 3)"
    )
    toolkits: List = Field(default=[], description="List of toolkits with their tools required by this skill")
