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
Response models for user reactions endpoint.
"""

from datetime import datetime
from enum import Enum
from typing import Literal, Union
from pydantic import BaseModel, Field


class ResourceType(str, Enum):
    """Types of resources that can receive reactions and user interactions"""

    ASSISTANTS = "assistants"
    SKILLS = "skills"
    ALL = "all"


class BaseReactionResponse(BaseModel):
    """Base model for reaction responses"""

    resource_id: str = Field(serialization_alias="resourceId")
    reaction: str
    reaction_at: datetime = Field(serialization_alias="reactionAt")


class MinimalAssistantReactionResponse(BaseReactionResponse):
    """Minimal assistant reaction response without details"""

    resource_type: Literal["assistant"] = Field(default="assistant", serialization_alias="resourceType")


class MinimalSkillReactionResponse(BaseReactionResponse):
    """Minimal skill reaction response without details"""

    resource_type: Literal["skill"] = Field(default="skill", serialization_alias="resourceType")


class AssistantReactionResponse(BaseReactionResponse):
    """Assistant reaction with full details"""

    resource_type: Literal["assistant"] = Field(default="assistant", serialization_alias="resourceType")
    name: str
    description: str
    project: str
    slug: str
    icon: str | None = None


class SkillReactionResponse(BaseReactionResponse):
    """Skill reaction with full details"""

    resource_type: Literal["skill"] = Field(default="skill", serialization_alias="resourceType")
    name: str
    description: str
    project: str
    visibility: str  # SkillVisibility value
    categories: list[str]  # SkillCategory values


class UserReactionsResponse(BaseModel):
    """Container for user reactions list"""

    items: list[
        Union[
            AssistantReactionResponse,
            SkillReactionResponse,
            MinimalAssistantReactionResponse,
            MinimalSkillReactionResponse,
        ]
    ]
