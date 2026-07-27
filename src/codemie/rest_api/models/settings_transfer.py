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

"""Request and response models for moving or copying integrations between projects."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from codemie_tools.base.models import CredentialTypes


class TransferMode(str, Enum):
    """Operation mode for a project integration transfer."""

    MOVE = "move"
    COPY = "copy"


class TransferSettingsRequest(BaseModel):
    source_project_name: str = Field(min_length=1)
    target_project_name: str = Field(min_length=1)
    mode: TransferMode


class TransferItem(BaseModel):
    id: str
    alias: str
    credential_type: CredentialTypes


class TransferSettingsResponse(BaseModel):
    message: str
    source_project_name: str
    target_project_name: str
    mode: TransferMode
    transferred_count: int
    transferred: list[TransferItem] = Field(default_factory=list)
    skipped_count: int = 0
    skipped: list[TransferItem] = Field(default_factory=list)
