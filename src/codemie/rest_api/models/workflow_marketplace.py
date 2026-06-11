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

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from codemie.rest_api.models.assistant import InlineCredential

_CategoryId = Annotated[str, Field(min_length=1)]


class PublishWorkflowToMarketplaceRequest(BaseModel):
    categories: list[_CategoryId] = Field(min_length=1, max_length=3)

    @field_validator("categories")
    @classmethod
    def no_duplicate_categories(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("category IDs must be unique")
        return v


class WorkflowPublishValidationResponse(BaseModel):
    """Response model for workflow publish validation."""

    message: str
    inline_credentials: list[InlineCredential]
    workflow_id: str
