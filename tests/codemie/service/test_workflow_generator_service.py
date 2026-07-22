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

from unittest.mock import Mock, patch

import pytest

from codemie.core.exceptions import WorkflowGenerationError
from codemie.core.workflow_models.workflow_models import CreateWorkflowRequest, WorkflowMode
from codemie.rest_api.models.workflow_generator import WorkflowRefineResponse
from codemie.rest_api.security.user import User as _User


def test_metric_constants_importable():
    from codemie.service.monitoring.metrics_constants import (
        WORKFLOW_GENERATOR_TOTAL_METRIC,
        WORKFLOW_GENERATOR_ERRORS_METRIC,
    )

    assert WORKFLOW_GENERATOR_TOTAL_METRIC == "codemie_workflow_generator_total"
    assert WORKFLOW_GENERATOR_ERRORS_METRIC == "codemie_workflow_generator_errors_total"


def _make_user(project="demo"):
    user = Mock()
    user.id = "user-1"
    user.name = "Test User"
    user.username = "test@example.com"
    user.current_project = project
    return user


def _make_create_request(project="demo"):
    return CreateWorkflowRequest(
        name="Generated Workflow",
        description="Auto-generated workflow",
        project=project,
        mode=WorkflowMode.SEQUENTIAL,
        states=[],
        assistants=[],
    )


class TestWorkflowGeneratorRequest:
    def test_default_values(self):
        from codemie.rest_api.models.workflow_generator import WorkflowGeneratorRequest

        req = WorkflowGeneratorRequest(text="Create a workflow")
        assert req.text == "Create a workflow"
        assert req.llm_model is None
        assert req.persist is False
        assert req.guardrail_ids is None

    def test_with_all_fields(self):
        from codemie.rest_api.models.workflow_generator import WorkflowGeneratorRequest

        req = WorkflowGeneratorRequest(
            text="Workflow",
            llm_model="gpt-4o",
            persist=True,
            guardrail_ids=["g1", "g2"],
        )
        assert req.persist is True
        assert req.guardrail_ids == ["g1", "g2"]


class TestWorkflowGeneratorResponse:
    def test_without_workflow_id(self):
        from codemie.rest_api.models.workflow_generator import WorkflowGeneratorResponse

        resp = WorkflowGeneratorResponse(
            workflow_config=_make_create_request(),
        )
        assert resp.workflow_id is None

    def test_with_workflow_id(self):
        from codemie.rest_api.models.workflow_generator import WorkflowGeneratorResponse

        resp = WorkflowGeneratorResponse(
            workflow_config=_make_create_request(),
            workflow_id="wf-123",
        )
        assert resp.workflow_id == "wf-123"


@patch("codemie.service.workflow_generator_service.WorkflowGeneratorGraph")
@patch("codemie.service.workflow_generator_service.ToolsInfoService")
@patch("codemie.service.workflow_generator_service.llm_service")
def test_generate_returns_response(mock_llm_svc, mock_tools_svc, mock_graph_class):
    from codemie.rest_api.models.workflow_generator import WorkflowGeneratorResponse
    from codemie.service.workflow_generator_service import WorkflowGeneratorService

    mock_llm_svc.default_llm_model = "gpt-4o"
    mock_tools_svc.get_tools_info.return_value = []

    create_req = _make_create_request()
    mock_graph = Mock()
    mock_graph.run.return_value = {"result": create_req, "error": None, "validation_errors": []}
    mock_graph_class.return_value = mock_graph

    response = WorkflowGeneratorService.generate(
        nl_query="Create a workflow",
        user=_make_user(),
    )

    assert isinstance(response, WorkflowGeneratorResponse)
    assert response.workflow_config == create_req
    assert response.workflow_id is None


@patch("codemie.service.workflow_generator_service.WorkflowGeneratorGraph")
@patch("codemie.service.workflow_generator_service.ToolsInfoService")
@patch("codemie.service.workflow_generator_service.llm_service")
def test_generate_raises_on_graph_error(mock_llm_svc, mock_tools_svc, mock_graph_class):
    from codemie.service.workflow_generator_service import WorkflowGeneratorService

    mock_llm_svc.default_llm_model = "gpt-4o"
    mock_tools_svc.get_tools_info.return_value = []

    mock_graph = Mock()
    mock_graph.run.return_value = {
        "result": None,
        "error": "Validation failed after 3 retries",
        "validation_errors": ["missing field"],
    }
    mock_graph_class.return_value = mock_graph

    with pytest.raises(WorkflowGenerationError) as exc_info:
        WorkflowGeneratorService.generate(nl_query="bad query", user=_make_user())

    assert "Workflow generation failed" in exc_info.value.message


@patch("codemie.service.workflow_generator_service.WorkflowGeneratorGraph")
@patch("codemie.service.workflow_generator_service.ToolsInfoService")
@patch("codemie.service.workflow_generator_service.llm_service")
def test_generate_applies_guardrail_ids(mock_llm_svc, mock_tools_svc, mock_graph_class):
    from codemie.service.workflow_generator_service import WorkflowGeneratorService

    mock_llm_svc.default_llm_model = "gpt-4o"
    mock_tools_svc.get_tools_info.return_value = []

    create_req = _make_create_request()
    mock_graph = Mock()
    mock_graph.run.return_value = {"result": create_req, "error": None, "validation_errors": []}
    mock_graph_class.return_value = mock_graph

    response = WorkflowGeneratorService.generate(
        nl_query="test",
        user=_make_user(),
        guardrail_ids=["g-1", "g-2"],
    )

    assert response.workflow_config.guardrail_assignments is not None
    assert len(response.workflow_config.guardrail_assignments) == 2
    assert response.workflow_config.guardrail_assignments[0].guardrail_id == "g-1"


@patch("codemie.service.workflow_generator_service.WorkflowService")
@patch("codemie.service.workflow_generator_service.GuardrailService")
@patch("codemie.service.workflow_generator_service.WorkflowGeneratorGraph")
@patch("codemie.service.workflow_generator_service.ToolsInfoService")
@patch("codemie.service.workflow_generator_service.llm_service")
def test_generate_persist_creates_workflow(
    mock_llm_svc, mock_tools_svc, mock_graph_class, mock_guardrail_svc, mock_workflow_svc_class
):
    from codemie.service.workflow_generator_service import WorkflowGeneratorService

    mock_llm_svc.default_llm_model = "gpt-4o"
    mock_tools_svc.get_tools_info.return_value = []

    create_req = _make_create_request()
    mock_graph = Mock()
    mock_graph.run.return_value = {"result": create_req, "error": None, "validation_errors": []}
    mock_graph_class.return_value = mock_graph

    persisted_config = Mock()
    persisted_config.id = "wf-saved-id"
    mock_workflow_svc = Mock()
    mock_workflow_svc.create_workflow.return_value = persisted_config
    mock_workflow_svc_class.return_value = mock_workflow_svc

    with patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow") as mock_validate:
        response = WorkflowGeneratorService.generate(
            nl_query="test",
            user=_make_user(),
            persist=True,
        )

    assert response.workflow_id == "wf-saved-id"
    mock_workflow_svc.create_workflow.assert_called_once()
    mock_validate.assert_called_once()


@patch("codemie.service.workflow_generator_service.WorkflowGeneratorGraph")
@patch("codemie.service.workflow_generator_service.ToolsInfoService")
@patch("codemie.service.workflow_generator_service.llm_service")
def test_generate_uses_config_model_when_no_llm_model_arg(mock_llm_svc, mock_tools_svc, mock_graph_class):
    """WORKFLOW_GENERATOR_LLM_MODEL takes priority over llm_service.default_llm_model when set."""
    from codemie.service.workflow_generator_service import WorkflowGeneratorService

    mock_llm_svc.default_llm_model = "gpt-4o"
    mock_tools_svc.get_tools_info.return_value = []

    create_req = _make_create_request()
    mock_graph = Mock()
    mock_graph.run.return_value = {"result": create_req, "error": None, "validation_errors": []}
    mock_graph_class.return_value = mock_graph

    with patch("codemie.service.workflow_generator_service.config") as mock_config:
        mock_config.WORKFLOW_GENERATOR_LLM_MODEL = "gpt-4.1"
        WorkflowGeneratorService.generate(nl_query="test", user=_make_user())

    mock_graph_class.assert_called_once_with(llm_model="gpt-4.1", request_id=None)


@patch("codemie.service.workflow_generator_service.WorkflowGeneratorGraph")
@patch("codemie.service.workflow_generator_service.ToolsInfoService")
@patch("codemie.service.workflow_generator_service.llm_service")
def test_generate_falls_back_to_default_when_config_model_empty(mock_llm_svc, mock_tools_svc, mock_graph_class):
    """When WORKFLOW_GENERATOR_LLM_MODEL is empty, llm_service.default_llm_model is used."""
    from codemie.service.workflow_generator_service import WorkflowGeneratorService

    mock_llm_svc.default_llm_model = "gpt-4o"
    mock_tools_svc.get_tools_info.return_value = []

    create_req = _make_create_request()
    mock_graph = Mock()
    mock_graph.run.return_value = {"result": create_req, "error": None, "validation_errors": []}
    mock_graph_class.return_value = mock_graph

    with patch("codemie.service.workflow_generator_service.config") as mock_config:
        mock_config.WORKFLOW_GENERATOR_LLM_MODEL = ""
        WorkflowGeneratorService.generate(nl_query="test", user=_make_user())

    mock_graph_class.assert_called_once_with(llm_model="gpt-4o", request_id=None)


# ── EPMCDME-12616: refine_workflow tests (graph-based) ──────────────────────────

EXAMPLE_PROJECT = "example_project"
EXAMPLE_YAML = "name: my-workflow\nstates:\n  - id: state1\n"
REFINED_YAML = "name: my-workflow\nstates:\n  - id: state1\n    description: improved\n"


@pytest.fixture
def refine_user():
    return _User(id="123", username="testuser", name="Test User", project_names=[EXAMPLE_PROJECT])


@patch("codemie.service.workflow_generator_service.request_summary_manager")
@patch("codemie.service.workflow_generator_service.emit_llm_token_metric")
@patch("codemie.service.workflow_generator_service.WorkflowRefinerGraph")
@patch("codemie.service.workflow_generator_service.ToolsInfoService")
@patch("codemie.service.workflow_generator_service.llm_service")
def test_refine_workflow_returns_response(
    mock_llm_svc, mock_tools_svc, mock_graph_class, mock_emit, mock_summary, refine_user
):
    from codemie.core.workflow_models.workflow_models import CreateWorkflowRequest, WorkflowMode
    from codemie.service.workflow_generator_service import WorkflowGeneratorService

    mock_llm_svc.default_llm_model = "gpt-4o"
    mock_tools_svc.get_tools_info.return_value = []

    create_req = CreateWorkflowRequest(
        name="my-workflow",
        description="",
        project=EXAMPLE_PROJECT,
        mode=WorkflowMode.SEQUENTIAL,
        states=[],
        assistants=[],
        yaml_config=REFINED_YAML,
    )
    mock_graph = Mock()
    mock_graph.run.return_value = {"result": create_req, "error": None, "validation_errors": []}
    mock_graph_class.return_value = mock_graph

    result = WorkflowGeneratorService.refine_workflow(
        yaml_config=EXAMPLE_YAML,
        refine_prompt="improve descriptions",
        user=refine_user,
        llm_model="gpt-4o",
        request_id="req-1",
    )

    assert isinstance(result, WorkflowRefineResponse)
    assert result.yaml_config == REFINED_YAML
    mock_emit.assert_called_once()
    mock_summary.clear_summary.assert_called_once_with("req-1")


@patch("codemie.service.workflow_generator_service.request_summary_manager")
@patch("codemie.service.workflow_generator_service.WorkflowRefinerGraph")
@patch("codemie.service.workflow_generator_service.ToolsInfoService")
@patch("codemie.service.workflow_generator_service.llm_service")
def test_refine_workflow_raises_on_graph_error(
    mock_llm_svc, mock_tools_svc, mock_graph_class, mock_summary, refine_user
):
    from codemie.service.workflow_generator_service import WorkflowGeneratorService

    mock_llm_svc.default_llm_model = "gpt-4o"
    mock_tools_svc.get_tools_info.return_value = []

    mock_graph = Mock()
    mock_graph.run.return_value = {
        "result": None,
        "error": "Validation failed after 3 retries",
        "validation_errors": ["missing field"],
    }
    mock_graph_class.return_value = mock_graph

    with pytest.raises(WorkflowGenerationError) as exc_info:
        WorkflowGeneratorService.refine_workflow(
            yaml_config=EXAMPLE_YAML,
            refine_prompt=None,
            user=refine_user,
            llm_model=None,
            request_id="req-2",
        )

    assert "Workflow refinement failed" in exc_info.value.message
    mock_summary.clear_summary.assert_called_once_with("req-2")


@patch("codemie.service.workflow_generator_service.request_summary_manager")
@patch("codemie.service.workflow_generator_service.WorkflowRefinerGraph")
@patch("codemie.service.workflow_generator_service.ToolsInfoService")
@patch("codemie.service.workflow_generator_service.llm_service")
def test_refine_workflow_raises_500_on_unexpected_exception(
    mock_llm_svc, mock_tools_svc, mock_graph_class, mock_summary, refine_user
):
    from codemie.service.workflow_generator_service import WorkflowGeneratorService

    mock_llm_svc.default_llm_model = "gpt-4o"
    mock_tools_svc.get_tools_info.return_value = []
    mock_graph_class.side_effect = RuntimeError("LLM unavailable")

    with pytest.raises(WorkflowGenerationError) as exc_info:
        WorkflowGeneratorService.refine_workflow(
            yaml_config=EXAMPLE_YAML,
            refine_prompt=None,
            user=refine_user,
            llm_model=None,
            request_id="req-3",
        )

    assert "Failed to refine workflow" in exc_info.value.message
    mock_summary.clear_summary.assert_called_once_with("req-3")
