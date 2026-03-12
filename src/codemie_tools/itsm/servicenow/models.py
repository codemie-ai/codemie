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

from typing import Union, Dict, Any

from pydantic import BaseModel, Field

from codemie_tools.base.models import CodeMieToolConfig, RequiredField, CredentialTypes


class ServiceNowConfig(CodeMieToolConfig):
    credential_type: CredentialTypes = Field(default=CredentialTypes.SERVICENOW, exclude=True, frozen=True)
    url: str = RequiredField(
        description="ServiceNow instance URL",
        json_schema_extra={"placeholder": "https://your-instance.service-now.com"},
    )
    api_key: str = RequiredField(
        description="ServiceNow API key for authentication",
        json_schema_extra={
            "sensitive": True,
            "help": "https://docs.servicenow.com/bundle/tokyo-platform-administration/page/administer/security/task/t_CreateAnOAuthAPIEndpointForExternalClients.html",
        },
    )


class ServiceNowInput(BaseModel):
    method: str = Field(
        ...,
        description="""
        Required parameter: The HTTP method to use for the request (GET, POST, PUT, DELETE, etc.). Required parameter.
        """,
    )
    table: str = Field(
        ...,
        description="""
        Required parameter: The table name to use in API.
        This parameter will be used to form request url, e.g. /api/now/table/{table}
        """,
    )
    sys_id: str = Field(
        default="",
        description="""
        Optional parameter: Sys_id of the record to use in API. Should only be supplied when working with individual record.
        This parameter will be used to form request url, e.g. /api/now/table/{table}/{sys_id}
        """,
    )
    params: Union[str, Dict[str, Any], None] = Field(
        default=None,
        description="""
        Optional parameter: Parameters to be sent in request body or query params.

        RECOMMENDED: Provide as a dictionary (dict) - this avoids JSON escaping issues with quotes and special characters.
        LEGACY: Can also accept a JSON string, but this is error-prone for complex content.

        For search/read operations, you MUST always get "key", "summary", "status", "assignee", "issuetype" and
        set maxResult, until users ask explicitly for more fields.

        Dict format examples (RECOMMENDED):
        - Simple query: params={"sysparm_limit": 10, "sysparm_query": "active=true"}
        - With multiple fields: params={"sysparm_fields": "number,short_description,state", "sysparm_limit": 50}

        JSON string format (LEGACY - only use if dict not available):
        - Must properly escape all quotes and special characters
        """,
    )
    body: str = Field(
        default="",
        description="""
        Optional parameter: Body of JSON request to use with POST/PUT/PATCH methods.
        """,
    )
