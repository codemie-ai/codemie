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

"""Utility functions for plugin tools."""


def cleanup_plugin_tool_name(name: str) -> str:
    """
    Clean up plugin tool name by removing any suffix after the last underscore.

    Plugin tools often have dynamic suffixes (e.g., _tool_abc, _tool_xyz) that are
    generated at runtime. This function removes those suffixes to get the base tool name.

    Args:
        name: The tool name to clean up

    Returns:
        The cleaned tool name without the suffix

    Example:
        >>> cleanup_plugin_tool_name("_some_tool_xyz")
        "_some_tool"
        >>> cleanup_plugin_tool_name("simple_tool")
        "simple_tool"
    """
    if not name:
        return name
    if "_" in name:
        return name.rsplit("_", 1)[0]
    return name
