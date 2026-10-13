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

"""The assistant id and workflow id every metric of a run is written under."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant, VirtualAssistant
from codemie.rest_api.security.workflow_context import set_current_workflow_id
from codemie.service.monitoring.run_ids import assistant_id_label, run_id_metadata, workflow_id_label


def _assistant(assistant_id: str = "asst-1") -> Assistant:
    return Assistant(id=assistant_id, name="a", description="d", system_prompt="p", project="proj")


def _virtual() -> VirtualAssistant:
    return VirtualAssistant(name="v", description="d", system_prompt="p", project="proj")


def test_a_stored_assistant_is_labelled_with_its_id() -> None:
    assert assistant_id_label(_assistant()) == "asst-1"


def test_a_virtual_assistant_is_labelled_virtual_not_with_its_per_run_id() -> None:
    virtual = _virtual()
    virtual.id = "virtual_assistant_0b1c"

    assert assistant_id_label(virtual) == "virtual"


def test_no_assistant_has_no_id() -> None:
    assert assistant_id_label(None) == ""
    assert assistant_id_label(MagicMock(id=None)) == ""


@pytest.fixture
def in_workflow():
    set_current_workflow_id("wf-1")
    yield
    set_current_workflow_id(None)


def test_inside_a_workflow_the_label_is_the_bound_workflow_id(in_workflow) -> None:
    assert workflow_id_label() == "wf-1"


def test_outside_a_workflow_there_is_no_workflow_id() -> None:
    assert workflow_id_label() == ""


def test_the_workflow_id_a_client_sends_is_ignored() -> None:
    AssistantChatRequest.model_validate({"text": "t", "workflow_id": "forged", "workflowId": "forged"})

    assert workflow_id_label() == ""


def test_the_metadata_always_has_both_keys(in_workflow) -> None:
    assert run_id_metadata(_assistant()) == {"assistant_id": "asst-1", "workflow_id": "wf-1"}
    set_current_workflow_id(None)
    assert run_id_metadata(None) == {"assistant_id": "", "workflow_id": ""}
