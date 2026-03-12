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

from typing import Optional, Dict, Any

from pydantic import model_validator, Field

from codemie_tools.base.models import CodeMieToolConfig, FileConfigMixin, CredentialTypes


class ConfluenceConfig(CodeMieToolConfig, FileConfigMixin):
    credential_type: CredentialTypes = Field(default=CredentialTypes.CONFLUENCE, exclude=True, frozen=True)
    url: str = Field(
        default="",
        description="URL to your Confluence instance, e.g. http://confluence.example.com/",
        json_schema_extra={"placeholder": "https://confluence.example.com/"},
    )
    username: Optional[str] = Field(
        default=None,
        description="Username/email for Confluence (Required for Confluence Cloud)",
        json_schema_extra={"placeholder": "user@example.com"},
    )
    token: str = Field(
        default="",
        description="API Access Token/ApiKey for authentication.",
        json_schema_extra={
            "placeholder": "Token/ApiKey",
            "sensitive": True,
            "help": "https://confluence.atlassian.com/enterprise/using-personal-access-tokens-1026032365.html",
        },
    )
    cloud: Optional[bool] = Field(
        default=False, description="Is this a Confluence Cloud instance? Toggle on if using Atlassian Cloud"
    )

    @model_validator(mode='before')
    def validate_config(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if "is_cloud" in values:
            # Special handling for creating model from UI. is_cloud field is passed and should be passed to cloud
            values["cloud"] = values.pop("is_cloud")
        return values
