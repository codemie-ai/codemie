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

"""_replay_tool_start_on_resume resolves tool metadata by the real tool name, not the display name."""

import uuid
from unittest.mock import MagicMock, patch

from langchain_core.messages import ToolMessage

from codemie.agents.langgraph_event_adapter import LangGraphCallbackBridge
from codemie.agents.tool_confirmation.models import ToolCallPendingEvent
from codemie.agents.tool_confirmation.tool_call_confirmation_mixin import ToolCallConfirmationMixin
from codemie.core.constants import ToolMetadataKeys, ToolType


def _make_mixin_with_bridge(tools: list) -> tuple[ToolCallConfirmationMixin, MagicMock]:
    callback = MagicMock()
    agent = MagicMock()
    agent.tools = tools
    agent.callbacks = [callback]
    bridge = LangGraphCallbackBridge(agent, get_logger=MagicMock(return_value=MagicMock()))

    mixin = ToolCallConfirmationMixin.__new__(ToolCallConfirmationMixin)
    mixin.require_tool_confirmation = True
    mixin._callback_bridge = bridge
    mixin._tool_call_id_to_uuid = lambda tc_id: uuid.UUID(int=int(tc_id) if tc_id.isdigit() else 1)
    mixin._on_tool_start = lambda tool_name, input_str, run_id=None, author=None, metadata=None: bridge.on_tool_start(
        tool_name, input_str, run_id=run_id, author=author, metadata=metadata
    )
    return mixin, callback


def _pending_event(tool_name: str, display_name: str, tool_call_id: str) -> ToolCallPendingEvent:
    return ToolCallPendingEvent(
        pending_tool_call_id=tool_call_id,
        tool_name=tool_name,
        tool_args={"query": "test"},
        display_name=display_name,
    )


def test_replay_tool_start_on_resume_forwards_tool_type_by_real_tool_name():
    tool = MagicMock()
    tool.name = "search_kb"
    tool.metadata = {ToolMetadataKeys.TOOL_TYPE.value: ToolType.DATASOURCE}
    mixin, callback = _make_mixin_with_bridge([tool])
    mixin.tool_pending_event = _pending_event("search_kb", "Search KB", "1")

    mixin._replay_tool_start_on_resume(ToolMessage(content="result", tool_call_id="1", name="search_kb"))

    assert mixin.tool_pending_event is None
    callback.on_tool_start.assert_called_once()
    assert callback.on_tool_start.call_args.kwargs["metadata"] == {
        ToolMetadataKeys.TOOL_TYPE.value: ToolType.DATASOURCE
    }
    assert callback.on_tool_start.call_args.args[0]["name"] == "Search KB"


def test_replay_tool_start_on_resume_clears_pending_event_when_metadata_lookup_fails():
    mixin, callback = _make_mixin_with_bridge([])
    mixin._callback_bridge = MagicMock()
    mixin._callback_bridge.attribution_metadata.side_effect = RuntimeError("boom")
    mixin.tool_pending_event = _pending_event("search_kb", "Search KB", "1")

    with patch("codemie.agents.tool_confirmation.tool_call_confirmation_mixin.logger") as mock_logger:
        mixin._replay_tool_start_on_resume(ToolMessage(content="result", tool_call_id="1", name="search_kb"))

    assert mixin.tool_pending_event is None
    assert callback.on_tool_start.call_args.kwargs["metadata"] == {}
    mock_logger.warning.assert_called_once()
    warning = mock_logger.warning.call_args.args[0]
    assert "search_kb" in warning
    assert "boom" in warning
