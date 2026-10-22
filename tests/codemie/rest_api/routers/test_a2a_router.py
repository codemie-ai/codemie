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

# ruff: noqa: E402

import json
import sys
from unittest.mock import MagicMock, patch

# Stub optional native packages that may not be available/compiled in all environments
# (e.g. switchyard requires Rust/C++ compilers on Windows).
# This must run before any codemie imports so the stubs are in place at import time.
for _missing_pkg in ("switchyard", "switchyard.libsy"):
    if _missing_pkg not in sys.modules:
        sys.modules[_missing_pkg] = MagicMock()

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import attributes

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.a2a.types import AgentCard, Task, TaskStatus, TaskState
from codemie.rest_api.main import app
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.routers import a2a
from codemie.rest_api.security.user import User
from tests.codemie.rest_api.a2a.fixtures import load_fixture


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_user():
    # Patch config so is_admin is not forced to True by the dev/local environment check
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(
            id="user-1",
            username="tester",
            name="Test User",
            email="tester@example.com",
            project_names=["project1"],
            admin_project_names=[],
            knowledge_bases=[],
            user_type="regular",
            is_admin=False,
        )


@pytest.fixture
def mock_unauthorized_user():
    # Patch config so is_admin is not forced to True by the dev/local environment check
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(
            id="user-2",
            username="unauthorized",
            name="Unauthorized User",
            email="unauthorized@example.com",
            project_names=["unrelated-project"],
            admin_project_names=[],
            knowledge_bases=[],
            user_type="regular",
            is_admin=False,
        )


@pytest.fixture
def private_assistant():
    return Assistant(
        id="private-id-123",
        name="Private Assistant",
        description="Private Description",
        system_prompt="Private system prompt",
        project="project1",
        shared=True,
        is_global=False,
    )


@pytest.fixture
def global_assistant():
    return Assistant(
        id="global-id-123",
        name="Global Assistant",
        description="Global Description",
        system_prompt="Global system prompt",
        project="global-project",
        shared=True,
        is_global=True,
    )


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
async def test_get_agent_card_anonymous_raises_401_on_private_assistant(
    mock_authenticate, mock_find_by_id, private_assistant
):
    """Anonymous request to private/project assistant must return 401 Unauthorized."""
    mock_find_by_id.return_value = private_assistant
    mock_authenticate.side_effect = ExtendedHTTPException(
        code=status.HTTP_401_UNAUTHORIZED,
        message="Authentication failed",
        details="No valid credentials provided.",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/v1/a2a/assistants/private-id-123/.well-known/agent-card.json")

    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["message"] == "Authentication failed"
    mock_authenticate.assert_called_once()
    mock_find_by_id.assert_called_once_with("private-id-123")


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
async def test_get_agent_card_authorized_succeeds_on_private_assistant(
    mock_authenticate, mock_find_by_id, private_assistant, mock_user
):
    """Authenticated request with READ access on a private assistant must succeed (200 OK)."""
    mock_find_by_id.return_value = private_assistant
    mock_authenticate.return_value = mock_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/v1/a2a/assistants/private-id-123/.well-known/agent-card.json")

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Private Assistant"
    assert data["description"] == "Private Description"
    mock_authenticate.assert_called_once()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
async def test_get_agent_card_unauthorized_raises_403_on_private_assistant(
    mock_authenticate, mock_find_by_id, private_assistant, mock_unauthorized_user
):
    """Authenticated request without READ access on a private assistant must return 403 Forbidden."""
    mock_find_by_id.return_value = private_assistant
    mock_authenticate.return_value = mock_unauthorized_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/v1/a2a/assistants/private-id-123/.well-known/agent-card.json")

    assert resp.status_code == 403
    data = resp.json()
    assert "Access Denied" in data["error"]["message"] or "Access Denied" in data["error"].get("details", "")
    mock_authenticate.assert_called_once()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
async def test_get_agent_card_anonymous_succeeds_on_global_assistant(
    mock_authenticate, mock_find_by_id, global_assistant
):
    """Anonymous request on intentionally public (global/marketplace) assistant must succeed (200 OK) without calling authenticate."""
    mock_find_by_id.return_value = global_assistant

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/v1/a2a/assistants/global-id-123/.well-known/agent-card.json")

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Global Assistant"
    assert data["description"] == "Global Description"
    mock_authenticate.assert_not_called()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
async def test_get_agent_card_missing_raises_404(mock_find_by_id):
    """Requesting non-existent assistant card must return 404 Not Found."""
    mock_find_by_id.return_value = None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/v1/a2a/assistants/missing-id-123/.well-known/agent-card.json")

    assert resp.status_code == 404
    data = resp.json()
    assert "wasn't found" in data["error"]["message"]


INTERNAL_CARD_KEYS = {"project_name", "integration_id", "user_id", "bedrock_agentcore"}
V1 = {"A2A-Version": "1.0"}
ENDPOINT = "/v1/a2a/assistants/private-id-123"


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
async def test_get_agent_card_shape_matches_v1(mock_find_by_id, global_assistant):
    mock_find_by_id.return_value = global_assistant
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/v1/a2a/assistants/global-id-123/.well-known/agent-card.json")
    data = resp.json()
    assert "url" not in data and "authentication" not in data
    interface = data["supportedInterfaces"][0]
    assert interface["protocolBinding"] == "JSONRPC" and interface["protocolVersion"] == "1.0"
    assert "transport" not in interface
    assert data["capabilities"]["extendedAgentCard"] is True
    assert data["defaultInputModes"] == ["text/plain"]
    assert not INTERNAL_CARD_KEYS & set(data)


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.A2ACardResolver.fetch_agent_card")
async def test_fetch_remote_assistant_still_returns_internal_fields(mock_fetch, mock_user):
    card = AgentCard.model_validate(
        {
            **load_fixture("agent_card"),
            "project_name": "project1",
            "integration_id": "integ-1",
            "user_id": "user-1",
        }
    )
    mock_fetch.return_value = (True, card, "")
    app.dependency_overrides[a2a.authenticate] = lambda: mock_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            resp = await ac.get(
                "/v1/a2a/assistants/fetch", params={"url": "https://remote.example.com", "project_name": "project1"}
            )
    finally:
        app.dependency_overrides = {}
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_name"] == "project1" and data["integration_id"] == "integ-1"
    assert data["user_id"] == "user-1" and data["bedrock_agentcore"] is False


@pytest.fixture
def a2a_env(private_assistant, mock_user):
    """Authenticated, authorized caller on the private assistant, with an in-memory a2a_tasks store."""
    tasks: dict[str, Task] = {}

    def find(task_id):
        return tasks.get(task_id)

    def save(self, refresh=False):
        tasks[self.id] = self
        return self

    app.dependency_overrides[a2a.authenticate] = lambda: mock_user
    try:
        with (
            patch("codemie.rest_api.routers.a2a.Assistant.find_by_id", return_value=private_assistant),
            patch("codemie.rest_api.routers.a2a.Ability") as ability,
            patch.object(Task, "find_by_id", side_effect=find),
            patch.object(Task, "save", save),
            patch.object(Task, "update", save),
        ):
            ability.return_value.can.return_value = True
            yield tasks
    finally:
        app.dependency_overrides = {}


async def _post(body=None, *, headers=V1, params=None, content=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        if content is not None:
            return await ac.post(ENDPOINT, content=content, headers=headers, params=params)
        return await ac.post(ENDPOINT, json=body, headers=headers, params=params)


def _rpc(method, params=None, id_="r1"):
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _send_params(text="hi", **message_extra):
    return {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": text}], **message_extra}}


def _seed_task(store, state=TaskState.WORKING, user_id="user-1", task_id="task-9"):
    store[task_id] = Task(
        id=task_id,
        contextId="ctx-1",
        status=TaskStatus(state=state),
        history=[],
        assistant_id="private-id-123",
        user_id=user_id,
    )


def _sse_frames(resp):
    return [json.loads(line[len("data: ") :]) for line in resp.text.splitlines() if line.startswith("data: ")]


# ---- TR-5: version gate ----


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers,params",
    [
        ({}, None),
        ({"A2A-Version": ""}, None),
        ({"A2A-Version": "0.3"}, None),
        ({"A2A-Version": "2.0"}, None),
        ({"A2A-Version": "1"}, None),
        ({}, {"A2A-Version": "0.3"}),
        ({}, {"A2A-Version": "2.0"}),
    ],
)
async def test_unsupported_or_missing_version_is_rejected_with_32009(a2a_env, headers, params):
    resp = await _post(_rpc("GetTask", {"id": "task-9"}), headers=headers, params=params)
    assert resp.status_code == 200
    error = resp.json()["error"]
    assert error["code"] == -32009
    assert error["data"][0]["@type"] == "type.googleapis.com/google.rpc.ErrorInfo"
    assert error["data"][0]["reason"] == "VERSION_NOT_SUPPORTED"
    assert error["data"][0]["domain"] == "a2a-protocol.org"
    assert error["data"][0]["metadata"]["supportedVersions"] == "1.0"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers,params",
    [
        ({"A2A-Version": "1.0"}, None),
        ({"A2A-Version": "1.0.1"}, None),
        ({"a2a-version": "1.0"}, None),
        ({}, {"A2A-Version": "1.0"}),
        ({}, {"A2A-Version": "1.0.1"}),
        ({"A2A-Version": "1.0"}, {"A2A-Version": "0.3"}),
    ],
)
async def test_supported_version_passes_the_gate(a2a_env, headers, params):
    resp = await _post(_rpc("GetTask", {"id": "missing"}), headers=headers, params=params)
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32001


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
async def test_version_gate_runs_after_authentication(mock_find_by_id, private_assistant):
    mock_find_by_id.return_value = private_assistant

    def deny():
        raise ExtendedHTTPException(code=status.HTTP_401_UNAUTHORIZED, message="Authentication failed")

    app.dependency_overrides[a2a.authenticate] = deny
    try:
        resp = await _post(_rpc("GetTask", {"id": "x"}), headers={})
    finally:
        app.dependency_overrides = {}
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_version_gate_runs_after_authorization(a2a_env):
    with patch("codemie.rest_api.routers.a2a.Ability") as ability:
        ability.return_value.can.return_value = False
        resp = await _post(_rpc("GetTask", {"id": "x"}), headers={})
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_version_gate_runs_before_body_parsing(a2a_env):
    resp = await _post(content=b"not json", headers={"A2A-Version": "0.3"})
    assert resp.json()["error"]["code"] == -32009


# ---- TR-6: envelope, method scope, params ----


@pytest.mark.anyio
@pytest.mark.parametrize(
    "method",
    [
        "ListTasks",
        "SubscribeToTask",
        "CreateTaskPushNotificationConfig",
        "GetTaskPushNotificationConfig",
        "ListTaskPushNotificationConfigs",
        "DeleteTaskPushNotificationConfig",
        "message/send",
        "tasks/get",
        "garbage",
    ],
)
async def test_unknown_and_out_of_scope_methods_return_method_not_found(a2a_env, method):
    resp = await _post(_rpc(method, {}))
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32601 and body["id"] == "r1"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "method,params",
    [
        ("SendMessage", {}),
        ("SendMessage", {"message": {"messageId": "m", "role": "ROLE_USER", "parts": []}}),
        ("SendStreamingMessage", {"message": {"role": "ROLE_USER"}}),
        ("GetTask", {}),
        ("CancelTask", {"id": 5}),
        ("GetTask", "not-an-object"),
    ],
)
async def test_invalid_params_return_32602_with_field_violations(a2a_env, method, params):
    resp = await _post(_rpc(method, params))
    assert resp.status_code == 200
    error = resp.json()["error"]
    assert error["code"] == -32602
    detail = error["data"][0]
    assert detail["@type"] == "type.googleapis.com/google.rpc.BadRequest"
    assert detail["fieldViolations"] and all({"field", "description"} <= set(v) for v in detail["fieldViolations"])


@pytest.mark.anyio
async def test_non_json_body_returns_parse_error(a2a_env):
    resp = await _post(content=b"{not json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32700 and body["id"] is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"jsonrpc": "2.0", "id": "r1", "params": {}},
        {"jsonrpc": "2.0", "id": "r1", "method": 5},
        {"jsonrpc": "1.0", "id": "r1", "method": "GetTask", "params": {"id": "x"}},
        {"jsonrpc": "2.0", "id": ["bad"], "method": "GetTask", "params": {"id": "x"}},
        ["not", "an", "object"],
        "just a string",
    ],
)
async def test_non_jsonrpc_envelope_returns_invalid_request_with_v1_hint(a2a_env, body):
    resp = await _post(body)
    assert resp.status_code == 200
    error = resp.json()["error"]
    assert error["code"] == -32600 and "1.0" in error["message"]
    assert error["data"][0]["@type"].startswith("type.googleapis.com/")


@pytest.mark.anyio
async def test_integer_request_id_is_valid_and_echoed(a2a_env):
    resp = await _post(_rpc("GetTask", {"id": "missing"}, id_=42))
    assert resp.status_code == 200
    assert resp.json()["id"] == 42 and resp.json()["error"]["code"] == -32001


# ---- flows through the router ----


@pytest.mark.anyio
async def test_send_message_returns_v1_task(a2a_env):
    with patch("codemie.rest_api.a2a.server.codemie_task_manager.AssistantService.build_agent") as mock_build:
        mock_build.return_value.invoke_with_a2a_output.return_value = {"content": "ok"}
        resp = await _post(_rpc("SendMessage", _send_params()))
    assert resp.status_code == 200
    body = resp.json()
    task = body["result"]["task"]
    assert body["jsonrpc"] == "2.0" and body["id"] == "r1"
    assert task["id"] and task["contextId"] and "taskId" not in task
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["artifactId"] and task["artifacts"][0]["parts"] == [{"text": "ok"}]
    assert not {"sessionId", "assistant_id", "user_id", "date", "update_date"} & set(task)


@pytest.mark.anyio
async def test_send_streaming_message_returns_sse_frames(a2a_env):
    with patch("codemie.rest_api.a2a.server.codemie_task_manager.AssistantService.build_agent") as mock_build:
        mock_build.return_value.invoke_with_a2a_output.return_value = {"content": "ok"}
        resp = await _post(_rpc("SendStreamingMessage", _send_params()))
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = _sse_frames(resp)
    assert "task" in frames[0]["result"]
    assert frames[1]["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
    assert frames[-1]["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "final" not in resp.text


@pytest.mark.anyio
async def test_streaming_error_before_stream_is_a_single_error_frame(a2a_env):
    resp = await _post(_rpc("SendStreamingMessage", _send_params(taskId="missing")))
    frames = _sse_frames(resp)
    assert len(frames) == 1 and frames[0]["error"]["code"] == -32001 and frames[0]["id"] == "r1"


@pytest.mark.anyio
async def test_get_task_unknown_id_returns_200_with_task_not_found(a2a_env):
    resp = await _post(_rpc("GetTask", {"id": "missing"}))
    assert resp.status_code == 200
    error = resp.json()["error"]
    assert error["code"] == -32001 and error["data"][0]["reason"] == "TASK_NOT_FOUND"


@pytest.mark.anyio
async def test_get_task_success_returns_task(a2a_env):
    _seed_task(a2a_env, TaskState.COMPLETED)
    resp = await _post(_rpc("GetTask", {"id": "task-9"}))
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["id"] == "task-9" and "taskId" not in result
    assert result["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "assistant_id" not in result and "user_id" not in result


@pytest.mark.anyio
async def test_get_task_owned_by_other_user_returns_not_found(a2a_env):
    _seed_task(a2a_env, TaskState.COMPLETED, user_id="user-2")
    resp = await _post(_rpc("GetTask", {"id": "task-9"}))
    assert resp.json()["error"]["code"] == -32001


@pytest.mark.anyio
async def test_cancel_task_success_returns_canceled_task(a2a_env):
    _seed_task(a2a_env, TaskState.WORKING)
    resp = await _post(_rpc("CancelTask", {"id": "task-9"}))
    assert resp.status_code == 200
    assert resp.json()["result"]["status"]["state"] == "TASK_STATE_CANCELED"


@pytest.mark.anyio
async def test_cancel_task_unknown_id_returns_not_found(a2a_env):
    resp = await _post(_rpc("CancelTask", {"id": "missing"}))
    assert resp.json()["error"]["code"] == -32001


@pytest.mark.anyio
async def test_get_extended_agent_card_returns_public_card(a2a_env):
    resp = await _post(_rpc("GetExtendedAgentCard", {}))
    result = resp.json()["result"]
    assert result["name"] == "Private Assistant"
    assert result["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert not INTERNAL_CARD_KEYS & set(result)
    assert "authentication" not in result


@pytest.mark.anyio
async def test_send_streaming_message_unhandled_error_yields_error_frame(a2a_env):
    async def _boom(self, request):
        raise RuntimeError("stream exploded")
        yield  # pragma: no cover

    with patch.object(a2a.CodemieTaskManager, "on_send_streaming_message", _boom):
        resp = await _post(_rpc("SendStreamingMessage", _send_params()))
    frames = _sse_frames(resp)
    assert len(frames) == 1
    assert frames[0]["error"]["code"] == -32603
    assert frames[0]["id"] == "r1"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "method,params,attr",
    [
        ("GetTask", {"id": "t"}, "on_get_task"),
        ("SendMessage", _send_params(), "on_send_task"),
        ("CancelTask", {"id": "t"}, "on_cancel_task"),
    ],
)
async def test_non_streaming_dispatch_failure_returns_internal_error_without_leaking(a2a_env, method, params, attr):
    async def boom(self, request):
        raise RuntimeError("secret internals")

    with patch.object(a2a.CodemieTaskManager, attr, boom):
        resp = await _post(_rpc(method, params, id_="req-77"))
    body = resp.json()
    assert resp.status_code == 200
    assert body["error"]["code"] == -32603 and body["id"] == "req-77"
    assert "secret internals" not in resp.text


def _db_loaded_task(state):
    """Instance created by the ORM loader path: SQLAlchemy state exists, pydantic __init__ never ran."""
    task = attributes.manager_of_class(Task).new_instance()
    for key, value in {
        "id": "task-db",
        "contextId": None,
        "sessionId": "legacy-ctx",
        "assistant_id": "private-id-123",
        "user_id": "user-1",
        "status": TaskStatus(state=state),
        "artifacts": None,
        "history": [],
    }.items():
        setattr(task, key, value)
    return task


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["GetTask", "CancelTask"])
async def test_db_loaded_task_serializes_through_router(a2a_env, method):
    a2a_env["task-db"] = _db_loaded_task(TaskState.WORKING)
    resp = await _post(_rpc(method, {"id": "task-db"}))
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["id"] == "task-db" and result["contextId"] == "legacy-ctx"


@pytest.mark.anyio
async def test_send_message_continuing_db_loaded_task_serializes(a2a_env):
    a2a_env["task-db"] = _db_loaded_task(TaskState.INPUT_REQUIRED)
    with patch("codemie.rest_api.a2a.server.codemie_task_manager.AssistantService.build_agent") as mock_build:
        mock_build.return_value.invoke_with_a2a_output.return_value = {"content": "ok"}
        resp = await _post(_rpc("SendMessage", _send_params(taskId="task-db")))
    assert resp.status_code == 200
    assert resp.json()["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
