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

from typing import Optional
from pydantic import BaseModel, Field
from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes


class ZephyrConfig(CodeMieToolConfig):
    credential_type: CredentialTypes = Field(default=CredentialTypes.ZEPHYR_SCALE, exclude=True, frozen=True)
    url: str
    token: str


class ZephyrToolInput(BaseModel):
    entity_str: str = Field(
        ...,
        description="""
        The Zephyr entity name.
        Can be one of the (test_cases, test_cycles, test_plans, test_executions,
        folders, statuses, priorities, environments, projects, links, issue_links,
        automations, healthcheck). Required parameter.
        """.strip(),
    )
    method_str: str = Field(
        ...,
        description="""
        Required parameter: The method that should be executed on the entity.
        Always use "dir" as value before you run the real method to get the list of available methods.
        **Important:** If you receive an error that object has no attribute then use "dir".
        """,
    )
    body: Optional[str] = Field(
        ...,
        description="""
        Optional JSON of input parameters of the method. MUST be string with valid JSON.
        """,
    )
