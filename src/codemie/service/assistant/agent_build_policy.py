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

"""The policy side of one agent build (STR-3, STR-4): the resolved tool-call policy, its build context and handle.

``AssistantService.build_agent_handle`` resolves the policy here once per assistant and wraps the built agent in a
``SubagentHandle``.
"""

from __future__ import annotations

from typing import Any

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.service.assistant.assistant_engine_builder import (
    BuildChannel,
    LangGraphAssistantBuilder,
    ResolvedToolPolicy,
    SubagentBuildContext,
    SubagentHandle,
)


def resolve_build_policy(
    assistant: Assistant,
    request: AssistantChatRequest,
    build_context: SubagentBuildContext | None,
    channel: BuildChannel | None,
) -> tuple[ResolvedToolPolicy, SubagentBuildContext]:
    """The assistant's effective tool-call policy and its build context (a root context for ``channel``)."""
    resolved_policy = LangGraphAssistantBuilder.resolve_tool_policy(assistant, request, allow_tool_confirmation=True)
    if build_context is None:
        build_context = SubagentBuildContext.root(channel, save_history=_turn_entry_saved(request))
    return resolved_policy, build_context


def _turn_entry_saved(request: AssistantChatRequest) -> bool:
    """The turn has a saved history entry for a pause to point at (history on and the placeholder save done).

    Without one (history off, or the placeholder save failed) the run cannot be resumed, so its gates answer the
    calls needing confirmation as unavailable instead of pausing (rule 9).
    """
    return bool(request.save_history) and request.history_index is not None


def agent_handle(agent: Any, tree_requires_confirmation: bool, *, assistant: Assistant) -> SubagentHandle:
    """The built agent with its tree's tool-confirmation flag."""
    return SubagentHandle(
        agent=agent,
        executor=getattr(agent, "agent_executor", None),
        tree_requires_confirmation=tree_requires_confirmation,
        name=assistant.name or "",
        description=assistant.description or "",
    )
