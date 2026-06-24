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

from unittest.mock import MagicMock, patch

from codemie.rest_api.models.assistant import AssistantType
from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService

_ORCHESTRATION_MOD = "codemie.service.aws_bedrock.bedrock_orchestration_service"


def _agentcore_assistant():
    a = MagicMock()
    a.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME
    a.bedrock_agentcore_runtime = MagicMock()
    a.bedrock_agentcore_runtime.runtime_arn = "arn:test"
    a.bedrock = None
    return a


@patch(f"{_ORCHESTRATION_MOD}.BedrockAgentCoreRuntimeService.invoke_agentcore_runtime")
def test_orchestrator_passes_chat_history_to_agentcore(mock_invoke):
    mock_invoke.return_value = {"output": "ok", "thoughts": [], "time_elapsed": 0.1}
    history = [MagicMock()]

    BedrockOrchestratorService.invoke_bedrock_assistant(
        assistant=_agentcore_assistant(),
        input_text="hello",
        conversation_id="conv-1",
        chat_history=history,
    )

    _, kwargs = mock_invoke.call_args
    assert kwargs["history"] == history


@patch(f"{_ORCHESTRATION_MOD}.BedrockAgentCoreRuntimeService.invoke_agentcore_runtime")
def test_orchestrator_passes_none_history_when_absent(mock_invoke):
    mock_invoke.return_value = {"output": "ok", "thoughts": [], "time_elapsed": 0.1}

    BedrockOrchestratorService.invoke_bedrock_assistant(
        assistant=_agentcore_assistant(),
        input_text="hello",
        conversation_id="conv-1",
    )

    _, kwargs = mock_invoke.call_args
    assert kwargs["history"] is None
