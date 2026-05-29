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

"""Unit tests for k5l6m7n8o9p0 migration transform logic."""

OLD_TOOL = "python_repl_code_interpreter"
NEW_TOOL = "code_executor"

CODE_EXECUTOR = {
    "name": NEW_TOOL,
    "label": "Code Executor",
    "settings_config": False,
    "settings": None,
    "description": "Execute Python code.",
    "user_description": "Execute Python code.",
}


def _import_transform():
    from external.alembic.versions.k5l6m7n8o9p0_deprecate_python_repl_code_interpreter import (
        _transform_toolkits,
    )

    return _transform_toolkits


def _import_workflow_helpers():
    from external.alembic.versions.k5l6m7n8o9p0_deprecate_python_repl_code_interpreter import (
        _transform_workflow_assistants,
        _transform_workflow_tools,
        _transform_yaml_config,
    )

    return _transform_workflow_tools, _transform_workflow_assistants, _transform_yaml_config


def test_sole_old_tool_replaced_with_code_executor():
    """Toolkit with only python_repl gets replaced by code_executor."""
    _transform_toolkits = _import_transform()
    toolkits = [
        {
            "toolkit": "FileSystem",
            "label": "",
            "tools": [{"name": OLD_TOOL, "label": "Code Interpreter", "settings_config": False, "settings": None}],
            "settings": None,
            "is_external": False,
            "config_class": None,
            "settings_config": False,
        }
    ]
    result = _transform_toolkits(toolkits, CODE_EXECUTOR)
    assert len(result) == 1
    assert result[0]["toolkit"] == "FileSystem"
    assert len(result[0]["tools"]) == 1
    assert result[0]["tools"][0]["name"] == NEW_TOOL


def test_old_tool_removed_when_code_executor_present():
    """Toolkit with both tools: old is removed, code_executor stays unchanged."""
    _transform_toolkits = _import_transform()
    existing_executor = {
        "name": NEW_TOOL,
        "label": "Code Executor",
        "settings_config": False,
        "settings": None,
        "description": "existing description",
        "user_description": "existing user_description",
    }
    toolkits = [
        {
            "toolkit": "FileSystem",
            "label": "",
            "tools": [
                {"name": OLD_TOOL, "label": "Code Interpreter", "settings_config": False, "settings": None},
                existing_executor,
            ],
            "settings": None,
            "is_external": False,
            "config_class": None,
            "settings_config": False,
        }
    ]
    result = _transform_toolkits(toolkits, CODE_EXECUTOR)
    assert len(result[0]["tools"]) == 1
    tool = result[0]["tools"][0]
    assert tool["name"] == NEW_TOOL
    # The pre-existing code_executor object is kept, not replaced
    assert tool["description"] == "existing description"


def test_mixed_toolkit_preserves_other_tools():
    """Other tools in the toolkit are untouched."""
    _transform_toolkits = _import_transform()
    toolkits = [
        {
            "toolkit": "FileSystem",
            "label": "",
            "tools": [
                {"name": "generate_image_tool", "label": "Generate image", "settings_config": False, "settings": None},
                {"name": OLD_TOOL, "label": "Code Interpreter", "settings_config": False, "settings": None},
            ],
            "settings": None,
            "is_external": False,
            "config_class": None,
            "settings_config": False,
        }
    ]
    result = _transform_toolkits(toolkits, CODE_EXECUTOR)
    tool_names = [t["name"] for t in result[0]["tools"]]
    assert "generate_image_tool" in tool_names
    assert NEW_TOOL in tool_names
    assert OLD_TOOL not in tool_names


def test_replacement_preserves_position():
    """code_executor is inserted at the same index as python_repl was."""
    _transform_toolkits = _import_transform()
    toolkits = [
        {
            "toolkit": "FileSystem",
            "label": "",
            "tools": [
                {"name": OLD_TOOL, "label": "Code Interpreter", "settings_config": False, "settings": None},
                {"name": "generate_image_tool", "label": "Generate image", "settings_config": False, "settings": None},
            ],
            "settings": None,
            "is_external": False,
            "config_class": None,
            "settings_config": False,
        }
    ]
    result = _transform_toolkits(toolkits, CODE_EXECUTOR)
    assert result[0]["tools"][0]["name"] == NEW_TOOL
    assert result[0]["tools"][1]["name"] == "generate_image_tool"


def test_unrelated_toolkits_untouched():
    """Toolkits without python_repl are not modified."""
    _transform_toolkits = _import_transform()
    toolkits = [
        {
            "toolkit": "Cloud",
            "label": "Cloud",
            "tools": [{"name": "AWS", "label": "AWS", "settings_config": True, "settings": None}],
            "settings": None,
            "is_external": False,
            "config_class": None,
            "settings_config": False,
        }
    ]
    result = _transform_toolkits(toolkits, CODE_EXECUTOR)
    assert result == toolkits


def test_workflow_tools_old_renamed():
    """Workflow tool with old name is renamed to code_executor; other fields preserved."""
    _transform_workflow_tools, _, _ = _import_workflow_helpers()
    tools = [
        {
            "id": "tool_2",
            "tool": OLD_TOOL,
            "tool_args": {"code": "print('hello')"},
            "trace": True,
            "tool_result_json_pointer": "",
            "resolve_dynamic_values_in_response": False,
        }
    ]
    result = _transform_workflow_tools(tools)
    assert len(result) == 1
    assert result[0]["tool"] == NEW_TOOL
    assert result[0]["id"] == "tool_2"
    assert result[0]["tool_args"] == {"code": "print('hello')"}


def test_workflow_tools_other_untouched():
    """Workflow tools with different names are not modified."""
    _transform_workflow_tools, _, _ = _import_workflow_helpers()
    tools = [
        {"id": "t1", "tool": "gitlab", "tool_args": {}},
        {"id": "t2", "tool": OLD_TOOL, "tool_args": {"code": "x = 1"}},
        {"id": "t3", "tool": "generic_jira_tool", "tool_args": {}},
    ]
    result = _transform_workflow_tools(tools)
    assert result[0]["tool"] == "gitlab"
    assert result[1]["tool"] == NEW_TOOL
    assert result[2]["tool"] == "generic_jira_tool"


def test_workflow_tools_empty_list():
    """Empty tools list returns empty list."""
    _transform_workflow_tools, _, _ = _import_workflow_helpers()
    assert _transform_workflow_tools([]) == []


def test_yaml_config_old_tool_replaced():
    """YAML config with old tool name has it replaced."""
    _, _, _transform_yaml_config = _import_workflow_helpers()
    yaml = (
        "tools:\n"
        "  - id: tool_2\n"
        f"    tool: {OLD_TOOL}\n"
        "    tool_args:\n"
        "      code: |-\n"
        "        print('hello')\n"
    )
    result = _transform_yaml_config(yaml)
    assert f"tool: {NEW_TOOL}" in result
    assert OLD_TOOL not in result


def test_yaml_config_unrelated_untouched():
    """YAML config without old tool name is returned unchanged."""
    _, _, _transform_yaml_config = _import_workflow_helpers()
    yaml = "tools:\n  - id: tool_1\n    tool: gitlab\n"
    assert _transform_yaml_config(yaml) == yaml


def test_workflow_assistants_old_tool_renamed():
    """Assistant tool with old name is renamed; other fields and other assistants untouched."""
    _, _transform_workflow_assistants, _ = _import_workflow_helpers()
    assistants = [
        {
            "id": "assistant-1",
            "model": "claude-sonnet-4-6",
            "tools": [
                {"name": OLD_TOOL, "integration_alias": None},
                {"name": "gitlab", "integration_alias": "gitlab-alias"},
            ],
        },
        {
            "id": "assistant-2",
            "model": "claude-sonnet-4-6",
            "tools": [],
        },
    ]
    result = _transform_workflow_assistants(assistants)
    assert len(result) == 2
    tool_names_0 = [t["name"] for t in result[0]["tools"]]
    assert NEW_TOOL in tool_names_0
    assert OLD_TOOL not in tool_names_0
    assert "gitlab" in tool_names_0
    assert result[0]["id"] == "assistant-1"
    assert result[1]["tools"] == []


def test_workflow_assistants_no_old_tool_untouched():
    """Assistants without old tool name are returned unchanged."""
    _, _transform_workflow_assistants, _ = _import_workflow_helpers()
    assistants = [
        {"id": "a1", "tools": [{"name": "gitlab", "integration_alias": None}]},
    ]
    result = _transform_workflow_assistants(assistants)
    assert result[0]["tools"][0]["name"] == "gitlab"


def test_workflow_assistants_empty_list():
    """Empty assistants list returns empty list."""
    _, _transform_workflow_assistants, _ = _import_workflow_helpers()
    assert _transform_workflow_assistants([]) == []


def test_yaml_config_multiple_occurrences():
    """All occurrences of old tool name in YAML are replaced."""
    _, _, _transform_yaml_config = _import_workflow_helpers()
    yaml = f"    tool: {OLD_TOOL}\n" "    tool_args: {}\n" f"    tool: {OLD_TOOL}\n"
    result = _transform_yaml_config(yaml)
    assert result.count(f"tool: {NEW_TOOL}") == 2
    assert OLD_TOOL not in result


def test_pre_empty_toolkit_is_dropped():
    """Guard: pre-existing toolkit with empty tools list is dropped."""
    _transform_toolkits = _import_transform()
    toolkits = [
        {
            "toolkit": "FileSystem",
            "label": "",
            "tools": [],  # already empty before transform
            "settings": None,
            "is_external": False,
            "config_class": None,
            "settings_config": False,
        }
    ]
    result = _transform_toolkits(toolkits, CODE_EXECUTOR)
    assert result == []
