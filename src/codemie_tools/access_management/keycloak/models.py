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

from pydantic import Field, BaseModel, model_validator

from codemie_tools.base.models import CredentialTypes, CodeMieToolConfig, RequiredField


class KeycloakConfig(CodeMieToolConfig):
    """Configuration for Keycloak integration."""

    credential_type: CredentialTypes = Field(default=CredentialTypes.KEYCLOAK, exclude=True, frozen=True)

    base_url: str = RequiredField(
        description="Base URL of the Keycloak server", json_schema_extra={"placeholder": "https://keycloak.example.com"}
    )

    realm: str = RequiredField(description="Keycloak realm name", json_schema_extra={"placeholder": "master"})

    client_id: str = RequiredField(
        description="Client ID for authentication", json_schema_extra={"placeholder": "admin-cli"}
    )

    client_secret: str = RequiredField(
        description="Client secret for authentication",
        json_schema_extra={
            "placeholder": "your_client_secret",
            "sensitive": True,
            "help": "https://www.keycloak.org/docs/latest/server_admin/#_clients",
        },
    )

    timeout: Optional[int] = Field(default=30, description="Request timeout in seconds")

    @model_validator(mode='before')
    def handle_url_compatibility(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """Handle backward compatibility: map 'url' to 'base_url' if present."""
        if isinstance(values, dict):
            # If 'url' is present but 'base_url' is not, use 'url' as 'base_url'
            if 'url' in values and 'base_url' not in values:
                values['base_url'] = values.pop('url')
            # If both are present, prefer 'base_url' and remove 'url'
            elif 'url' in values and 'base_url' in values:
                values.pop('url')
        return values


class KeycloakToolInput(BaseModel):
    """Input schema for Keycloak tool operations."""

    method: str = Field(
        ..., description="The HTTP method to use for the request (GET, POST, PUT, DELETE, etc.). Required parameter."
    )

    relative_url: str = Field(
        ...,
        description="""
        The relative URL of the Keycloak Admin API to call, e.g. '/users'. Required parameter.
        Must start with '/'. In case of GET method, you MUST include query parameters in the URL.
        """,
    )

    params: Optional[str] = Field(
        default=None,
        description="Optional string dictionary of parameters to be sent in the query string or request body.",
    )
