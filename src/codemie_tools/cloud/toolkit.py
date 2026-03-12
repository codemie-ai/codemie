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

from typing import List

from codemie_tools.base.base_toolkit import DiscoverableToolkit
from codemie_tools.base.models import ToolKit, ToolSet, Tool
from .aws.tools import GenericAWSTool
from .aws.tools_vars import AWS_TOOL
from .azure.tools import GenericAzureTool
from .azure.tools_vars import AZURE_TOOL
from .gcp.tools import GenericGCPTool
from .gcp.tools_vars import GCP_TOOL
from .kubernetes.tools import GenericKubernetesTool
from .kubernetes.tools_vars import KUBERNETES_TOOL


class CloudToolkitUI(ToolKit):
    """UI definition for Cloud Toolkit."""

    toolkit: ToolSet = ToolSet.CLOUD
    tools: List[Tool] = [
        Tool.from_metadata(AWS_TOOL, tool_class=GenericAWSTool),
        Tool.from_metadata(AZURE_TOOL, tool_class=GenericAzureTool),
        Tool.from_metadata(GCP_TOOL, tool_class=GenericGCPTool),
        Tool.from_metadata(KUBERNETES_TOOL, tool_class=GenericKubernetesTool),
    ]
    label: str = "Cloud"
    description: str = "Comprehensive toolkit for cloud platform integrations (AWS, Azure, GCP, Kubernetes)"


class CloudToolkit(DiscoverableToolkit):
    """
    Toolkit for cloud platform integrations.

    Provides tools for interacting with:
    - AWS (Amazon Web Services) via boto3
    - Azure (Microsoft Azure) via REST API
    - GCP (Google Cloud Platform) via REST API
    - Kubernetes clusters via Kubernetes API

    Each tool can be configured independently and tools are only enabled when
    their respective configurations are provided.
    """

    @classmethod
    def get_definition(cls):
        """Return toolkit definition for UI autodiscovery."""
        return CloudToolkitUI()
