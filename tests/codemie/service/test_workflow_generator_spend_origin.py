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

"""Spend-origin binding in WorkflowGeneratorService.

Lives outside test_workflow_generator_service.py because that module is skipped whenever
WORKFLOW_GENERATION_ENABLED is off, while the origin binding is independent of the feature flag.
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import WorkflowGenerationError
from codemie.core.workflow_models.workflow_models import CreateWorkflowRequest, WorkflowMode
from codemie.service.spend.origin_context import current_origin
from codemie.service.spend.traffic_class import TrafficOrigin
from codemie.service.workflow_generator_service import WorkflowGeneratorService

_SVC = "codemie.service.workflow_generator_service"


@pytest.fixture
def user():
    mock_user = MagicMock()
    mock_user.id = "user-1"
    mock_user.current_project = "proj"
    return mock_user


def _make_create_request():
    return CreateWorkflowRequest(
        name="Generated Workflow",
        description="Auto-generated workflow",
        project="demo",
        mode=WorkflowMode.SEQUENTIAL,
        states=[],
        assistants=[],
    )


def _recording_graph(observed, final_state):
    """A graph class whose construction records the origin the node models will be built under."""

    def record(*_args, **_kwargs):
        observed.append(current_origin())
        graph = MagicMock()
        graph.run.return_value = final_state
        return graph

    return record


@patch(f"{_SVC}.request_summary_manager")
@patch(f"{_SVC}.emit_llm_token_metric")
@patch(f"{_SVC}.ToolsInfoService")
@patch(f"{_SVC}.WorkflowGeneratorGraph")
def test_generate_publishes_workflow_generator(mock_graph_cls, mock_tools, _emit, _rsm, user):
    observed = []
    mock_tools.get_tools_info.return_value = []
    mock_graph_cls.side_effect = _recording_graph(observed, {"error": None, "result": _make_create_request()})

    WorkflowGeneratorService.generate(nl_query="build me a workflow", user=user)

    assert observed == [TrafficOrigin.WORKFLOW_GENERATOR]
    assert current_origin() is None


@patch(f"{_SVC}.request_summary_manager")
@patch(f"{_SVC}.emit_llm_token_metric")
@patch(f"{_SVC}.ToolsInfoService")
@patch(f"{_SVC}.WorkflowRefinerGraph")
def test_refine_workflow_publishes_workflow_generator(mock_graph_cls, mock_tools, _emit, _rsm, user):
    observed = []
    mock_tools.get_tools_info.return_value = []
    mock_graph_cls.side_effect = _recording_graph(
        observed, {"error": None, "result": MagicMock(yaml_config="name: wf")}
    )

    WorkflowGeneratorService.refine_workflow(
        yaml_config="name: wf",
        refine_prompt="make it shorter",
        user=user,
        llm_model=None,
        request_id="req-1",
    )

    assert observed == [TrafficOrigin.WORKFLOW_GENERATOR]
    assert current_origin() is None


@patch(f"{_SVC}.request_summary_manager")
@patch(f"{_SVC}.ToolsInfoService")
@patch(f"{_SVC}.WorkflowGeneratorGraph")
def test_origin_is_restored_when_generation_fails(mock_graph_cls, mock_tools, _rsm, user):
    observed = []
    mock_tools.get_tools_info.return_value = []

    def record_and_fail(*_args, **_kwargs):
        observed.append(current_origin())
        raise RuntimeError("graph failure")

    mock_graph_cls.side_effect = record_and_fail

    with pytest.raises(WorkflowGenerationError):
        WorkflowGeneratorService.generate(nl_query="build me a workflow", user=user)

    assert observed == [TrafficOrigin.WORKFLOW_GENERATOR]
    assert current_origin() is None
