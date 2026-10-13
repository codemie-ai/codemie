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
from unittest.mock import MagicMock, patch

from codemie.service.assistant import VirtualAssistantService, VIRTUAL_ASSISTANT_PREFIX
from codemie.rest_api.models.assistant import ToolKitDetails
from codemie.core.workflow_models import WorkflowAssistant, WorkflowTool, WorkflowAssistantTool
from codemie.rest_api.security.user import User
from codemie.rest_api.models.settings import Settings
from codemie.rest_api.models.index import IndexInfo

TEST_TOOLKIT = "Project Management"
TEST_TOOL = "generic_jira_tool"


@pytest.fixture
def mock_toolkit():
    return MagicMock(spec=ToolKitDetails)


@pytest.fixture
def assistant(mock_toolkit):
    return VirtualAssistantService.create(
        toolkits=[mock_toolkit],
        project="Test",
        execution_id="test_execution",
    )


@pytest.fixture
def workflow_assistant_config():
    return WorkflowAssistant(
        id="test_asst",
        system_prompt="Hello, World! 1",
        tools=[WorkflowAssistantTool(name=TEST_TOOL, integration_alias="integration")],
        exclude_context_tools=True,
    )


@pytest.fixture
def workflow_assistant_config_invalid_toolkit():
    return WorkflowAssistant(
        id="test_asst",
        system_prompt="Hello, World! 2",
        tools=[WorkflowAssistantTool(name=TEST_TOOL, integration_alias="integration")],
    )


@pytest.fixture
def workflow_assistant_config_invalid_tool():
    return WorkflowAssistant(
        id="test_asst",
        system_prompt="Hello, World! 3",
        tools=[WorkflowAssistantTool(name=TEST_TOOL, integration_alias="integration")],
    )


@pytest.fixture
def workflow_tool():
    return WorkflowTool(id="tool_id", toolset=TEST_TOOLKIT, tool="invalid_tool", integration_alist="jira_config")


def test_create(assistant):
    assert assistant.id.startswith(VIRTUAL_ASSISTANT_PREFIX)
    assert assistant.project == "Test"
    assert len(assistant.toolkits) == 1

    assert VirtualAssistantService.assistants[assistant.id] == assistant

    VirtualAssistantService.delete(assistant_id=assistant.id)


def test_create_with_skill_ids(mock_toolkit):
    skill_ids = ["skill-1", "skill-2"]
    assistant = VirtualAssistantService.create(
        toolkits=[mock_toolkit],
        project="Test",
        execution_id="test_execution",
        skill_ids=skill_ids,
    )

    assert assistant.skill_ids == skill_ids

    VirtualAssistantService.delete(assistant_id=assistant.id)


def test_create_without_skill_ids_defaults_to_empty(mock_toolkit):
    assistant = VirtualAssistantService.create(
        toolkits=[mock_toolkit],
        project="Test",
        execution_id="test_execution",
    )

    assert assistant.skill_ids == []

    VirtualAssistantService.delete(assistant_id=assistant.id)


def test_get(assistant):
    result = VirtualAssistantService.get(assistant_id=assistant.id)
    assert result == assistant

    VirtualAssistantService.delete(assistant_id=assistant.id)


def test_delete(assistant):
    VirtualAssistantService.delete(assistant_id=assistant.id)

    assert VirtualAssistantService.assistants == {}


@patch("codemie.rest_api.models.settings.Settings.get_by_fields")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_id")
def test_create_with_datasource_code(mock_get_index_info, mock_get_setting):
    mock_get_setting.return_value = MagicMock(spec=Settings)
    mock_get_index_info.return_value = IndexInfo(
        project_name="test_project",
        repo_name="test_repo",
        index_type="code",
        description="Test description",
    )

    assistant = VirtualAssistantService.create(
        toolkits=[], project="Test", execution_id="test_execution", datasource_ids=["test"]
    )

    assert assistant.context[0].context_type == "code"
    assert assistant.context[0].name == "test_repo"


@patch("codemie.rest_api.models.settings.Settings.get_by_fields")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_id")
def test_create_with_datasource_kb(mock_get_index_info, mock_get_setting):
    mock_get_setting.return_value = MagicMock(spec=Settings)
    mock_get_index_info.return_value = IndexInfo(
        project_name="test_project",
        repo_name="test_repo",
        index_type="knowledge_base_json",
        description="Test description",
    )

    assistant = VirtualAssistantService.create(
        toolkits=[], project="Test", execution_id="test_execution", datasource_ids=["test"]
    )

    assert assistant.context[0].context_type == "knowledge_base"
    assert assistant.context[0].name == "test_repo"


@patch("codemie.rest_api.models.settings.Settings.get_by_fields")
@patch("codemie.rest_api.models.index.IndexInfo.get_by_id")
def test_create_with_datasource_not_found(mock_get_index_info, mock_get_setting):
    mock_get_setting.return_value = MagicMock(spec=Settings)
    mock_get_setting.return_value = None

    with pytest.raises(ValueError):
        VirtualAssistantService.create(
            toolkits=[], project="Test", execution_id="test_execution", datasource_ids=["test"]
        )


@patch("codemie.service.tools.tools_info_service.ToolsInfoService._provider_toolkits_info", return_value=[])
@patch("codemie.rest_api.models.settings.Settings.get_by_fields")
def test_create_from_virtual_asst_config_invalid_integration(
    _mock_get_setting, _mock_provider_tools, workflow_assistant_config
):
    with pytest.raises(ValueError):
        assistant = VirtualAssistantService.create_from_virtual_asst_config(
            config=workflow_assistant_config,
            project_name="Test",
            user=User(id="test_user"),
            execution_id="test_execution",
        )

        assert assistant


@patch("codemie.service.tools.tools_info_service.ToolsInfoService._provider_toolkits_info", return_value=[])
@patch("codemie.rest_api.models.settings.Settings.get_by_fields")
def test_create_from_virtual_asst_config_invalid_toolkit(
    _mock_get_setting, _mock_provider_tools, workflow_assistant_config_invalid_toolkit
):
    with pytest.raises(ValueError):
        assistant = VirtualAssistantService.create_from_virtual_asst_config(
            config=workflow_assistant_config_invalid_toolkit,
            project_name="Test",
            user=User(id="test_user"),
            execution_id="test_execution",
        )

        assert assistant


@patch("codemie.service.tools.tools_info_service.ToolsInfoService._provider_toolkits_info", return_value=[])
@patch("codemie.rest_api.models.settings.Settings.get_by_fields")
def test_create_from_virtual_asst_config_invalid_tool(
    _mock_get_setting, _mock_provider_tools, workflow_assistant_config_invalid_tool
):
    with pytest.raises(ValueError):
        assistant = VirtualAssistantService.create_from_virtual_asst_config(
            config=workflow_assistant_config_invalid_tool,
            project_name="Test",
            user=User(id="test_user"),
            execution_id="test_execution",
        )

        assert assistant


@patch("codemie.service.tools.tools_info_service.ToolsInfoService._provider_toolkits_info", return_value=[])
@patch("codemie.rest_api.models.settings.Settings.get_by_fields")
def test_create_from_virtual_asst_config_valid_integration(
    mock_get_setting, _mock_provider_tools, workflow_assistant_config
):
    mock_get_setting.return_value = MagicMock(spec=Settings)
    assistant = VirtualAssistantService.create_from_virtual_asst_config(
        config=workflow_assistant_config, project_name="Test", user=User(id="test_user"), execution_id="test_execution"
    )

    assert assistant.toolkits[0].toolkit == TEST_TOOLKIT
    assert assistant.toolkits[0].tools[0].name == "generic_jira_tool"
    mock_get_setting.assert_called_once()


@patch("codemie.service.tools.tools_info_service.ToolsInfoService._provider_toolkits_info", return_value=[])
@patch("codemie.rest_api.models.settings.Settings.get_by_fields")
def test_create_from_virtual_asst_config_no_integration(
    mock_get_setting, _mock_provider_tools, workflow_assistant_config
):
    mock_get_setting.return_value = MagicMock(spec=Settings)
    workflow_assistant_config.tools[0].integration_alias = None

    assistant = VirtualAssistantService.create_from_virtual_asst_config(
        config=workflow_assistant_config, project_name="Test", user=User(id="test_user"), execution_id="test_execution"
    )

    assert assistant.toolkits[0].toolkit == TEST_TOOLKIT
    assert assistant.toolkits[0].tools[0].name == "generic_jira_tool"
    mock_get_setting.assert_not_called()


@patch("codemie.rest_api.models.settings.Settings.get_by_fields")
def create_from_tool_config(mock_get_setting, mock_workflow_tool):
    mock_get_setting.return_value = MagicMock(spec=Settings)

    assistant = VirtualAssistantService.create_from_tool_config(
        tool_config=mock_workflow_tool(), project_name="Test", user=User(id="test_user"), execution_id="test_execution"
    )

    assert assistant.toolkits[0].toolkit == TEST_TOOLKIT
    assert assistant.toolkits[0].tools[0].name == "generic_jira_tool"
    mock_get_setting.assert_called_once()


def test_delete_by_execution_id(mock_toolkit):
    VirtualAssistantService.assistants = {
        "first": VirtualAssistantService.create(
            toolkits=[mock_toolkit],
            project="Test",
            execution_id="test_execution_1",
        ),
        "second": VirtualAssistantService.create(
            toolkits=[mock_toolkit],
            project="Test",
            execution_id="test_execution_1",
        ),
        "third": VirtualAssistantService.create(
            toolkits=[mock_toolkit],
            project="Test",
            execution_id="test_execution_2",
        ),
    }

    VirtualAssistantService.delete_by_execution_id("test_execution_1")

    with pytest.raises(KeyError):
        assert VirtualAssistantService.assistants["first"]

    with pytest.raises(KeyError):
        assert VirtualAssistantService.assistants["second"]

    assert VirtualAssistantService.assistants["third"]


def _tool_step_assistant(mock_toolkit, tool_config=None, access_toolkits=None, **kwargs):
    with (
        patch(
            "codemie.service.assistant.virtual_assistant_service.ToolsService.get_toolkit_from_workflow_tool_config",
            return_value=mock_toolkit,
        ),
        patch(
            "codemie.service.assistant.virtual_assistant_service.ToolsService.get_toolkits_for_tool_access",
            return_value=access_toolkits or [],
        ) as for_access,
    ):
        assistant = VirtualAssistantService.create_from_tool_config(
            tool_config=tool_config or WorkflowTool(id="s1", tool="execute_workspace_script"),
            project_name="Test",
            user=User(id="test_user"),
            execution_id="exec-1",
            **kwargs,
        )
    return assistant, for_access


def test_create_from_tool_config_carries_the_toolkit_of_the_tool_and_the_execution_id(mock_toolkit):
    assistant, _ = _tool_step_assistant(mock_toolkit)

    assert assistant.toolkits == [mock_toolkit]
    assert assistant.execution_id == "exec-1"


def test_create_from_tool_config_marks_the_assistant_as_a_tool_step(mock_toolkit):
    assistant, _ = _tool_step_assistant(mock_toolkit)

    assert assistant.is_tool_step is True


def test_create_keeps_is_tool_step_off_by_default(mock_toolkit):
    assistant = VirtualAssistantService.create(toolkits=[mock_toolkit], project="Test", execution_id="exec-2")

    assert assistant.is_tool_step is False


def test_create_from_tool_config_adds_one_toolkit_per_listed_tool(mock_toolkit):
    listed = MagicMock(spec=ToolKitDetails)
    step = WorkflowTool(
        id="s1",
        tool="execute_workspace_script",
        tool_access=[WorkflowAssistantTool(name="jira", integration_alias="a")],
    )

    assistant, for_access = _tool_step_assistant(mock_toolkit, step, access_toolkits=[listed])

    assert assistant.toolkits == [mock_toolkit, listed]
    assert for_access.call_args.args[:2] == ("execute_workspace_script", step.tool_access)


def test_a_tool_that_takes_no_tool_access_gets_no_listed_toolkits(mock_toolkit):
    step = WorkflowTool(id="s1", tool="generic_jira_tool", tool_access=[WorkflowAssistantTool(name="jira")])

    assistant, for_access = _tool_step_assistant(mock_toolkit, step)

    assert assistant.toolkits == [mock_toolkit]
    for_access.assert_not_called()


def test_global_workflow_looks_pinned_aliases_up_for_the_creator(mock_toolkit):
    step = WorkflowTool(
        id="s1",
        tool="execute_workspace_script",
        tool_access=[WorkflowAssistantTool(name="jira", integration_alias="a")],
    )

    _, for_access = _tool_step_assistant(mock_toolkit, step, is_global=True, created_by_user_id="creator-1")

    assert for_access.call_args.kwargs["owner_user_id"] == "creator-1"


@pytest.mark.parametrize("is_global", [True, False])
def test_automatic_lookup_never_goes_to_the_creator(mock_toolkit, is_global):
    step = WorkflowTool(id="s1", tool="execute_workspace_script", tool_access=[WorkflowAssistantTool(name="jira")])

    _, for_access = _tool_step_assistant(mock_toolkit, step, is_global=is_global, created_by_user_id="creator-1")

    assert for_access.call_args.kwargs["owner_user_id"] is None


def test_non_global_workflow_looks_pinned_aliases_up_for_the_running_user(mock_toolkit):
    step = WorkflowTool(
        id="s1",
        tool="execute_workspace_script",
        tool_access=[WorkflowAssistantTool(name="jira", integration_alias="a")],
    )

    _, for_access = _tool_step_assistant(mock_toolkit, step, is_global=False, created_by_user_id="creator-1")

    assert for_access.call_args.kwargs["owner_user_id"] is None


def test_global_workflow_with_a_pinned_alias_and_no_creator_is_refused(mock_toolkit):
    step = WorkflowTool(
        id="s1",
        tool="execute_workspace_script",
        tool_access=[WorkflowAssistantTool(name="jira", integration_alias="a")],
    )

    with pytest.raises(ValueError, match="creator"):
        _tool_step_assistant(mock_toolkit, step, is_global=True, created_by_user_id=None)
