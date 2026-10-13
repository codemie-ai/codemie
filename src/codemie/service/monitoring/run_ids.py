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

"""The assistant id and workflow id that metrics of a run are written under.

One place decides them, so a direct tool call, a call made from a script and the savings estimates agree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from codemie.core.constants import ASSISTANT_ID, WORKFLOW_ID
from codemie.rest_api.security.workflow_context import get_current_workflow_id
from codemie.service.monitoring.metrics_constants import MetricsAttributes

if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import Assistant, VirtualAssistant

#: ``assistant_id`` of a virtual assistant: its own id is new on every run, so it would be a label of one value per run.
VIRTUAL_ASSISTANT_ID_LABEL = "virtual"


def assistant_id_label(assistant: Assistant | VirtualAssistant | None) -> str:
    """A stored assistant's id; ``virtual`` for a virtual one; empty without an assistant."""
    from codemie.rest_api.models.assistant import VirtualAssistant

    if assistant is None:
        return ""
    if isinstance(assistant, VirtualAssistant):
        return VIRTUAL_ASSISTANT_ID_LABEL
    return assistant.id or ""


def workflow_id_label() -> str:
    """The workflow being executed; empty outside a workflow run.

    Read from the context variable the workflow executor binds for its own thread, so no client input can set it.
    """
    return get_current_workflow_id() or ""


def run_id_metadata(assistant: Assistant | VirtualAssistant | None) -> dict[str, Any]:
    """The two ids as tool metadata. Both keys are always present, so request metadata cannot supply them."""
    return {ASSISTANT_ID: assistant_id_label(assistant), WORKFLOW_ID: workflow_id_label()}


def build_dimensions(
    *,
    user_id: str | None,
    user_name: str,
    assistant_name: str,
    llm_model: str,
    project: str,
    assistant_id: str = "",
    workflow_id: str = "",
) -> dict[str, str]:
    """The one set of dimensions every record of a run is written under: who ran it, where and with which model.

    ``user_id`` is left out when ``None`` (the usage metrics get it from the request instead); the assistant and
    workflow ids are written only when the run has them.
    """
    dimensions: dict[str, str] = {
        MetricsAttributes.ASSISTANT_NAME: assistant_name,
        MetricsAttributes.LLM_MODEL: llm_model,
        MetricsAttributes.PROJECT: project,
        MetricsAttributes.USER_NAME: user_name,
    }
    if user_id is not None:
        dimensions[MetricsAttributes.USER_ID] = user_id
    if assistant_id:
        dimensions[MetricsAttributes.ASSISTANT_ID] = assistant_id
    if workflow_id:
        dimensions[MetricsAttributes.WORKFLOW_ID] = workflow_id
    return dimensions
