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

from codemie_tools.base.models import ToolMetadata
from .models import AzureConfig

AZURE_TOOL = ToolMetadata(
    name="Azure",
    description="""
    Tool for interacting with Azure REST API.
    All credentials and authentication handling is done automatically by the tool.

    Capabilities:
    - Access to Azure Resource Manager REST API
    - Manage Azure resources (VMs, Storage, Networking, etc.)
    - Execute any Azure REST API operation
    - Support for multiple Azure services (management, graph, storage, database)
    - Automatic OAuth token management

    Usage:
    Provide HTTP method, full URL, and optional request arguments.
    The tool handles authentication and token refresh automatically.
    """.strip(),
    label="Azure",
    user_description="""
    Provides access to the Azure API, allowing for management and interaction with various Azure
    resources and services. This tool enables the AI assistant to perform a wide range of operations
    within the Azure cloud environment.

    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Alias (A friendly name for the Azure account)
    2. Subscription ID
    3. Tenant ID
    4. Client ID
    5. Client Secret

    Usage Note:
    Use this tool when you need to manage Azure resources, deploy services, or retrieve information
    from your Azure environment.
    """.strip(),
    settings_config=True,
    config_class=AzureConfig,
)
