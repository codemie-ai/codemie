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

"""Glue between the tool registry and script tool calls: skip building tools that are known to be excluded."""

from __future__ import annotations

from codemie_tools.base.tool_registry import ensure_loaded, is_known_excluded_from_script_calls


def is_known_excluded(name: str) -> bool:
    """True when every tool class carrying ``name`` is not script-callable, so building the tool would be wasted."""
    ensure_loaded()
    return is_known_excluded_from_script_calls(name)
