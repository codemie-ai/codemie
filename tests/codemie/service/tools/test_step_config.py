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

"""Tools describe the extra workflow step settings they take; the schema endpoint passes them on."""

from unittest.mock import patch

from codemie.core.workflow_models.workflow_models import WorkflowTool
from codemie.service.tools.step_config import get_step_config
from codemie_tools.base.tool_registry import step_config_fields


def test_script_tool_declares_the_access_map():
    assert step_config_fields("execute_workspace_script") == ("tool_access",)

    [entry] = get_step_config("execute_workspace_script")

    assert entry.field == "tool_access"
    assert entry.component == "tool_integration_picker"
    assert entry.props["tool_filter"] == {"script_callable": True}
    assert "multiple_integrations" not in entry.props


def test_other_and_unknown_tools_have_no_step_config():
    assert get_step_config("generic_jira_tool") == []
    assert get_step_config("no_such_tool") == []


def test_step_config_entries_are_copies():
    get_step_config("execute_workspace_script")[0].props["label"] = "changed"

    assert get_step_config("execute_workspace_script")[0].props["label"] != "changed"


def test_every_declared_field_exists_on_the_workflow_tool():
    for name in ("execute_workspace_script",):
        for field in step_config_fields(name):
            assert field in WorkflowTool.model_fields


def test_the_tool_filter_is_the_flag_the_step_tool_names():
    with patch("codemie.service.tools.step_config.tool_access_filter", return_value="agent_callable"):
        [entry] = get_step_config("execute_workspace_script")

    assert entry.props["tool_filter"] == {"agent_callable": True}
    assert entry.props["label"] == "Tools available to the step"
