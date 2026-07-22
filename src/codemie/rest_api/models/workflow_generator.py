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

from typing import Optional

from pydantic import BaseModel

from codemie.core.workflow_models.workflow_models import CreateWorkflowRequest


class WorkflowGeneratorRequest(BaseModel):
    text: str
    llm_model: Optional[str] = None
    persist: bool = False
    guardrail_ids: Optional[list[str]] = None


class WorkflowGeneratorResponse(BaseModel):
    workflow_config: CreateWorkflowRequest
    workflow_id: Optional[str] = None


class WorkflowRefineRequest(BaseModel):
    yaml_config: str
    refine_prompt: Optional[str] = None
    llm_model: Optional[str] = None
    project: Optional[str] = None


class WorkflowRefineResponse(BaseModel):
    yaml_config: str
