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


class ZephyrSquadConfig(CodeMieToolConfig):
    credential_type: CredentialTypes = Field(default=CredentialTypes.ZEPHYR_SQUAD, exclude=True, frozen=True)
    account_id: str
    access_key: str
    secret_key: str


class ZephyrSquadToolInput(BaseModel):
    method: str = Field(
        ...,
        description="""
        HTTP method to be used in an API call, e.x. GET or POST
        """.strip(),
    )
    relative_path: str = Field(
        ...,
        description="""
        Relative path excluding base url and /public/rest/api/1.0/config/, e.x.:
        - /cycle?expand=123&cloned123CycleId=123
        - /executions/search?executionId=123
        - ...
        """.strip(),
    )
    body: Optional[str] = Field(
        ...,
        description="""
        Optional JSON of input parameters of the method. MUST be string with valid JSON.
        """,
    )
    content_type: Optional[str] = Field(
        default="application/json",
        description="""
        Content type to pass in the header of the HTTP request. For ex. application/json, application/text, etc.
        """,
    )
