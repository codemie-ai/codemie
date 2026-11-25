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

from unittest.mock import Mock, patch

from codemie.service.generated_files_reader import WorkspaceGeneratedFiles
from codemie.service.tools.toolkit_service import ToolkitService
from codemie.rest_api.models.assistant import Tool


def _initialize(fields, conversation_id):
    tool_class = Mock()
    tool_class.model_fields = fields
    definition = Mock()
    definition.name = "t"
    definition.tool_class = tool_class
    definition.settings_config = True
    toolkit = Mock()
    toolkit.get_definition.return_value = Mock(settings_config=True)
    with (
        patch("codemie.service.tools.tool_metadata_service.toolkit_provider") as provider,
        patch("codemie.service.settings.settings.SettingsService") as settings,
        patch.object(ToolkitService, "_inject_runtime_dependencies"),
    ):
        provider.get_tool.return_value = definition
        provider.get_toolkit.return_value = toolkit
        settings.get_config.return_value = Mock()
        ToolkitService._initialize_tool(
            assistant_toolkit="tk",
            assistant_tool=Tool(name="t", description="d", label="l"),
            user_id="user-1",
            project_name="p",
            assistant_id="a",
            tools_config=None,
            conversation_id=conversation_id,
        )
    return tool_class


def test_confluence_tool_gets_adapter_bound_to_request_conversation():
    tool_class = _initialize({"config": object(), "generated_files": object()}, "conv-1")
    adapter = tool_class.call_args.kwargs["generated_files"]
    assert isinstance(adapter, WorkspaceGeneratedFiles)
    assert adapter._conversation_id == "conv-1"
    assert adapter._user_id == "user-1"


def test_no_conversation_leaves_generated_files_none():
    tool_class = _initialize({"config": object(), "generated_files": object()}, None)
    assert "generated_files" not in tool_class.call_args.kwargs


def test_tool_without_field_unchanged():
    tool_class = _initialize({"config": object()}, "conv-1")
    assert "generated_files" not in tool_class.call_args.kwargs


def test_collect_agent_tools_passes_request_conversation_to_core_tools():
    with patch.object(ToolkitService, "_initialize_toolkit_tools", return_value=[]) as init:
        ToolkitService.get_core_tools(
            assistant_toolkits=[Mock(toolkit="tk", tools=[])],
            user_id="u",
            project_name="p",
            assistant_id="a",
            tools_config=None,
            conversation_id="conv-9",
        )
    assert init.call_args.kwargs["conversation_id"] == "conv-9"


def test_collect_agent_tools_forwards_request_conversation_to_get_core_tools():
    request = Mock(conversation_id="conv-1")
    with (
        patch.object(ToolkitService, "_merge_skill_toolkits", return_value=[]),
        patch.object(ToolkitService, "_augment_toolkits_with_feature_flags", return_value=[]),
        patch.object(ToolkitService, "get_core_tools", return_value=[]) as core,
        patch.object(ToolkitService, "add_context_tools", return_value=[]),
        patch.object(ToolkitService, "_get_tools", return_value=([], [])),
        patch.object(ToolkitService, "_append_workspace_image_tool_if_enabled", side_effect=lambda t, *a: t),
        patch.object(ToolkitService, "_append_request_user_input_tool_if_enabled", side_effect=lambda t, *a, **k: t),
        patch("codemie.service.tools.toolkit_service.create_skill_tool_if_needed", return_value=None),
        patch("codemie.service.tools.toolkit_service.create_skill_companion_file_tool_if_needed", return_value=None),
        patch("codemie.service.tools.toolkit_service.bind_script_registries"),
        patch("codemie.service.tools.toolkit_service.instructions_for_tools", return_value=""),
    ):
        ToolkitService.collect_agent_tools(
            assistant=Mock(), request=request, user=Mock(), llm_model="m", request_uuid="r"
        )
    assert core.call_args.kwargs["conversation_id"] == "conv-1"


def test_initialize_toolkit_tools_forwards_conversation_to_initialize_tool():
    with patch.object(ToolkitService, "_initialize_tool", return_value=Mock()) as init:
        ToolkitService._initialize_toolkit_tools("tk", [Mock()], "u", "p", "a", None, conversation_id="conv-2")
    assert init.call_args.kwargs["conversation_id"] == "conv-2"
