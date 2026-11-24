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

"""Configuration models for Headroom context compression."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel


class HeadroomStrategy(str, Enum):
    LOSSLESS = "lossless"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


class HeadroomConfig(BaseModel):
    """Stored on assistants and workflows. All fields required, with defaults."""

    enabled: bool = False
    compress_user_messages: bool = True
    compress_tool_outputs: bool = True
    strategy: HeadroomStrategy = HeadroomStrategy.BALANCED


class HeadroomOverride(BaseModel):
    """Workflow node override. Unset fields inherit."""

    enabled: bool | None = None
    compress_user_messages: bool | None = None
    compress_tool_outputs: bool | None = None
    strategy: HeadroomStrategy | None = None


@dataclass(frozen=True)
class ResolvedHeadroomConfig:
    compress_user_messages: bool
    compress_tool_outputs: bool
    strategy: HeadroomStrategy
