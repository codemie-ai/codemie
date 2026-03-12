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

from typing import Optional, Dict, Any, Union

from pydantic import BaseModel, Field, model_validator

from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes, RequiredField


class AzureConfig(CodeMieToolConfig):
    """Configuration for Azure integration."""

    credential_type: CredentialTypes = Field(default=CredentialTypes.AZURE, exclude=True, frozen=True)

    subscription_id: str = RequiredField(
        description="Azure Subscription ID", json_schema_extra={"placeholder": "12345678-1234-1234-1234-123456789012"}
    )

    tenant_id: str = RequiredField(
        description="Azure Tenant ID (Directory ID)",
        json_schema_extra={"placeholder": "12345678-1234-1234-1234-123456789012"},
    )

    client_id: str = RequiredField(
        description="Azure Client ID (Application ID)",
        json_schema_extra={
            "placeholder": "12345678-1234-1234-1234-123456789012",
            "help": "https://docs.microsoft.com/en-us/azure/active-directory/develop/howto-create-service-principal-portal",
        },
    )

    client_secret: str = RequiredField(
        description="Azure Client Secret",
        json_schema_extra={
            "placeholder": "your_client_secret",
            "sensitive": True,
            "help": "https://docs.microsoft.com/en-us/azure/active-directory/develop/howto-create-service-principal-portal",
        },
    )

    @model_validator(mode='before')
    def validate_config(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """Support legacy credential keys for backward compatibility."""
        # Map legacy keys to new keys
        if "azure_subscription_id" in values:
            values["subscription_id"] = values.pop("azure_subscription_id")
        if "azure_tenant_id" in values:
            values["tenant_id"] = values.pop("azure_tenant_id")
        if "azure_client_id" in values:
            values["client_id"] = values.pop("azure_client_id")
        if "azure_client_secret" in values:
            values["client_secret"] = values.pop("azure_client_secret")

        return values


class AzureInput(BaseModel):
    """Input schema for Azure tool operations."""

    method: str = Field(
        description="""
        The HTTP method to use for the request.
        Supported methods: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS

        Example: "GET", "POST", "PUT"
        """
    )

    url: str = Field(
        description="""
        Full URL for the Azure Resource Management REST API request.
        Must include protocol (https://) and full qualified domain name.

        Common Azure endpoints:
        - Azure Resource Manager: https://management.azure.com
        - Microsoft Graph: https://graph.microsoft.com
        - Azure Storage: https://<account>.blob.core.windows.net

        Example: "https://management.azure.com/subscriptions/{subscriptionId}/resourcegroups?api-version=2021-04-01"
        """
    )

    optional_args: Optional[Union[str, Dict[str, Any]]] = Field(
        default=None,
        description="""
        Optional JSON object containing additional request parameters.
        Possible keys: 'data', 'headers', 'files', 'params'

        Example: {"data": {"location": "eastus"}, "headers": {"Content-Type": "application/json"}}

        Note: Must be valid JSON. No comments allowed.
        """,
    )

    scope: Optional[str] = Field(
        default="https://management.azure.com/.default",
        description="""
        OAuth scope to request when obtaining an access token.

        Common scopes:
        - https://management.azure.com/.default (Azure Resource Manager API - default)
        - https://graph.microsoft.com/.default (Microsoft Graph API)
        - https://database.windows.net/.default (Azure SQL Database)
        - https://storage.azure.com/.default (Azure Storage)

        Use the appropriate scope that matches the Azure service you're accessing.
        """,
    )
