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
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from codemie.rest_api.main import app
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.rest_api.models.conversation_folder import ConversationFolder
from codemie.rest_api.security.user import User
import codemie.rest_api.routers.conversation as conversation_router
from unittest.mock import patch


client = TestClient(app)


@pytest.fixture
def user():
    return User(id="123", username="testuser", name="Test User")


@pytest.fixture(autouse=True)
def override_dependency(user):
    app.dependency_overrides[conversation_router.authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.fixture
def conversation():
    return Conversation(
        id="456",
        conversation_id="456",
        name="Test Conversation",
    )


@pytest.fixture
def conversation_with_history():
    return Conversation(
        id="456",
        conversation_id="456",
        name="Test Conversation",
        history=[GeneratedMessage(message="test", role="User"), GeneratedMessage(message="test", role="Assistant")],
    )


@pytest.fixture
def conversation_folder(user):
    return ConversationFolder(folder_name="Test folder", id="test", user_id=user.id)


@pytest.mark.asyncio
async def test_get_conversation_by_id(user, conversation):
    with (
        patch(
            "codemie.rest_api.routers.conversation.Conversation.get_by_id", return_value=conversation
        ) as mock_get_by_id,
        patch("codemie.rest_api.routers.conversation.Ability.can", return_value=True),
        patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", return_value=[]),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/conversations/{conversation.id}", headers={"Authorization": "Bearer testtoken"}
            )

        assert response.status_code == 200
        body = response.json()
        expected = {
            **conversation.dict(),
            "very_first_msg_at": None,
            "very_last_msg_at": None,
        }
        assert body == expected
        mock_get_by_id.assert_called_once_with(conversation.id)


CONVETSATION_MSG_EXPORT_PATH = "/v1/conversations/123/history/0/0/export?export_format=pdf"


@pytest.mark.asyncio
@patch("codemie.rest_api.routers.conversation.Ability.can", return_value=True)
@patch("codemie.service.conversation.MessageExporter.export_single_message")
@patch("codemie.rest_api.routers.conversation.Conversation.get_by_id")
async def test_export_conversation_message(
    mock_get_conversation, mock_export_service, _mock_ability, conversation_with_history
):
    mock_get_conversation.return_value = conversation_with_history
    mock_export_service.return_value = iter([b"ok"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as ac:
        response = await ac.get(CONVETSATION_MSG_EXPORT_PATH, headers={"Authorization": "Bearer testtoken"})

    assert response.status_code == 200
    assert response.text == "ok"


@pytest.mark.asyncio
@patch("codemie.rest_api.routers.conversation.Conversation.get_by_id")
async def test_export_conversation_message_not_found(mock_get_conversation):
    mock_get_conversation.return_value = None

    response = client.get(CONVETSATION_MSG_EXPORT_PATH)
    assert response.status_code == 404
    assert response.json()['error']['message'] == "Conversation not found"


# Tests for deleted workflow handling
@pytest.fixture
def workflow_conversation():
    """Conversation for a workflow"""
    return Conversation(
        id="789",
        conversation_id="789",
        name="Workflow Conversation",
        is_workflow_conversation=True,
        initial_assistant_id="workflow-123",
    )


@pytest.mark.asyncio
async def test_get_conversation_with_deleted_workflow(user, workflow_conversation):
    """Test that getting a conversation with a deleted workflow returns fallback data"""
    from codemie.core.workflow_models import WorkflowConfig

    with (
        patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=workflow_conversation),
        patch("codemie.rest_api.routers.conversation.Ability.can", return_value=True),
        patch.object(WorkflowConfig, "get_by_id", side_effect=KeyError("Workflow not found")) as mock_get_workflow,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/conversations/{workflow_conversation.id}", headers={"Authorization": "Bearer testtoken"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data['id'] == workflow_conversation.id
        # Check that assistant_data is populated with fallback
        assert 'assistant_data' in data
        assert len(data['assistant_data']) == 1
        assert data['assistant_data'][0]['assistant_id'] == "workflow-123"
        assert data['assistant_data'][0]['assistant_name'] is None  # Backend returns None, UI handles display
        mock_get_workflow.assert_called_once_with(workflow_conversation.initial_assistant_id)


@pytest.mark.asyncio
async def test_get_conversation_with_existing_workflow(user, workflow_conversation):
    """Test that getting a conversation with an existing workflow works normally"""
    from codemie.core.workflow_models.workflow_config import WorkflowConfig, WorkflowMode

    workflow = WorkflowConfig(
        id="workflow-123",
        name="Test Workflow",
        icon_url="http://example.com/icon.png",
        description="Test workflow",
        mode=WorkflowMode.SEQUENTIAL,
        project="test_project",
    )

    with (
        patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=workflow_conversation),
        patch("codemie.rest_api.routers.conversation.Ability.can", return_value=True),
        patch.object(WorkflowConfig, "get_by_id", return_value=workflow) as mock_get_workflow,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/conversations/{workflow_conversation.id}", headers={"Authorization": "Bearer testtoken"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data['id'] == workflow_conversation.id
        # Check that assistant_data is populated with actual workflow data
        assert 'assistant_data' in data
        assert len(data['assistant_data']) == 1
        assert data['assistant_data'][0]['assistant_id'] == "workflow-123"
        assert data['assistant_data'][0]['assistant_name'] == "Test Workflow"
        assert data['assistant_data'][0]['assistant_icon'] == "http://example.com/icon.png"
        mock_get_workflow.assert_called_once_with(workflow_conversation.initial_assistant_id)


@pytest.mark.asyncio
@patch("codemie.rest_api.routers.conversation.Ability.can", return_value=False)
@patch("codemie.rest_api.routers.conversation.Conversation.get_by_id")
async def test_export_conversation_message_permission_err(mock_get_conversation, _mock_ability):
    mock_get_conversation.return_value = conversation_with_history

    response = client.get(CONVETSATION_MSG_EXPORT_PATH)
    assert response.status_code == 401
    assert response.json()['error']['message'] == 'Access denied'


@pytest.mark.asyncio
@patch("codemie.rest_api.routers.conversation.ConversationFolder.get_all_by_fields")
async def test_get_conversation_folder_list(mock_get_all_by_id, conversation_folder):
    mock_get_all_by_id.return_value = [conversation_folder]

    response = client.get("/v1/conversations/folders/list", headers={"Authorization": "Bearer testtoken"})
    assert response.status_code == 200

    data = response.json()
    assert data

    folder_ids = [folder["id"] for folder in data]
    assert len(folder_ids) == len(set(folder_ids)), "Folder ids mismatch"
    assert folder_ids[0] == conversation_folder.id


# ---------------------------------------------------------------------------
# Tests for resume_conversation endpoint
# ---------------------------------------------------------------------------

RESUME_URL = "/v1/conversations/{conv_id}/resume"


def _interrupted_workflow_conversation() -> Conversation:
    return Conversation(
        id="conv-resume",
        conversation_id="conv-resume",
        name="Workflow Conv",
        is_workflow_conversation=True,
        initial_assistant_id="wf-1",
        history=[
            GeneratedMessage(role="User", message="hello", history_index=0),
            GeneratedMessage(
                role="Assistant",
                message=None,
                history_index=1,
                workflow_execution_ref=True,
                execution_id="exec-001",
            ),
        ],
    )


def _interrupted_execution():
    from unittest.mock import MagicMock
    from codemie.core.workflow_models import WorkflowExecutionStatusEnum

    execution = MagicMock()
    execution.overall_status = WorkflowExecutionStatusEnum.INTERRUPTED
    execution.workflow_id = "wf-1"
    return execution


class TestResumeConversation:
    @pytest.mark.asyncio
    async def test_conversation_not_found_returns_404(self):
        with patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=None):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                response = await ac.post(RESUME_URL.format(conv_id="missing"))

        assert response.status_code == 404
        assert response.json()["error"]["message"] == "Conversation not found"

    @pytest.mark.asyncio
    async def test_not_workflow_conversation_returns_400(self):
        plain = Conversation(id="conv-1", conversation_id="conv-1", name="Plain")
        with (
            patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=plain),
            patch("codemie.rest_api.routers.conversation.Ability.can", return_value=True),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                response = await ac.post(RESUME_URL.format(conv_id="conv-1"))

        assert response.status_code == 400
        assert response.json()["error"]["message"] == "Not a workflow conversation"

    @pytest.mark.asyncio
    async def test_no_execution_id_in_history_returns_404(self):
        conv = Conversation(
            id="conv-1",
            conversation_id="conv-1",
            name="Workflow conv",
            is_workflow_conversation=True,
            history=[GeneratedMessage(role="User", message="hi", history_index=0)],
        )
        with (
            patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=conv),
            patch("codemie.rest_api.routers.conversation.Ability.can", return_value=True),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                response = await ac.post(RESUME_URL.format(conv_id="conv-1"))

        assert response.status_code == 404
        assert response.json()["error"]["message"] == "No workflow execution found"

    @pytest.mark.asyncio
    async def test_execution_not_found_returns_404(self):
        conv = _interrupted_workflow_conversation()
        with (
            patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=conv),
            patch("codemie.rest_api.routers.conversation.Ability.can", return_value=True),
            patch("codemie.service.workflow_service.WorkflowService.find_workflow_execution_by_id", return_value=None),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                response = await ac.post(RESUME_URL.format(conv_id="conv-resume"))

        assert response.status_code == 404
        assert response.json()["error"]["message"] == "Workflow execution not found"

    @pytest.mark.asyncio
    async def test_execution_not_interrupted_returns_409(self):
        from codemie.core.workflow_models import WorkflowExecutionStatusEnum
        from unittest.mock import MagicMock

        execution = MagicMock()
        execution.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED
        conv = _interrupted_workflow_conversation()
        with (
            patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=conv),
            patch("codemie.rest_api.routers.conversation.Ability.can", return_value=True),
            patch(
                "codemie.service.workflow_service.WorkflowService.find_workflow_execution_by_id",
                return_value=execution,
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                response = await ac.post(RESUME_URL.format(conv_id="conv-resume"))

        assert response.status_code == 409
        assert response.json()["error"]["message"] == "Workflow execution is not interrupted"

    @pytest.mark.asyncio
    async def test_success_marks_interrupted_thoughts_as_completed(self):
        from unittest.mock import MagicMock
        from starlette.responses import Response

        execution = _interrupted_execution()
        conv = _interrupted_workflow_conversation()
        mock_workflow = MagicMock()

        with (
            patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=conv),
            patch("codemie.rest_api.routers.conversation.Ability.can", return_value=True),
            patch(
                "codemie.service.workflow_service.WorkflowService.find_workflow_execution_by_id",
                return_value=execution,
            ),
            patch("codemie.service.workflow_service.WorkflowService.get_workflow", return_value=MagicMock()),
            patch(
                "codemie.workflows.workflow.WorkflowExecutor.create_executor", return_value=mock_workflow
            ) as mock_create,
            patch(
                "codemie.rest_api.routers.utils._handle_streaming_execution",
                return_value=Response(content="ok", status_code=200),
            ),
            patch("codemie.core.dependecies.set_disable_prompt_cache"),
            patch("codemie.core.thread.ThreadedGenerator"),
            patch("codemie.core.thought_queue.ThoughtQueue"),
            patch("codemie.core.dual_queue.DualQueue"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                response = await ac.post(RESUME_URL.format(conv_id="conv-resume"))

        assert response.status_code == 200
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["resume_execution"] is True
        assert call_kwargs["execution_id"] == "exec-001"
