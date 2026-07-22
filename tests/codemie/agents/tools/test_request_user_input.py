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

import json
from unittest.mock import MagicMock

import pytest

from codemie.agents.tools.interactive.request_user_input import (
    REQUEST_USER_INPUT_TOOL_NAME,
    RequestUserInputTool,
)
from codemie.core.interactive import InteractiveFeaturesConfig

CFG = InteractiveFeaturesConfig(action_buttons=True)


def _tool():
    generator = MagicMock()
    return RequestUserInputTool(config=CFG, thread_generator=generator), generator


def test_execute_emits_interactive_request_chunk():
    tool, generator = _tool()
    tool.execute(surface=[{"type": "button", "id": "ok", "label": "OK"}])
    assert generator.send.call_count == 1
    chunk = json.loads(generator.send.call_args[0][0])
    assert chunk["interactive_request"]["request_id"]
    assert chunk["interactive_request"]["surface"][0]["id"] == "ok"


def test_execute_rejects_disabled_element():
    tool, generator = _tool()
    with pytest.raises(ValueError, match="multiple_choice"):
        tool.execute(surface=[{"type": "multiple_choice", "id": "c", "options": [{"value": "a", "label": "A"}]}])
    generator.send.assert_not_called()


def test_tool_is_return_direct_and_named():
    tool, _ = _tool()
    assert tool.name == REQUEST_USER_INPUT_TOOL_NAME == "request_user_input"
    assert tool.return_direct is True


def test_args_schema_reflects_config():
    tool, _ = _tool()
    schema = str(tool.args_schema.model_json_schema())
    assert "button" in schema
    assert "text_field" not in schema


class TestToolkitAppend:
    def test_appended_when_features_enabled(self):
        from unittest.mock import patch

        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        assistant.interactive_features = CFG
        generator = MagicMock()
        flag = MagicMock()
        flag.is_feature_enabled.return_value = True
        flag.get_feature_setting.return_value = None
        with patch("codemie.service.tools.toolkit_service.customer_config", flag):
            tools = ToolkitService._append_request_user_input_tool_if_enabled([], assistant, generator)
        assert len(tools) == 1
        assert tools[0].name == REQUEST_USER_INPUT_TOOL_NAME

    def test_noop_when_catalog_override_empties_enabled_types(self):
        # CR-001: a catalog override can leave feature flags on while resolving to zero
        # allowed types; the tool must be skipped (fail-closed), never constructed (500).
        from unittest.mock import patch

        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        assistant.interactive_features = CFG
        flag = MagicMock()
        flag.is_feature_enabled.return_value = True
        flag.get_feature_setting.return_value = {"layout": [], "features": {}}
        with patch("codemie.service.tools.toolkit_service.customer_config", flag):
            tools = ToolkitService._append_request_user_input_tool_if_enabled([], assistant, MagicMock())
        assert tools == []

    def test_noop_when_config_absent(self):
        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        assistant.interactive_features = None
        tools = ToolkitService._append_request_user_input_tool_if_enabled([], assistant, MagicMock())
        assert tools == []

    def test_noop_when_all_features_disabled(self):
        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        assistant.interactive_features = InteractiveFeaturesConfig()
        tools = ToolkitService._append_request_user_input_tool_if_enabled([], assistant, MagicMock())
        assert tools == []

    def test_noop_without_thread_generator(self):
        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        assistant.interactive_features = CFG
        tools = ToolkitService._append_request_user_input_tool_if_enabled([], assistant, None)
        assert tools == []

    def test_noop_when_customer_flag_disabled(self):
        from unittest.mock import patch

        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        assistant.interactive_features = CFG
        flag = MagicMock()
        flag.is_feature_enabled.return_value = False
        with patch("codemie.service.tools.toolkit_service.customer_config", flag):
            tools = ToolkitService._append_request_user_input_tool_if_enabled([], assistant, MagicMock())
        assert tools == []


def test_interactive_prompt_lists_only_enabled_elements():
    from codemie.core.interactive import render_interactive_elements_prompt

    prompt = render_interactive_elements_prompt(CFG)
    assert "request_user_input" in prompt
    assert "button" in prompt
    assert "multiple_choice" not in prompt
    assert "text_field" not in prompt
