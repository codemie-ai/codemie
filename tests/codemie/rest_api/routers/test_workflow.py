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

from unittest.mock import patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from codemie.core.constants import DEMO_PROJECT
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import AssistantChatRequest
from codemie.core.workflow_models import (
    WorkflowConfig,
    WorkflowConfigTemplate,
    CreateWorkflowRequest,
    UpdateWorkflowRequest,
    WorkflowMode,
)
from codemie.rest_api.main import app
from codemie.rest_api.security.user import User
from codemie.service.workflow_service import WorkflowService
import codemie.rest_api.routers.workflow as workflow_router

CREATE_DIAGRAM_PATH = "/v1/workflows/diagram"

client = TestClient(app)

test_yaml_config = """
assistants:
  - id: business_analyst
    assistant_id: 196ede41-e7f0-4658-ae99-1dc0d83c8347
    model: 'gpt-4o-2024-11-20'
  - id: onboarder
    assistant_id: d09ec675-16db-4aba-901d-1fff17d84692
    model: 'gpt-4o-2024-11-20'  # Specify the model type
  - id: developer
    assistant_id: d98bd4b7-e8b9-4bb9-92d8-9fbc19b250b0
    model: 'gpt-4o-2024-11-20'  # Specify the model type
states:
  - id: business_analyst
    assistant_id: business_analyst
    task: |
      You must create Jira story for detailed user input for PROJ project. Put everything to description.
    output_schema: |
      {
        "success": "Boolean true | false",
        "title": "Ticket title",
        "description": "Ticket description, should be String of all description details"
      }
    next:
      condition:
        expression: "success == True"
        then: end
        otherwise: business_analyst
"""

workflow_config_data = WorkflowConfig(
    id="workflow_123",
    name="Test Workflow",
    description="A test workflow",
    yaml_config=test_yaml_config,
    project="demo",
    shared=True,
    assistants=[],
    states=[],
)

user = User(id="123", username="123", name="123")


@pytest.fixture
def request_header():
    return {"user-id": user.id, "username": user.username, "name": user.name}


@pytest.fixture
def projects():
    return ["demo"]


@pytest.fixture
def workflow_config():
    return workflow_config_data


@pytest.fixture
def create_workflow_request():
    return CreateWorkflowRequest(
        name="Test Workflow",
        description="A new test workflow",
        project="demo",
        icon_url="test",
        yaml_config=test_yaml_config,
        assistants=[],
        states=[],
        mode=WorkflowMode.SEQUENTIAL,
    )


@pytest.fixture
def create_autonomous_workflow_request():
    return CreateWorkflowRequest(
        name="Test Workflow",
        description="A new test workflow",
        project="demo",
        icon_url="test",
        yaml_config=test_yaml_config,
        assistants=[],
        states=[],
        mode=WorkflowMode.AUTONOMOUS,
        supervisor_prompt="test",
    )


@pytest.fixture
def update_workflow_request():
    return UpdateWorkflowRequest(
        name="Updated Workflow",
        description="An updated test workflow",
        project="demo",
        icon_url="test",
        yaml_config=test_yaml_config,
        mode=WorkflowMode.SEQUENTIAL,
    )


@pytest.fixture
def assistant_chat_request():
    return AssistantChatRequest(
        text="Test Text",
        conversation_id="conv_123",
        stream=True,
        llm_model="Test Model",
    )


@pytest.fixture
def mock_threaded_generator():
    with patch("core.thread.ThreadedGenerator") as mock:
        yield mock


@pytest.fixture
def mock_serve_data():
    with patch("rest_api.routers.workflow.serve_data") as mock:
        yield mock


@pytest.fixture(autouse=True)
def override_auth():
    """Override authentication to return the global user instance."""
    from codemie.rest_api.security.authentication import authenticate

    app.dependency_overrides[authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("codemie.service.workflow_config.workflow_config_index_service.WorkflowConfigIndexService.run")
async def test_list_workflows(mock_index_workflows, projects, request_header):
    mock_index_workflows.return_value = {
        "data": [workflow_config_data],
        "pagination": {"page": 1, "pages": 1, "total": 1, "per_page": 20},
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/workflows", params={"filter_by_user": True, "page": 2, "per_page": 20}, headers=request_header
        )

    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()['data']) == 1
    assert response.json()['data'][0] == workflow_config_data.model_dump()
    assert response.json()['pagination'] == {"page": 1, "pages": 1, "total": 1, "per_page": 20}

    mock_index_workflows.assert_called_once_with(
        user=user, filter_by_user=True, page=2, per_page=20, filters=None, minimal_response=True
    )

    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/workflows", headers=request_header)
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_get_workflow_by_id(workflow_config, request_header):
    with (
        patch(
            "codemie.service.workflow_service.WorkflowService.get_workflow",
            return_value=workflow_config,
        ) as mock_get_workflow,
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.service.guardrail.guardrail_service.GuardrailService.get_entity_guardrail_assignments",
            return_value=[],
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(f"/v1/workflows/id/{workflow_config.id}", headers=request_header)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == workflow_config.model_dump()
        mock_get_workflow.assert_called_once_with(workflow_config.id, user)


@pytest.mark.asyncio
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
async def test_get_workflow_by_id_no_config(mock_get_workflow, workflow_config, request_header):
    mock_get_workflow.side_effect = Exception("Workflow not found")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(f"/v1/workflows/id/{workflow_config.id}", headers=request_header)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    response_json = response.json()
    assert response_json["error"]["message"] == "Workflow not found"
    assert workflow_config.id in response_json["error"]["details"]
    assert "Please ensure the specified ID is correct" in response_json["error"]["help"]
    mock_get_workflow.assert_called_once_with(workflow_config.id, user)


@pytest.mark.asyncio
async def test_get_workflow_by_id_access_denied(workflow_config, request_header):
    with (
        patch(
            "codemie.service.workflow_service.WorkflowService.get_workflow",
            return_value=workflow_config,
        ) as mock_get_workflow,
        patch("codemie.core.ability.Ability.can", return_value=False),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(f"/v1/workflows/id/{workflow_config.id}", headers=request_header)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        mock_get_workflow.assert_called_once_with(workflow_config.id, user)


@pytest.mark.asyncio
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_entity_guardrail_assignments")
async def test_create_workflow(mock_get_guardrail_assignments, create_workflow_request, request_header):
    with (
        patch(
            "codemie.service.workflow_service.WorkflowService.create_workflow",
            return_value=workflow_config_data,
        ),
        patch(
            "codemie.service.workflow_service.WorkflowService.save_workflow_schema",
        ),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw"),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow") as workflow_executor,
        patch("codemie.rest_api.routers.workflow.project_access_check"),
    ):
        mock_get_guardrail_assignments.return_value = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/workflows", json=create_workflow_request.model_dump(), headers=request_header)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Workflow created successfully"
        assert response.json()["data"] == workflow_config_data.model_dump()
        workflow_executor.assert_called_once_with(
            workflow_config=WorkflowConfig(**create_workflow_request.model_dump()), user=user, error_format='string'
        )


@pytest.mark.asyncio
async def test_create_autonomous_workflow(create_autonomous_workflow_request, request_header):
    with (
        patch(
            "codemie.service.workflow_service.WorkflowService.create_workflow",
            return_value=workflow_config_data,
        ),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow"),
        patch("codemie.service.workflow_service.WorkflowService.save_workflow_schema"),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw"),
        patch("codemie.rest_api.routers.workflow.project_access_check"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/workflows", json=create_autonomous_workflow_request.model_dump(), headers=request_header
            )
        assert response.status_code == status.HTTP_410_GONE


@pytest.mark.asyncio
@patch("codemie.core.ability.Ability.can")
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_entity_guardrail_assignments")
async def test_update_workflow(
    mock_get_guardrail_assignments, mock_get_wf, mock_ability, workflow_config, update_workflow_request, request_header
):
    mock_get_guardrail_assignments.return_value = None
    mock_get_wf.return_value = workflow_config
    mock_ability.return_value = True

    with (
        patch("codemie.service.workflow_service.WorkflowService.get_workflow", return_value=workflow_config),
        patch("codemie.service.workflow_service.WorkflowService.update_workflow", return_value=workflow_config_data),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow") as workflow_executor,
        patch("codemie.service.workflow_service.WorkflowService.save_workflow_schema"),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw"),
        patch("codemie.rest_api.routers.workflow.project_access_check"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put(
                f"/v1/workflows/{workflow_config.id}",
                json=update_workflow_request.model_dump(),
                headers=request_header,
            )
        result = response.json().get("message", "")
        assert result
        assert result == "Workflow updated successfully"
        workflow_executor.assert_called_once_with(
            workflow_config=WorkflowConfig(**update_workflow_request.model_dump()), user=user, error_format='string'
        )
        assert response.json()["data"] == workflow_config_data.model_dump()


@pytest.mark.asyncio
@patch("codemie.core.ability.Ability.can")
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
async def test_update_workflow_no_permissions(
    mock_get_wf, mock_ability, workflow_config, update_workflow_request, request_header
):
    mock_get_wf.return_value = workflow_config
    mock_ability.return_value = False

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            f"/v1/workflows/{workflow_config.id}",
            json=update_workflow_request.model_dump(),
            headers=request_header,
        )

        assert response.status_code == 401
        assert response.json()['error']['message'] == 'Access denied'


@pytest.mark.asyncio
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
async def test_update_workflow_not_found(mock_get_wf, workflow_config, update_workflow_request, request_header):
    mock_get_wf.side_effect = KeyError()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            f"/v1/workflows/{workflow_config.id}",
            json=update_workflow_request.model_dump(),
            headers=request_header,
        )

        assert response.status_code == 404
        assert response.json()['error']['message'] == 'Workflow not found'


@pytest.mark.asyncio
@pytest.mark.asyncio
@patch("codemie.core.ability.Ability.can")
@patch("codemie.service.workflow_service.WorkflowService.get_workflow")
async def test_update_workflow_exception(mock_get_wf, mock_ability, update_workflow_request, request_header):
    mock_get_wf.return_value = workflow_config
    mock_ability.return_value = True

    with patch(
        "codemie.service.workflow_service.WorkflowService.update_workflow",
        side_effect=Exception("Update failed"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put(
                "/v1/workflows/nonexistent_id",
                json=update_workflow_request.model_dump(),
                headers=request_header,
            )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_delete_workflow(workflow_config, request_header):
    with (
        patch(
            "codemie.service.workflow_service.WorkflowService.get_workflow",
            return_value=workflow_config,
        ),
        patch("codemie.service.workflow_service.WorkflowService.delete_workflow") as mock_delete_workflow,
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            'codemie.service.guardrail.guardrail_service.GuardrailService.remove_guardrail_assignments_for_entity',
            return_value=None,
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.delete(f"/v1/workflows/{workflow_config.id}", headers=request_header)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Specified workflow removed"
        mock_delete_workflow.assert_called_once_with(workflow_config, user)


@pytest.mark.asyncio
async def test_delete_workflow_access_denied(workflow_config, request_header):
    with (
        patch(
            "codemie.service.workflow_service.WorkflowService.get_workflow",
            return_value=workflow_config,
        ),
        patch("codemie.core.ability.Ability.can", return_value=False),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.delete(f"/v1/workflows/{workflow_config.id}", headers=request_header)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_create_workflow_exception(create_workflow_request, request_header):
    with (
        patch(
            "codemie.service.workflow_service.WorkflowService.create_workflow",
            side_effect=Exception("Creation failed"),
        ),
        patch("codemie.rest_api.routers.workflow.project_access_check"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/workflows", json=create_workflow_request.model_dump(), headers=request_header)
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
@patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw")
async def test_create_workflow_diagram_success(mock_draw, create_workflow_request, request_header):
    mock_draw.return_value = b"diagram_data"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            CREATE_DIAGRAM_PATH,
            json=create_workflow_request.model_dump(),
            headers=request_header,
        )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"] == "data:image/svg+xml;base64,ZGlhZ3JhbV9kYXRh"
    assert response.json()["message"] == "Workflow diagram generated successfully"


@pytest.mark.asyncio
@patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw")
async def test_create_workflow_diagram_no_diagram(mock_draw, create_workflow_request, request_header):
    mock_draw.return_value = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            CREATE_DIAGRAM_PATH,
            json=create_workflow_request.model_dump(),
            headers=request_header,
        )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["error"]["details"] == "Mermaid is not available."


@pytest.mark.asyncio
@patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw")
async def test_create_workflow_diagram_invalid_config(mock_draw, create_workflow_request, request_header):
    mock_draw.side_effect = Exception("Workflow diagram creation failed")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            CREATE_DIAGRAM_PATH,
            json=create_workflow_request.model_dump(),
            headers=request_header,
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["details"] == "Workflow diagram creation failed"


@patch('codemie.service.workflow_service.WorkflowService.get_prebuilt_workflows')
def test_get_prebuilt_workflows_logged_out(mock_get_prebuilt_workflows):
    response = client.get("/v1/workflows/prebuilt")

    assert response.status_code == status.HTTP_200_OK
    assert all(workflow["project"] == user.current_project for workflow in response.json())
    mock_get_prebuilt_workflows.assert_called_once()


@patch('codemie.service.workflow_service.WorkflowService.get_prebuilt_workflows')
def test_get_prebuilt_workflow_by_slug_logged_out(mock_get_prebuilt_workflows):
    app.dependency_overrides = {}  # Ensure no auth override
    expected_error = "Missing user-id header for local authentication"

    response = client.get("/v1/workflows/prebuilt/example_slug")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error"]["details"] == expected_error
    mock_get_prebuilt_workflows.assert_not_called()


@patch.object(WorkflowService, '_cached_prebuilt_workflows', new_callable=list)
def test_get_prebuilt_workflows_with_project(mock_cached_workflows):
    wf = WorkflowConfigTemplate(name="Cached Workflow", description="Example", slug="test")
    assert wf.project == DEMO_PROJECT
    mock_cached_workflows.append(wf)

    response = client.get(
        "/v1/workflows/prebuilt", headers={"user-id": user.id, "username": user.username, "name": user.name}
    )

    assert response.status_code == status.HTTP_200_OK
    assert all(workflow["project"] == user.current_project for workflow in response.json())


@pytest.mark.asyncio
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_entity_guardrail_assignments")
async def test_create_workflow_calls_project_access_check(
    mock_get_guardrail_assignments, create_workflow_request, request_header
):
    """Test that create_workflow calls project_access_check with correct parameters"""
    with (
        patch(
            "codemie.service.workflow_service.WorkflowService.create_workflow",
            return_value=workflow_config_data,
        ),
        patch(
            "codemie.service.workflow_service.WorkflowService.save_workflow_schema",
        ),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw"),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow"),
        patch("codemie.rest_api.routers.workflow.project_access_check") as mock_project_access_check,
    ):
        mock_get_guardrail_assignments.return_value = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/workflows", json=create_workflow_request.model_dump(), headers=request_header)

        assert response.status_code == status.HTTP_200_OK
        mock_project_access_check.assert_called_once_with(user, "demo")


@pytest.mark.asyncio
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_entity_guardrail_assignments")
async def test_update_workflow_calls_project_access_check(
    mock_get_guardrail_assignments, workflow_config, update_workflow_request, request_header
):
    """Test that update_workflow calls project_access_check with correct parameters"""
    with (
        patch("codemie.service.workflow_service.WorkflowService.get_workflow", return_value=workflow_config),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.service.workflow_service.WorkflowService.update_workflow", return_value=workflow_config_data),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow"),
        patch("codemie.service.workflow_service.WorkflowService.save_workflow_schema"),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw"),
        patch("codemie.rest_api.routers.workflow.project_access_check") as mock_project_access_check,
    ):
        mock_get_guardrail_assignments.return_value = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put(
                f"/v1/workflows/{workflow_config.id}",
                json=update_workflow_request.model_dump(),
                headers=request_header,
            )

        assert response.status_code == status.HTTP_200_OK
        mock_project_access_check.assert_called_once_with(user, "demo")


@pytest.mark.asyncio
async def test_create_workflow_project_access_denied(create_workflow_request, request_header):
    """Test that create_workflow returns 403 when user doesn't have access to project"""

    user_no_access = User(id="123", username="testuser", name="Test User", project_names=["other_project"])
    app.dependency_overrides[workflow_router.authenticate] = lambda: user_no_access

    with (
        patch("codemie.service.workflow_service.WorkflowService.create_workflow", return_value=None) as mock_create,
        patch(
            "codemie.service.workflow_service.WorkflowService.save_workflow_schema",
        ),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw"),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow"),
        patch(
            "codemie.rest_api.routers.workflow.project_access_check",
            side_effect=ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Access denied",
                details="You do not have permission to access the project 'demo'.",
                help="If you believe you should have access to this project, please contact your system administrator or the project owner.",
            ),
        ) as mock_project_access_check,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/workflows",
                json=create_workflow_request.model_dump(),
                headers=request_header,
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["error"]["message"] == "Access denied"
        assert "You do not have permission to access the project 'demo'" in response.json()["error"]["details"]
        mock_project_access_check.assert_called_once_with(user_no_access, "demo")
        mock_create.assert_not_called()

        # Restore original dependency
        app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_update_workflow_project_access_denied(workflow_config, update_workflow_request, request_header):
    """Test that update_workflow returns 403 when user doesn't have access to project"""

    user_no_access = User(id="123", username="testuser", name="Test User", project_names=["other_project"])
    app.dependency_overrides[workflow_router.authenticate] = lambda: user_no_access
    with (
        patch("codemie.service.workflow_service.WorkflowService.get_workflow", return_value=workflow_config),
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.service.workflow_service.WorkflowService.update_workflow", return_value=None) as mock_update,
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow"),
        patch("codemie.service.workflow_service.WorkflowService.save_workflow_schema"),
        patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw"),
        patch(
            "codemie.rest_api.routers.workflow.project_access_check",
            side_effect=ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Access denied",
                details="You do not have permission to access the project 'demo'.",
                help="If you believe you should have access to this project, please contact your system administrator or the project owner.",
            ),
        ) as mock_project_access_check,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put(
                f"/v1/workflows/{workflow_config.id}",
                json=update_workflow_request.model_dump(),
                headers=request_header,
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["error"]["message"] == "Access denied"
        assert "You do not have permission to access the project 'demo'" in response.json()["error"]["details"]
        mock_project_access_check.assert_called_once_with(user_no_access, "demo")
        mock_update.assert_not_called()

        # Restore original dependency
        app.dependency_overrides = {}


@pytest.mark.asyncio
@patch("codemie.service.workflow_config.workflow_config_index_service.WorkflowConfigIndexService.get_users")
async def test_get_workflow_users(mock_get_users, request_header):
    """Test that /workflows/users endpoint returns list of users who created workflows"""
    from codemie.core.models import CreatedByUser

    mock_users = [
        CreatedByUser(id="user1", username="user1", name="User One"),
        CreatedByUser(id="user2", username="user2", name="User Two"),
    ]
    mock_get_users.return_value = mock_users

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/workflows/users", headers=request_header)

    assert response.status_code == status.HTTP_200_OK
    assert len(response.json()) == 2
    assert response.json()[0]["id"] == "user1"
    assert response.json()[0]["username"] == "user1"
    assert response.json()[0]["name"] == "User One"
    assert response.json()[1]["id"] == "user2"
    mock_get_users.assert_called_once_with(user=user)
