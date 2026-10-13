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

"""ToolkitService.get_tools hands the request's conversation id to the skill tool (its workspace location hint)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.tools import toolkit_service as module
from codemie.service.tools.toolkit_service import ToolkitService


def _assistant() -> MagicMock:
    assistant = MagicMock(spec=Assistant)
    assistant.id = "a1"
    assistant.name = "A"
    assistant.project = "p"
    assistant.toolkits = []
    assistant.context = []
    assistant.mcp_servers = []
    assistant.assistant_ids = []
    assistant.skill_ids = ["sk1"]
    return assistant


def _request(conversation_id: str | None) -> MagicMock:
    request = MagicMock(spec=AssistantChatRequest)
    request.text = "hi"
    request.conversation_id = conversation_id
    request.file_name = None
    request.ide_installation_id = None
    request.tools_config = None
    request.enable_web_search = None
    request.enable_code_interpreter = None
    return request


def _user() -> MagicMock:
    user = MagicMock(spec=User)
    user.id = "u1"
    user.name = "U"
    user.is_admin = False
    user.is_maintainer = False
    return user


@pytest.mark.parametrize("request_obj, expected", [(_request("c1"), "c1"), (_request(None), None)])
def test_get_tools_passes_the_conversation_id_to_the_skill_tool(request_obj, expected):
    with (
        patch.object(ToolkitService, "add_tools_with_creds", return_value=[]),
        patch.object(ToolkitService, "add_context_tools", return_value=[]),
        patch.object(ToolkitService, "add_file_tools", return_value=[]),
        patch.object(module, "create_skill_tool_if_needed", return_value=None) as create_skill_tool,
        patch.object(module, "create_skill_companion_file_tool_if_needed", return_value=None),
    ):
        ToolkitService.get_tools(_assistant(), request_obj, _user(), "test-model", "uuid")

    assert create_skill_tool.call_args.kwargs["conversation_id"] == expected
