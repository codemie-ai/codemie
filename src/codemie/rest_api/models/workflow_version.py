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

from pydantic import BaseModel, Field

from codemie.core.workflow_models.workflow_config import YamlConfigHistory

CHANGE_NOTES_MAX_LENGTH = 1000


class WorkflowVersionHistoryResponse(BaseModel):
    revisions: list[YamlConfigHistory]
    total_revisions: int
    workflow_name: str
    workflow_id: str


class WorkflowRollbackRequest(BaseModel):
    revision_index: int
    change_notes: str | None = Field(default=None, max_length=CHANGE_NOTES_MAX_LENGTH)
