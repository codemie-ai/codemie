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
from .models import KubernetesConfig

KUBERNETES_TOOL = ToolMetadata(
    name="Kubernetes",
    description="""
    Tool for Create, Read, Update, and Delete operations on Kubernetes resources.
    Provides access to the Kubernetes API for cluster and resource management.

    Capabilities:
    - Access to Kubernetes API for all resource types
    - CRUD operations on pods, deployments, services, namespaces, etc.
    - Query cluster state and resource status
    - Create and manage Kubernetes resources
    - Support for all Kubernetes API endpoints

    Usage:
    Provide HTTP method, relative API path (suburl), and optional body/headers.
    The tool handles authentication with Bearer token automatically.

    Important:
    - suburl must start with a forward slash (/)
    - Do not include query parameters in suburl (provide them in body/headers)
    - Use appropriate API version in the path (e.g., /api/v1, /apis/apps/v1)

    Examples:
    - List pods in default namespace:
      method="GET", suburl="/api/v1/namespaces/default/pods"

    - Create a deployment:
      method="POST",
      suburl="/apis/apps/v1/namespaces/default/deployments",
      body={"metadata": {...}, "spec": {...}}

    - Delete a service:
      method="DELETE", suburl="/api/v1/namespaces/default/services/my-service"

    - Get cluster version:
      method="GET", suburl="/version"
    """.strip(),
    label="Kubernetes",
    user_description="""
    Provides access to the Kubernetes (k8s) API, allowing for Create, Read, Update, and Delete
    operations on Kubernetes resources. This tool enables the AI assistant to interact with and
    manage Kubernetes clusters and their resources.

    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Kubernetes API Server URL
    2. Kubernetes Bearer Token

    Usage Note:
    Use this tool when you need to perform operations on Kubernetes resources such as pods,
    services, deployments, etc. Often used in combination with other Cloud tools for comprehensive
    cluster management and application deployment.
    """.strip(),
    settings_config=True,
    config_class=KubernetesConfig,
)
