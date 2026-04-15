# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""
Name resolution protocol and implementations for agent callbacks.

This module provides an abstraction layer between callbacks and agent implementations,
allowing callbacks to resolve assistant names without direct coupling to specific agent types.
"""

from typing import Protocol

from codemie.core.constants import SUPERVISOR_HANDOFF_TOOL_PREFIX


class NameResolver(Protocol):
    """
    Protocol for resolving truncated assistant names to their original names.

    This protocol decouples callbacks from specific agent implementations,
    allowing any class to provide name resolution functionality.
    """

    def get_original_sub_assistant_name(self, truncated_name: str) -> str:
        """
        Retrieve the original sub-assistant name from a truncated name.

        Args:
            truncated_name: The truncated assistant name (without tool prefix)

        Returns:
            The original assistant name if a mapping exists, otherwise the truncated name
        """
        ...


class NoOpNameResolver:
    """
    A no-op name resolver that returns the input name unchanged.

    Used when no name resolution is needed (e.g., non-supervisor agents).
    """

    def get_original_sub_assistant_name(self, truncated_name: str) -> str:
        """Return the input name unchanged."""
        return truncated_name


def resolve_tool_display_name(tool_name: str, name_resolver: NameResolver) -> str:
    """
    Resolve the display name for a tool, handling handoff tool name mapping.

    This utility function checks if a tool is a supervisor handoff tool
    and resolves its truncated name back to the original sub-assistant name for UI display.
    All names are then formatted by replacing underscores with spaces and title-casing.

    Args:
        tool_name: The tool name from the callback (may be truncated for handoff tools)
        name_resolver: The name resolver to use for mapping truncated names to originals

    Returns:
        The display name to show in the UI (formatted tool name or resolved sub-assistant name)
    """
    display_name = tool_name

    # Check if this is a handoff tool by matching the full "transfer_to_" prefix
    prefix_with_separator = f"{SUPERVISOR_HANDOFF_TOOL_PREFIX}_"
    if tool_name.startswith(prefix_with_separator):
        # Extract the truncated agent name — the part after "transfer_to_"
        truncated_name = tool_name[len(prefix_with_separator) :]
        display_name = name_resolver.get_original_sub_assistant_name(truncated_name)

    # Replace underscores with spaces and title-case for display
    display_name = display_name.replace('_', ' ').title()

    return display_name
