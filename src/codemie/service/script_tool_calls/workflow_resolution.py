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

"""Resolve a platform tool by name for a workflow-scoped script run."""

from __future__ import annotations

from langchain_core.tools import BaseTool

from codemie.rest_api.security.user import User


def resolve_workflow_tool(user: User, project: str, name: str) -> BaseTool:
    """Build the tool ``name`` for ``user`` in ``project`` the way a workflow tool node does.

    Raises ``ValueError`` when no such tool exists. The temporary virtual assistant is always removed.
    Services are imported lazily: they import the toolkit stack, which imports this package.
    """
    from codemie.service.assistant import VirtualAssistantService
    from codemie.service.tools import ToolkitService, ToolsService

    assistant = VirtualAssistantService.create_from_tool_invocation(name, user, project)
    try:
        return ToolsService.find_tool_by_invoke_request(
            name, ToolkitService.get_toolkit_methods(), assistant, user, project
        )
    finally:
        VirtualAssistantService.delete(assistant.id)
