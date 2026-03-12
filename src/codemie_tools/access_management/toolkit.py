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

import logging
from typing import List

from codemie_tools.access_management.keycloak.tools import KeycloakTool
from codemie_tools.access_management.keycloak.tools_vars import KEYCLOAK_TOOL
from codemie_tools.base.base_toolkit import DiscoverableToolkit
from codemie_tools.base.models import ToolKit, ToolSet, Tool

logger = logging.getLogger(__name__)


class AccessManagementToolkitUI(ToolKit):
    """UI definition for Access Management Toolkit."""

    toolkit: ToolSet = ToolSet.ACCESS_MANAGEMENT
    tools: List[Tool] = [
        Tool.from_metadata(KEYCLOAK_TOOL, tool_class=KeycloakTool),
    ]
    label: str = "Access Management"
    description: str = "Comprehensive toolkit for identity and access management integrations"


class AccessManagementToolkit(DiscoverableToolkit):
    """Toolkit for Access Management integrations (Keycloak, IAM, etc.)."""

    @classmethod
    def get_definition(cls):
        """Return toolkit definition for UI autodiscovery."""
        return AccessManagementToolkitUI()
