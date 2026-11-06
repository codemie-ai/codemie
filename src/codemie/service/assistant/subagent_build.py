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

"""What a sub-assistant build passes down and returns up (STR-4, AR-5, spec rule 6).

The channel a root is built for, the policy resolved once per assistant, the build context handed to each
sub-assistant and the handle a build returns. ``assistant_engine_builder`` builds the trees from these.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from codemie.core.models import ToolCallPolicy


class BuildChannel(str, Enum):
    """The channel a root agent is built for; it decides whether the run's pause can be resumed."""

    STREAM = "stream"
    RESUME = "resume"
    HEDGED = "hedged"
    SYNC = "sync"
    BACKGROUND = "background"
    WORKFLOW = "workflow"
    A2A = "a2a"
    HEALTH_CHECK = "health_check"

    @property
    def can_resume_pause(self) -> bool:
        """True where the caller streams the agent and a paused run can be decided and resumed."""
        return self in (BuildChannel.STREAM, BuildChannel.RESUME, BuildChannel.HEDGED)


@dataclass(frozen=True)
class ResolvedToolPolicy:
    """The effective tool-call policy of one assistant, resolved once per build."""

    policy: ToolCallPolicy
    requires_confirmation: bool


@dataclass(frozen=True)
class SubagentBuildContext:
    """What a build passes down to the sub-assistants it builds (STR-4).

    ``can_resume_pause`` says whether the run can be resumed (a resumable channel with history on). Where it
    cannot, every gate of the tree answers the calls needing confirmation as unavailable instead of pausing.
    """

    can_resume_pause: bool = False

    @classmethod
    def root(cls, channel: BuildChannel | None = None, save_history: bool = True) -> SubagentBuildContext:
        """Root context of a build for ``channel``; a run without a channel or with history off cannot resume."""
        if channel is None:
            return cls()
        return cls(can_resume_pause=channel.can_resume_pause and save_history)

    def child(self) -> SubagentBuildContext:
        """Context for a sub-assistant of this one."""
        return SubagentBuildContext(can_resume_pause=self.can_resume_pause)


@dataclass(frozen=True)
class SubagentHandle:
    """What a sub-assistant build returns up to its parent.

    ``agent`` is the built agent instance (``LangGraphAgent`` for LangGraph builds); ``executor`` is its
    compiled graph, which the parent hands to the supervisor.
    """

    agent: Any
    executor: Any
    tree_requires_confirmation: bool
    name: str = ""
    description: str = ""


def built_subagent_descriptions(handles: list[SubagentHandle], descriptions: dict[str, str]) -> dict[str, str]:
    """Descriptions of the built sub-assistants only, keyed by the name each was built under (AR-5)."""
    return {
        handle.name: descriptions.get(handle.name) or handle.description or f"Assistant {handle.name}"
        for handle in handles
        if handle.name
    }
