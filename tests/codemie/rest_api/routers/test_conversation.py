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
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from codemie.rest_api.main import app
from codemie.rest_api.models.conversation import (
    Conversation,
    ConversationFinishBulkRequest,
    ConversationFinishResult,
    ConversationListItem,
    GeneratedMessage,
)
from codemie.rest_api.models.conversation_folder import ConversationFolder
from codemie.rest_api.security.user import User
import codemie.rest_api.routers.conversation as conversation_router
from unittest.mock import patch, MagicMock
from pydantic import ValidationError
from codemie.rest_api.models.assistant import Assistant


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


@pytest.mark.asyncio
async def test_get_new_conversation_within_assistant_chat(user):
    assistant = Assistant(
        id="asst-1",
        name="Assistant One",
        description="",
        system_prompt="",
        project="demo",
        icon_url="http://example.com/icon.png",
        toolkits=[],
        conversation_starters=["Hi"],
        shared=True,
    )

    with (
        patch(
            "codemie.service.conversation_service.ConversationService.build_new_conversation",
            return_value=Conversation(
                id="new",
                conversation_id="new",
                user_id=user.id,
                user_name=user.name,
                initial_assistant_id="asst-1",
                assistant_ids=["asst-1"],
                assistant_data=[
                    {
                        "assistant_id": "asst-1",
                        "assistant_name": "Assistant One",
                        "assistant_icon": "http://example.com/icon.png",
                        "assistant_type": assistant.type,
                        "context": None,
                        "tools": None,
                        "conversation_starters": ["Hi"],
                    }
                ],
                history=[],
            ),
        ) as mock_build,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                "/v1/conversations/new?initial_assistant_id=asst-1",
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == 200
        data = response.json()

        assert data["conversation_id"] == "new"
        assert data["initial_assistant_id"] == "asst-1"
        assert data["assistant_ids"] == ["asst-1"]
        assert "assistant_data" in data
        assert len(data["assistant_data"]) == 1
        assert data["assistant_data"][0]["assistant_id"] == "asst-1"
        assert data["assistant_data"][0]["assistant_name"] == "Assistant One"
        assert data["assistant_data"][0]["assistant_icon"] == "http://example.com/icon.png"
        assert data["assistant_data"][0]["conversation_starters"] == ["Hi"]

        mock_build.assert_called_once()


@pytest.mark.asyncio
async def test_get_conversation_by_id_includes_finished_at(user):
    conversation = Conversation(
        id="456",
        conversation_id="456",
        user_id=user.id,
        name="Test Conversation",
        finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )
    with (
        patch(
            "codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=conversation
        ) as mock_find_by_id,
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
        assert body["finished_at"] is not None
        mock_find_by_id.assert_called_once_with(conversation.id)


@pytest.mark.asyncio
async def test_finish_conversation_success(user):
    conversation = Conversation(
        id="456",
        conversation_id="456",
        user_id=user.id,
        history=[],
    )
    mock_session = MagicMock()
    mock_session.__enter__.return_value = mock_session
    mock_session.__exit__.return_value = False
    mock_session.execute.return_value.rowcount = 1
    with (
        patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=conversation),
        patch("codemie.rest_api.routers.conversation.Ability.can", return_value=True),
        patch("codemie.service.conversation_service.get_session", return_value=mock_session),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/conversations/456/finish", headers={"Authorization": "Bearer testtoken"})

    assert response.status_code == 200
    body = response.json()
    assert body["conversationId"] == "456"
    assert body["finishedAt"] is not None
    assert "alreadyFinished" not in body
    assert "error" not in body


@pytest.mark.asyncio
async def test_finish_conversation_raises_409_when_already_finished(user):
    conversation = Conversation(
        id="456",
        conversation_id="456",
        user_id=user.id,
        history=[],
        finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )
    with (
        patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=conversation),
        patch("codemie.rest_api.routers.conversation.Ability.can", return_value=True),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/conversations/456/finish", headers={"Authorization": "Bearer testtoken"})

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_finish_conversation_404_when_missing(user):
    with patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=None):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/conversations/does-not-exist/finish", headers={"Authorization": "Bearer testtoken"}
            )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_finish_conversation_403_when_not_owner(user):
    conversation = Conversation(
        id="456",
        conversation_id="456",
        user_id="someone-else",
        history=[],
    )
    with (
        patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", return_value=conversation),
        patch("codemie.rest_api.routers.conversation.Ability.can", return_value=False),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/conversations/456/finish", headers={"Authorization": "Bearer testtoken"})
    assert response.status_code == 403


def test_admin_finish_conversations_bulk_mixed_results():
    expected = [
        ConversationFinishResult(
            conversation_id="conv-1",
            already_finished=False,
            finished_at=datetime(2026, 8, 11, 12, 0, 0),
        ),
        ConversationFinishResult(
            conversation_id="conv-2",
            already_finished=False,
            finished_at=None,
            error="not_found",
        ),
    ]
    payload = ConversationFinishBulkRequest(conversation_ids=["conv-1", "conv-2"])

    with patch(
        "codemie.rest_api.routers.conversation.ConversationService.finish_conversations_bulk",
        return_value=expected,
    ):
        response = conversation_router.admin_finish_conversations_bulk(payload)

    assert response.total == 2
    results_by_id = {r.conversation_id: r for r in response.results}
    assert results_by_id["conv-1"].finished_at is not None
    assert results_by_id["conv-2"].error == "not_found"


def test_conversation_finish_bulk_request_rejects_empty_list():
    with pytest.raises(ValidationError):
        ConversationFinishBulkRequest(conversation_ids=[])


def test_list_admin_conversations_filters_by_is_finished():
    items = [
        ConversationListItem(id="conv-1", date=datetime(2026, 8, 11, 10, 0, 0), finished_at=None),
    ]
    with patch(
        "codemie.rest_api.routers.conversation.Conversation.get_all_conversations_admin",
        return_value=(items, 1),
    ) as mock_query:
        response = conversation_router.list_admin_conversations(
            is_finished=False, started_after=None, project=None, page=0, per_page=20
        )

    assert response.pagination.total == 1
    assert all(item.finished_at is None for item in response.data)
    mock_query.assert_called_once_with(is_finished=False, started_after=None, project=None, page=0, per_page=20)


def test_list_admin_conversations_filters_by_project():
    items = [
        ConversationListItem(id="conv-1", date=datetime(2026, 8, 11, 10, 0, 0)),
    ]
    with patch(
        "codemie.rest_api.routers.conversation.Conversation.get_all_conversations_admin",
        return_value=(items, 1),
    ) as mock_query:
        response = conversation_router.list_admin_conversations(
            is_finished=None, started_after=None, project="my-app", page=0, per_page=20
        )

    assert response.pagination.total == 1
    mock_query.assert_called_once_with(is_finished=None, started_after=None, project="my-app", page=0, per_page=20)


def test_list_admin_conversations_paginates():
    items = [ConversationListItem(id="conv-1", date=datetime(2026, 8, 11, 10, 0, 0))]
    with patch(
        "codemie.rest_api.routers.conversation.Conversation.get_all_conversations_admin",
        return_value=(items, 3),
    ):
        response = conversation_router.list_admin_conversations(
            is_finished=None, started_after=None, project=None, page=0, per_page=1
        )

    assert response.pagination.total == 3
    assert len(response.data) == 1
    assert response.pagination.pages == 3


def test_admin_conversation_routes_use_admin_access_only():
    from codemie.rest_api.security.authentication import admin_access_only

    def _find_route(path: str, method: str):
        for route in conversation_router.router.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return route
        raise AssertionError(f"{method} {path} route not found")

    for path, method in (
        ("/v1/admin/conversations/finish-bulk", "POST"),
        ("/v1/admin/conversations", "GET"),
    ):
        route = _find_route(path, method)
        dependency_functions = [dep.call for dep in route.dependant.dependencies]
        assert admin_access_only in dependency_functions, f"{method} {path} should use admin_access_only"


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
        # pending_checkpoint is an ORM-only column excluded from ConversationResponse
        expected.pop("pending_checkpoint", None)
        # pending_tool_call is populated separately; not part of the raw conversation dict
        expected.pop("pending_tool_call", None)
        body.pop("pending_tool_call", None)
        assert body == expected
        mock_get_by_id.assert_called_once_with(conversation.id)


@pytest.mark.asyncio
async def test_update_conversation_with_image_generation_fields(user):
    conversation = Conversation(
        id="456",
        conversation_id="456",
        name="Test Conversation",
        enable_image_generation=True,
        image_generation_model="gpt-image-1",
    )

    with (
        patch("codemie.rest_api.routers.conversation.Conversation.get_by_id", return_value=conversation),
        patch("codemie.rest_api.routers.conversation.Ability.can", return_value=True),
        patch(
            "codemie.rest_api.routers.conversation.ConversationService.update_conversation", return_value=conversation
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put(
                "/v1/conversations/456",
                json={
                    "enable_image_generation": True,
                    "image_generation_model": "gpt-image-1",
                },
                headers={"Authorization": "Bearer testtoken"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["enable_image_generation"] is True
    assert body["image_generation_model"] == "gpt-image-1"


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
# Tests for search_conversations endpoint
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
_OLDER = _NOW - timedelta(hours=1)


def _make_chat_item(name: str, updated_at: datetime, folder: str = None) -> ConversationListItem:
    return ConversationListItem(
        id='chat-1',
        name=name,
        folder=folder,
        date=updated_at,
        update_date=updated_at,
    )


def _make_folder_item(name: str, updated_at: datetime) -> ConversationFolder:
    return ConversationFolder(
        id='folder-1',
        folder_name=name,
        user_id='123',
        update_date=updated_at,
        date=updated_at,
    )


class TestSearchConversations:
    def test_search_conversations_success(self):
        """Test /v1/conversations/search returns combined results"""
        chat = _make_chat_item('admin chat', _NOW)
        folder = _make_folder_item('admin folder', _OLDER)

        with (
            patch(
                'codemie.rest_api.routers.conversation.Conversation.search_by_name_and_user',
                return_value=[chat],
            ),
            patch(
                'codemie.rest_api.routers.conversation.ConversationFolder.search_by_name_and_user',
                return_value=[folder],
            ),
        ):
            response = client.get(
                '/v1/conversations/search',
                params={'query': 'admin'},
                headers={'Authorization': 'Bearer testtoken'},
            )

        assert response.status_code == 200
        data = response.json()

        assert 'items' in data
        assert len(data['items']) > 0

        # Verify items have required fields
        item = data['items'][0]
        assert 'name' in item
        assert 'updated_at' in item
        assert 'type' in item
        assert item['type'] in ['chat', 'folder']
        chat_item = next(i for i in data['items'] if i['type'] == 'chat')
        assert 'finished_at' in chat_item

    def test_search_conversations_requires_auth(self):
        """Test search endpoint requires authentication when override is cleared"""
        # Temporarily clear the dependency override so auth is enforced
        app.dependency_overrides = {}
        try:
            response = client.get('/v1/conversations/search', params={'query': 'test'})
            assert response.status_code == 401
        finally:
            # Restore the override for subsequent tests
            app.dependency_overrides[conversation_router.authenticate] = lambda: User(
                id='123', username='testuser', name='Test User'
            )

    def test_search_conversations_validation_min_length(self):
        """Test query must be at least 3 characters"""
        response = client.get(
            '/v1/conversations/search',
            params={'query': 'ab'},
            headers={'Authorization': 'Bearer testtoken'},
        )

        assert response.status_code == 422

    def test_search_conversations_validation_max_length(self):
        """Test query must not exceed 100 characters"""
        long_query = 'a' * 101
        response = client.get(
            '/v1/conversations/search',
            params={'query': long_query},
            headers={'Authorization': 'Bearer testtoken'},
        )

        assert response.status_code == 422

    def test_search_conversations_sorted_by_update_date(self):
        """Test results are sorted by updated_at DESC"""
        earlier = _NOW - timedelta(hours=2)
        later = _NOW

        chat_newer = _make_chat_item('test chat newer', later)
        folder_older = _make_folder_item('test folder older', earlier)

        with (
            patch(
                'codemie.rest_api.routers.conversation.Conversation.search_by_name_and_user',
                return_value=[chat_newer],
            ),
            patch(
                'codemie.rest_api.routers.conversation.ConversationFolder.search_by_name_and_user',
                return_value=[folder_older],
            ),
        ):
            response = client.get(
                '/v1/conversations/search',
                params={'query': 'test'},
                headers={'Authorization': 'Bearer testtoken'},
            )

        assert response.status_code == 200
        data = response.json()
        items = data['items']

        # Verify descending order
        for i in range(len(items) - 1):
            current_time = datetime.fromisoformat(items[i]['updated_at'])
            next_time = datetime.fromisoformat(items[i + 1]['updated_at'])
            assert current_time >= next_time

    def test_search_conversations_returns_correct_types(self):
        """Test that chats have type 'chat' and folders have type 'folder'"""
        chat = _make_chat_item('test chat', _NOW)
        folder = _make_folder_item('test folder', _OLDER)

        with (
            patch(
                'codemie.rest_api.routers.conversation.Conversation.search_by_name_and_user',
                return_value=[chat],
            ),
            patch(
                'codemie.rest_api.routers.conversation.ConversationFolder.search_by_name_and_user',
                return_value=[folder],
            ),
        ):
            response = client.get(
                '/v1/conversations/search',
                params={'query': 'test'},
                headers={'Authorization': 'Bearer testtoken'},
            )

        assert response.status_code == 200
        items = response.json()['items']
        types = {item['type'] for item in items}
        assert 'chat' in types
        assert 'folder' in types

    def test_search_conversations_empty_results(self):
        """Test search returns empty list when no matches"""
        with (
            patch(
                'codemie.rest_api.routers.conversation.Conversation.search_by_name_and_user',
                return_value=[],
            ),
            patch(
                'codemie.rest_api.routers.conversation.ConversationFolder.search_by_name_and_user',
                return_value=[],
            ),
        ):
            response = client.get(
                '/v1/conversations/search',
                params={'query': 'zzz'},
                headers={'Authorization': 'Bearer testtoken'},
            )

        assert response.status_code == 200
        data = response.json()
        assert data['items'] == []


# ---------------------------------------------------------------------------
# Regression: cascade-delete WorkflowExecution on Conversation.delete_by_id
# ---------------------------------------------------------------------------


class TestCascadeDeleteWorkflowExecutionOnConversationDelete:
    """Verify that deleting a Conversation also cascade-deletes its linked
    WorkflowExecution rows (and their children).

    These tests mock the DB session (get_engine) to avoid a live connection.
    They assert that the cascade helper issues a SELECT for execution_ids scoped
    to the deleted conversation_id, and then issues DELETE statements for the
    workflow_executions table with a WHERE clause referencing conversation_id.

    A buggy implementation that issues an unconditional DELETE (no WHERE) or
    that keys on a wrong column would fail the WHERE-scoping assertions below.
    Call-count alone cannot catch either defect.

    State model:
      State 1 — execution linked to the deleted conversation  → MUST be deleted
      State 2 — execution linked to a different conversation  → MUST NOT be deleted
      State 3 — execution with conversation_id = NULL ("Run" mode) → MUST NOT be deleted
    """

    @staticmethod
    def _compile_stmt(stmt) -> str:
        """Return the SQL string of a SQLAlchemy statement with literal parameter binds."""
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    @patch("codemie.rest_api.models.conversation.Conversation.get_engine")
    def test_cascade_select_is_scoped_to_conversation_id(self, mock_get_engine):
        """The first exec call (SELECT execution_ids) must be scoped to conversation_id.

        Proves the SELECT does not pull ALL executions unconditionally, i.e.
        state-2 (other conversation) and state-3 (NULL) rows would be excluded.
        """
        mock_engine = MagicMock()
        mock_get_engine.return_value = mock_engine

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        # The first exec call (SELECT execution_ids) returns no rows so the
        # cascade short-circuits; the DELETE conversation is the second call.
        mock_session.exec.return_value.all.return_value = []
        mock_session.exec.return_value.rowcount = 0

        target_conv_id = "conv-abc-123"

        with patch("codemie.rest_api.models.conversation.Session", return_value=mock_session):
            Conversation.delete_by_id(target_conv_id)

        assert (
            mock_session.exec.call_count >= 2
        ), "Expected at least 2 session.exec calls: SELECT execution_ids + DELETE conversation"

        # The first call is the SELECT(execution_ids) statement.
        first_call_stmt = mock_session.exec.call_args_list[0][0][0]
        compiled = self._compile_stmt(first_call_stmt)

        # SELECT must reference conversation_id in its WHERE clause.
        assert (
            "conversation_id" in compiled
        ), f"SELECT statement WHERE clause does not scope to conversation_id.\nCompiled SQL: {compiled}"
        # The target conversation_id value must appear in the compiled SQL.
        assert target_conv_id in compiled, (
            f"SELECT statement does not filter on the specific conversation_id '{target_conv_id}'.\n"
            f"Compiled SQL: {compiled}"
        )

    @patch("codemie.rest_api.models.conversation.Conversation.get_engine")
    def test_cascade_delete_execution_is_scoped_to_execution_id(self, mock_get_engine):
        """The DELETE(WorkflowExecution) statement must target workflow_executions with
        a WHERE scoped to the pre-collected execution PKs (execution_id), not by
        conversation_id.  Scoping by PK eliminates the over-delete race where a row
        inserted with the same conversation_id after the SELECT would be removed.

        A buggy implementation that omits the WHERE (deleting all executions)
        would fail the 'execution_id IN ...' assertion below, proving the delete is
        PK-scoped rather than unconditional.
        """
        mock_engine = MagicMock()
        mock_get_engine.return_value = mock_engine

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        # First exec: SELECT execution_ids → returns one linked execution_id
        exec_ids_result = MagicMock()
        exec_ids_result.all.return_value = ["exec-id-001"]

        # Second exec: SELECT state_ids → empty (no states for this execution)
        state_ids_result = MagicMock()
        state_ids_result.all.return_value = []

        # Remaining execs: DELETE transitions, DELETE states, DELETE executions,
        # DELETE conversation — all succeed silently.
        delete_result = MagicMock()
        delete_result.rowcount = 1

        mock_session.exec.side_effect = [
            exec_ids_result,  # SELECT execution_ids  (cascade step 1)
            state_ids_result,  # SELECT state_ids      (cascade step 2)
            MagicMock(),  # DELETE transitions    (cascade step 4)
            MagicMock(),  # DELETE states         (cascade step 5)
            MagicMock(),  # DELETE executions     (cascade step 6)
            delete_result,  # DELETE conversation
        ]

        target_conv_id = "conv-abc-123"

        with patch("codemie.rest_api.models.conversation.Session", return_value=mock_session):
            result = Conversation.delete_by_id(target_conv_id)

        # Sanity: 6 calls total (2 SELECTs + 4 DELETEs)
        assert mock_session.exec.call_count == 6, f"Expected 6 session.exec calls; got {mock_session.exec.call_count}"
        assert result == {"status": "deleted"}

        # Call index 4 is DELETE(WorkflowExecution) — cascade step 6.
        # (0=SELECT exec_ids, 1=SELECT state_ids, 2=DELETE transitions,
        #  3=DELETE states, 4=DELETE executions, 5=DELETE conversation)
        delete_exec_stmt = mock_session.exec.call_args_list[4][0][0]
        compiled = self._compile_stmt(delete_exec_stmt)

        # The WHERE clause must scope to the pre-collected execution PKs.
        # An unconditional DELETE (no WHERE) would not contain 'execution_id'.
        assert (
            "workflow_executions" in compiled
        ), f"DELETE statement does not target workflow_executions table.\nCompiled SQL: {compiled}"
        assert "execution_id" in compiled, (
            f"DELETE(WorkflowExecution) has no WHERE on execution_id "
            f"— delete is not PK-scoped; the over-delete race is not prevented.\n"
            f"Compiled SQL: {compiled}"
        )
        assert "exec-id-001" in compiled, (
            f"DELETE(WorkflowExecution) does not filter on the specific "
            f"execution_id 'exec-id-001' collected during the SELECT.\n"
            f"Compiled SQL: {compiled}"
        )
