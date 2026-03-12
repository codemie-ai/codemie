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

import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4
from typing import Any, Dict, Union

from codemie.agents.callbacks.monitoring_callback import MonitoringCallback
from codemie.service.monitoring.agent_monitoring_service import AgentMonitoringService

DUMMY_TOOL_NAME = "dummy_tool"

RUN_ID = uuid4()


@pytest.fixture
def callback() -> MonitoringCallback:
    return MonitoringCallback()


@pytest.fixture
def tool_run_map_entry() -> Dict[str, Any]:
    return {"name": DUMMY_TOOL_NAME, "metadata": {}, "base_tool_name": DUMMY_TOOL_NAME}


def test_on_chat_model_start_does_nothing(callback: MonitoringCallback, tool_run_map_entry: Dict[str, Any]) -> None:
    callback.on_chat_model_start(serialized=tool_run_map_entry, messages=[])


def test_on_tool_start(callback: MonitoringCallback, tool_run_map_entry: Dict[str, Any]) -> None:
    serialized = {"name": DUMMY_TOOL_NAME}
    input_str = "Test input"
    expected_tools_run_map_optional_metadata = {**tool_run_map_entry, "metadata": None}

    callback.on_tool_start(serialized, input_str, run_id=RUN_ID)

    assert str(RUN_ID) in callback.tools_run_map
    assert callback.tools_run_map[str(RUN_ID)] == expected_tools_run_map_optional_metadata


@pytest.mark.parametrize(
    "method_name, output, success, additional_attributes",
    [
        ("on_tool_end", "output", True, {}),
        ("on_tool_error", Exception("Test error"), False, {"error_class": "Exception"}),
    ],
    ids=("on_tool_end", "on_tool_error"),
)
@patch.object(AgentMonitoringService, "send_tool_metrics")
@patch("codemie.agents.callbacks.monitoring_callback.calculate_tokens")
def test_on_tool_methods(
    mock_calculate_tokens: MagicMock,
    mock_send_tool_metrics: MagicMock,
    callback: MonitoringCallback,
    tool_run_map_entry: Dict[str, Any],
    method_name: str,
    output: Union[str, Exception],
    success: bool,
    additional_attributes: Dict[str, Any],
) -> None:
    expected_output_tokens_used = 1
    mock_calculate_tokens.return_value = expected_output_tokens_used
    expected_additional_attributes = {"base_tool_name": DUMMY_TOOL_NAME, **additional_attributes}
    callback.tools_run_map[str(RUN_ID)] = tool_run_map_entry

    getattr(callback, method_name)(output, run_id=RUN_ID)

    mock_send_tool_metrics.assert_called_once_with(
        tool_name=DUMMY_TOOL_NAME,
        tool_metadata={},
        output_tokens_used=expected_output_tokens_used,
        success=success,
        additional_attributes=expected_additional_attributes,
    )


def test_on_chain_end(callback: MonitoringCallback, tool_run_map_entry: Dict[str, Any]) -> None:
    callback.tools_run_map[str(RUN_ID)] = tool_run_map_entry

    callback.on_chain_end({"output": "result"}, run_id=RUN_ID)

    assert "output_tokens_used" in callback.tools_run_map[str(RUN_ID)]
