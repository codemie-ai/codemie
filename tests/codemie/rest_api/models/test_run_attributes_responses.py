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

"""The run read endpoints return the stored attributes: their response models list fields explicitly."""

import pytest

from codemie.core.workflow_models.workflow_execution import WorkflowExecution, WorkflowExecutionResponse
from codemie.rest_api.models.conversation import Conversation, ConversationResponse

ATTRIBUTES = {"com.epam.gen_ai.service.work_item.id": "ABC-123"}


@pytest.mark.parametrize("attributes", [ATTRIBUTES, None])
def test_conversation_response_carries_attributes(attributes):
    conversation = Conversation(id="conv-1", conversation_id="conv-1", attributes=attributes)

    assert ConversationResponse.model_validate(conversation).attributes == attributes


@pytest.mark.parametrize("attributes", [ATTRIBUTES, None])
def test_workflow_execution_response_carries_attributes(attributes):
    execution = WorkflowExecution(workflow_id="wf-1", execution_id="exec-1", attributes=attributes)

    assert WorkflowExecutionResponse(**execution.model_dump()).attributes == attributes
