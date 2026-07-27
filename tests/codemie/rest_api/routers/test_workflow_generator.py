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
from fastapi.testclient import TestClient

from codemie.configs import config
from codemie.core.workflow_models.workflow_models import CreateWorkflowRequest, WorkflowMode
from codemie.rest_api.models.workflow_generator import WorkflowGeneratorResponse

pytestmark = pytest.mark.skipif(
    not config.WORKFLOW_GENERATION_ENABLED,
    reason="WORKFLOW_GENERATION_ENABLED is False",
)


def _make_app():
    from fastapi import FastAPI
    from codemie.rest_api.routers import workflow as workflow_router

    app = FastAPI()
    app.include_router(workflow_router.router)
    return app


def _make_user():
    user = Mock()
    user.id = "user-1"
    user.name = "Test User"
    user.username = "test@example.com"
    user.current_project = "demo"
    user.is_admin = False
    user.project_names = ["demo"]
    user.admin_project_names = []
    return user


def _make_response():
    return WorkflowGeneratorResponse(
        workflow_config=CreateWorkflowRequest(
            name="Generated",
            description="Generated workflow",
            project="demo",
            mode=WorkflowMode.SEQUENTIAL,
            states=[],
            assistants=[],
        ),
        workflow_id=None,
    )


@patch("codemie.rest_api.routers.workflow.authenticate")
@patch("codemie.service.workflow_generator_service.WorkflowGeneratorService.generate")
def test_generate_workflow_returns_200(mock_generate, mock_auth):
    mock_auth.return_value = _make_user()
    mock_generate.return_value = _make_response()

    client = TestClient(_make_app())
    response = client.post(
        "/v1/workflows/generate",
        json={"text": "Create a code review workflow"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "workflow_config" in data
    assert data["workflow_config"]["name"] == "Generated"


@patch("codemie.rest_api.routers.workflow.authenticate")
@patch("codemie.service.workflow_generator_service.WorkflowGeneratorService.generate")
def test_generate_workflow_with_persist_flag(mock_generate, mock_auth):
    mock_auth.return_value = _make_user()
    resp = _make_response()
    resp.workflow_id = "wf-saved"
    mock_generate.return_value = resp

    client = TestClient(_make_app())
    response = client.post(
        "/v1/workflows/generate",
        json={"text": "Create workflow", "persist": True},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.json()["workflow_id"] == "wf-saved"
    call_kwargs = mock_generate.call_args[1]
    assert call_kwargs.get("persist") is True


@patch("codemie.rest_api.routers.workflow.authenticate")
@patch("codemie.service.workflow_generator_service.WorkflowGeneratorService.generate")
def test_generate_workflow_service_error_returns_500(mock_generate, mock_auth):
    from codemie.core.exceptions import ExtendedHTTPException

    mock_auth.return_value = _make_user()
    mock_generate.side_effect = ExtendedHTTPException(
        code=500,
        message="Generation failed",
        details="LLM timeout",
        help="Try again",
    )

    client = TestClient(_make_app(), raise_server_exceptions=False)
    response = client.post(
        "/v1/workflows/generate",
        json={"text": "bad query"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 500
