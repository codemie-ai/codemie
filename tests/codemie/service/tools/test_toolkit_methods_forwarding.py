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

"""The toolkit-method map forwards the turn and conversation that artifacts are published under."""

from unittest.mock import MagicMock, patch

from codemie_tools.base.models import ToolSet

from codemie.service.tools.toolkit_service import ToolkitService


def test_plugin_entry_forwards_request_uuid():
    assistant, user, request = MagicMock(), MagicMock(), MagicMock()

    with patch.object(ToolkitService, "_get_plugin_tools_delegate") as delegate:
        ToolkitService.get_toolkit_methods()[ToolSet.PLUGIN](assistant, user, "llm", "req-1", request)

    delegate.assert_called_once_with(assistant, user, request, "req-1")


def test_file_system_entry_forwards_conversation_id():
    request = MagicMock(conversation_id="conv-1", tools_config=None)

    with (
        patch("codemie.service.tools.toolkit_service.ToolkitSettingService.get_file_system_toolkit") as toolkit,
        patch.object(ToolkitService, "_get_file_objects_from_request", return_value=[]),
    ):
        ToolkitService.get_toolkit_methods()[ToolSet.FILE_SYSTEM](MagicMock(), MagicMock(), "llm", "req-1", request)

    assert toolkit.call_args.kwargs["conversation_id"] == "conv-1"
    assert toolkit.call_args.args[4] == "req-1"
