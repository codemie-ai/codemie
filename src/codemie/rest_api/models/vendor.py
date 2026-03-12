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

from enum import Enum
from pydantic import BaseModel


class Vendor(str, Enum):
    AWS = "aws"


class Entities(str, Enum):
    AWS_GUARDRAILS = "guardrails"
    AWS_AGENTS = "assistants"
    AWS_FLOWS = "workflows"
    AWS_KNOWLEDGE_BASES = "knowledgebases"
    AWS_AGENTCORE_RUNTIMES = "agentcore-runtimes"


class ImportEntityBase(BaseModel):
    setting_id: str  # Integration (setting/credentials) unique identifier


class ImportAgent(ImportEntityBase):
    id: str
    agentAliasId: str


class ImportAgentcoreRuntime(ImportEntityBase):
    id: str
    agentcoreRuntimeEndpointName: str
    invocation_json: str


class ImportKnowledgeBase(ImportEntityBase):
    id: str


class ImportGuardrail(ImportEntityBase):
    id: str
    version: str


class ImportFlow(ImportEntityBase):
    id: str
    flowAliasId: str
