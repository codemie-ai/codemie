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

from unittest.mock import MagicMock

import pytest

from codemie.service.inline_credentials_collector import InlineCredentialsCollector


@pytest.fixture
def collector() -> InlineCredentialsCollector:
    return InlineCredentialsCollector()


# ---------------------------------------------------------------------------
# collect_for_workflow
# ---------------------------------------------------------------------------


def test_collect_for_workflow_returns_empty_for_empty_workflow(
    collector: InlineCredentialsCollector,
) -> None:
    workflow = MagicMock()
    workflow.assistants = []
    workflow.tools = []

    assert collector.collect_for_workflow(workflow) == []


def test_collect_for_workflow_skips_external_assistant_steps(
    collector: InlineCredentialsCollector,
) -> None:
    from codemie.core.workflow_models.workflow_models import WorkflowAssistant

    step = MagicMock(spec=WorkflowAssistant)
    step.assistant_id = "ext-1"

    workflow = MagicMock()
    workflow.assistants = [step]
    workflow.tools = []

    assert collector.collect_for_workflow(workflow) == []


def test_collect_for_workflow_collects_mcp_from_virtual_step(
    collector: InlineCredentialsCollector,
) -> None:
    from codemie.core.workflow_models.workflow_models import WorkflowAssistant

    server = MagicMock()
    server.name = "mcp-srv"
    server.settings = MagicMock()
    server.settings.credential_values = ["v"]
    server.mcp_connect_auth_token = None
    server.config = None
    server.integration_alias = None

    step = MagicMock(spec=WorkflowAssistant)
    step.assistant_id = None
    step.mcp_servers = [server]
    step.tools = []

    workflow = MagicMock()
    workflow.assistants = [step]
    workflow.tools = []

    result = collector.collect_for_workflow(workflow)

    assert len(result) == 1
    assert result[0].mcp_server == "mcp-srv"
    assert result[0].credential_type == "mcp_environment_vars"


def test_collect_for_workflow_collects_mcp_integration_alias_from_virtual_step(
    collector: InlineCredentialsCollector,
) -> None:
    from codemie.core.workflow_models.workflow_models import WorkflowAssistant

    server = MagicMock()
    server.name = "mcp-srv"
    server.settings = None
    server.mcp_connect_auth_token = None
    server.config = None
    server.integration_alias = "my-mcp-alias"

    step = MagicMock(spec=WorkflowAssistant)
    step.assistant_id = None
    step.mcp_servers = [server]
    step.tools = []

    workflow = MagicMock()
    workflow.assistants = [step]
    workflow.tools = []

    result = collector.collect_for_workflow(workflow)

    assert len(result) == 1
    assert result[0].mcp_server == "mcp-srv"
    assert result[0].credential_type == "mcp_integration_alias"
    assert result[0].integration_alias == "my-mcp-alias"
    assert result[0].toolkit == "MCP"


def test_collect_for_workflow_collects_integration_alias_from_virtual_step(
    collector: InlineCredentialsCollector,
) -> None:
    from codemie.core.workflow_models.workflow_models import WorkflowAssistant

    assistant_tool = MagicMock()
    assistant_tool.name = "my-tool"
    assistant_tool.integration_alias = "my-alias"

    step = MagicMock(spec=WorkflowAssistant)
    step.assistant_id = None
    step.mcp_servers = []
    step.tools = [assistant_tool]

    workflow = MagicMock()
    workflow.assistants = [step]
    workflow.tools = []

    result = collector.collect_for_workflow(workflow)

    assert len(result) == 1
    assert result[0].tool == "my-tool"
    assert result[0].integration_alias == "my-alias"
    assert result[0].credential_type == "tool_integration_alias"


def test_collect_for_workflow_collects_mcp_from_tool_node(
    collector: InlineCredentialsCollector,
) -> None:
    server = MagicMock()
    server.name = "tool-mcp"
    server.settings = None
    server.mcp_connect_auth_token = None
    server.config = MagicMock()
    server.config.env = {"ENV_VAR": "val"}
    server.integration_alias = None

    tool = MagicMock()
    tool.mcp_server = server
    tool.integration_alias = None

    workflow = MagicMock()
    workflow.assistants = []
    workflow.tools = [tool]

    result = collector.collect_for_workflow(workflow)

    assert len(result) == 1
    assert result[0].mcp_server == "tool-mcp"
    assert result[0].credential_type == "mcp_inline_config_env"


def test_collect_for_workflow_collects_integration_alias_from_tool_node(
    collector: InlineCredentialsCollector,
) -> None:
    tool = MagicMock()
    tool.tool = "my-workflow-tool"
    tool.mcp_server = None
    tool.integration_alias = "wf-alias"

    workflow = MagicMock()
    workflow.assistants = []
    workflow.tools = [tool]

    result = collector.collect_for_workflow(workflow)

    assert len(result) == 1
    assert result[0].tool == "my-workflow-tool"
    assert result[0].integration_alias == "wf-alias"
    assert result[0].credential_type == "tool_integration_alias"


def test_collect_for_workflow_collects_both_mcp_and_alias_from_tool_node(
    collector: InlineCredentialsCollector,
) -> None:
    server = MagicMock()
    server.name = "tool-mcp"
    server.settings = MagicMock()
    server.settings.credential_values = ["v"]
    server.mcp_connect_auth_token = None
    server.config = None
    server.integration_alias = None

    tool = MagicMock()
    tool.tool = "combo-tool"
    tool.mcp_server = server
    tool.integration_alias = "combo-alias"

    workflow = MagicMock()
    workflow.assistants = []
    workflow.tools = [tool]

    result = collector.collect_for_workflow(workflow)

    assert len(result) == 2
    types = {c.credential_type for c in result}
    assert types == {"mcp_environment_vars", "tool_integration_alias"}


def test_collect_for_workflow_skips_tool_node_without_mcp_or_alias(
    collector: InlineCredentialsCollector,
) -> None:
    tool = MagicMock()
    tool.mcp_server = None
    tool.integration_alias = None

    workflow = MagicMock()
    workflow.assistants = []
    workflow.tools = [tool]

    assert collector.collect_for_workflow(workflow) == []


def test_collect_for_workflow_collects_mcp_and_alias_from_same_virtual_step(
    collector: InlineCredentialsCollector,
) -> None:
    from codemie.core.workflow_models.workflow_models import WorkflowAssistant

    server = MagicMock()
    server.name = "step-mcp"
    server.settings = MagicMock()
    server.settings.credential_values = ["v"]
    server.mcp_connect_auth_token = None
    server.config = None
    server.integration_alias = None

    assistant_tool = MagicMock()
    assistant_tool.name = "step-tool"
    assistant_tool.integration_alias = "step-alias"

    step = MagicMock(spec=WorkflowAssistant)
    step.assistant_id = None
    step.mcp_servers = [server]
    step.tools = [assistant_tool]

    workflow = MagicMock()
    workflow.assistants = [step]
    workflow.tools = []

    result = collector.collect_for_workflow(workflow)

    assert len(result) == 2
    types = {c.credential_type for c in result}
    assert types == {"mcp_environment_vars", "tool_integration_alias"}


def test_collect_for_workflow_aggregates_virtual_steps_and_tool_nodes(
    collector: InlineCredentialsCollector,
) -> None:
    from codemie.core.workflow_models.workflow_models import WorkflowAssistant

    step_server = MagicMock()
    step_server.name = "step-mcp"
    step_server.settings = MagicMock()
    step_server.settings.credential_values = ["v"]
    step_server.mcp_connect_auth_token = None
    step_server.config = None
    step_server.integration_alias = None

    step = MagicMock(spec=WorkflowAssistant)
    step.assistant_id = None
    step.mcp_servers = [step_server]
    step.tools = []

    tool_server = MagicMock()
    tool_server.name = "tool-mcp"
    tool_server.settings = None
    tool_server.mcp_connect_auth_token = None
    tool_server.config = MagicMock()
    tool_server.config.env = {"K": "v"}
    tool_server.integration_alias = None

    tool = MagicMock()
    tool.mcp_server = tool_server
    tool.integration_alias = None

    workflow = MagicMock()
    workflow.assistants = [step]
    workflow.tools = [tool]

    result = collector.collect_for_workflow(workflow)

    assert len(result) == 2
    names = {c.mcp_server for c in result}
    assert names == {"step-mcp", "tool-mcp"}
