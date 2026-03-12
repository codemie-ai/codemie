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

"""Integration layer for plugin enterprise features.

This module provides integration functions that bridge core codemie package
to the enterprise plugin system. It handles:
- Service lifecycle management (dependencies.py)
- Tool wrapping and protocol definitions (enterprise_tool.py)
"""

from .dependencies import (
    PluginToolkitUI,
    get_global_plugin_service,
    get_plugin_service_or_none,
    get_plugin_toolkit_ui_info,
    get_plugin_tools_for_assistant,
    initialize_plugin_from_config,
    is_plugin_enabled,
    set_global_plugin_service,
)
from codemie.enterprise.enterprise_tool import (
    EnterpriseTool,
    wrap_enterprise_plugin_tool,  # Backward compatibility
    wrap_enterprise_tool,
)

__all__ = [
    # Service management
    "is_plugin_enabled",
    "initialize_plugin_from_config",
    "set_global_plugin_service",
    "get_global_plugin_service",
    "get_plugin_service_or_none",
    "get_plugin_toolkit_ui_info",
    "get_plugin_tools_for_assistant",
    # Tool wrapping
    "wrap_enterprise_tool",
    "wrap_enterprise_plugin_tool",  # Backward compatibility
    # Protocol
    "EnterpriseTool",
    # UI models
    "PluginToolkitUI",
]
