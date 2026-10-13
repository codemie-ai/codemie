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

"""Who runs a tool: the identity every record of a tool run is written under.

A direct tool call, a sandbox run and a script run (``ScriptRunContext`` extends this) all describe their run with a
``ToolRunContext``, so their series are cut by the same user, project, assistant, workflow and model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from codemie.core.constants import AGENT_NAME, ASSISTANT_ID, LLM_MODEL, PROJECT, USER_ID, USER_NAME, WORKFLOW_ID
from codemie.service.monitoring.run_ids import build_dimensions, workflow_id_label


#: The keys of a tool's metadata that say who runs it (besides ``user_id``, which the request supplies): the one list,
#: read by ``attribution_from_metadata`` and by whoever copies the identity from a tool (the LangGraph event adapter).
IDENTITY_METADATA_KEYS: tuple[str, ...] = (AGENT_NAME, LLM_MODEL, USER_NAME, PROJECT, ASSISTANT_ID, WORKFLOW_ID)


def _text(value: Any) -> str:
    return "" if value is None else str(getattr(value, "value", value))


@dataclass(frozen=True, kw_only=True)
class ToolRunContext:
    """Attribution of a run, captured in the request thread (other threads have no request contextvars).

    Empty when unknown (a workflow tool step has no assistant agent, so no name and no model). ``assistant_id`` is a
    stored assistant's id, or ``virtual`` for a virtual assistant; ``workflow_id`` is the workflow the run belongs to
    (empty elsewhere). ``user_id`` ``None`` leaves the user id out of the dimensions (the usage metrics get it from the
    request).
    """

    user_id: str | None = ""
    user_name: str = ""
    assistant_name: str = ""
    llm_model: str = ""
    project: str = ""
    assistant_id: str = ""
    workflow_id: str = ""

    def dimensions(self) -> dict[str, str]:
        """The one set of dimensions every record of the run is written under."""
        return build_dimensions(
            user_id=self.user_id,
            user_name=self.user_name,
            assistant_name=self.assistant_name,
            llm_model=self.llm_model,
            project=self.project,
            assistant_id=self.assistant_id,
            workflow_id=self.workflow_id,
        )

    def to_metadata(self) -> dict[str, str]:
        """The tool metadata this identity reads back from (``from_metadata(ctx.to_metadata())`` gives the same
        dimensions), for a producer that has the context but reports through the tool-metadata channel."""
        return {
            USER_ID: self.user_id or "",
            USER_NAME: self.user_name,
            AGENT_NAME: self.assistant_name,
            LLM_MODEL: self.llm_model,
            PROJECT: self.project,
            ASSISTANT_ID: self.assistant_id,
            WORKFLOW_ID: self.workflow_id,
        }

    @classmethod
    def attribution_from_metadata(
        cls,
        metadata: Mapping[str, Any] | None,
        user_id: str | None = None,
        *,
        with_user_id: bool = True,
        user_name: str | None = None,
        project_fallback: str | None = None,
        workflow_from_thread: bool = True,
    ) -> dict[str, Any]:
        """The fields of the context for a run started by a tool the agent configured (the tool's metadata carries the
        run's identity). The only place that reads that metadata.

        The project is the request's billing project, else the metadata's, else ``project_fallback``; the workflow id
        falls back to the one the workflow executor bound for this thread (``workflow_from_thread=False`` leaves it to
        the caller, who already has it).
        """
        from codemie.core.dependecies import get_current_project  # lazy: it pulls in the Kubernetes stack

        metadata = metadata or {}
        return {
            "user_id": (user_id or _text(metadata.get(USER_ID))) if with_user_id else None,
            "user_name": user_name or _text(metadata.get(USER_NAME)),
            "assistant_name": _text(metadata.get(AGENT_NAME)),
            "llm_model": _text(metadata.get(LLM_MODEL)),
            "project": get_current_project(fallback=_text(metadata.get(PROJECT)) or project_fallback or None),
            "assistant_id": _text(metadata.get(ASSISTANT_ID)),
            "workflow_id": _text(metadata.get(WORKFLOW_ID)) or (workflow_id_label() if workflow_from_thread else ""),
        }

    @classmethod
    def from_metadata(
        cls, metadata: Mapping[str, Any] | None, user_id: str | None = None, *, with_user_id: bool = True
    ) -> ToolRunContext:
        return cls(**cls.attribution_from_metadata(metadata, user_id, with_user_id=with_user_id))
