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

from unittest.mock import MagicMock
from uuid import uuid4

from codemie.agents.langgraph_event_adapter import LangGraphCallbackBridge
from codemie.core.constants import AGENT_NAME, ToolMetadataKeys, ToolType


def _make_tool(name: str, metadata: dict) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.metadata = metadata
    return tool


def _make_bridge(tools: list) -> tuple[LangGraphCallbackBridge, MagicMock]:
    callback = MagicMock()
    agent = MagicMock()
    agent.tools = tools
    agent.callbacks = [callback]
    bridge = LangGraphCallbackBridge(agent, get_logger=MagicMock(return_value=MagicMock()))
    return bridge, callback


class TestLangGraphCallbackBridgeOnToolStart:
    def test_forwards_raw_tool_type_and_identity_from_tool_metadata(self):
        tool = _make_tool(
            "search_kb",
            {ToolMetadataKeys.TOOL_TYPE.value: ToolType.DATASOURCE, AGENT_NAME: "agent", "output_format": "markdown"},
        )
        bridge, callback = _make_bridge([tool])

        bridge.on_tool_start("search_kb", "some input", run_id=uuid4())

        assert callback.on_tool_start.call_args.kwargs["metadata"] == {
            ToolMetadataKeys.TOOL_TYPE.value: ToolType.DATASOURCE,
            AGENT_NAME: "agent",
        }

    def test_unknown_tool_forwards_empty_metadata(self):
        bridge, callback = _make_bridge([])

        bridge.on_tool_start("nonexistent_tool", "some input", run_id=uuid4())

        assert callback.on_tool_start.call_args.kwargs["metadata"] == {}

    def test_explicit_metadata_takes_precedence_over_tool_lookup(self):
        tool = _make_tool("search_kb", {ToolMetadataKeys.TOOL_TYPE.value: ToolType.DATASOURCE})
        bridge, callback = _make_bridge([tool])
        explicit = {ToolMetadataKeys.TOOL_TYPE.value: ToolType.MCP}

        bridge.on_tool_start("search_kb", "some input", run_id=uuid4(), metadata=explicit)

        assert callback.on_tool_start.call_args.kwargs["metadata"] is explicit
