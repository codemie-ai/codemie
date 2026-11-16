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

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from codemie.configs.llm_config import LLMModel, ModelStatus
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.routers.assistant import execution
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService


@pytest.fixture
def selection_api():
    assistant = Assistant(
        id="assistant-123",
        name="Existing assistant",
        description="Model selection regression",
        system_prompt="Answer the user",
        project="demo",
        is_global=True,
        llm_model_type="active-model",
        version=1,
    )
    models = [
        LLMModel(base_name="active-model", deployment_name="dep-active", enabled=True),
        LLMModel(base_name="old-model", deployment_name="dep-old", enabled=True, status=ModelStatus.DEPRECATED),
        LLMModel(base_name="disabled-model", deployment_name="dep-disabled", enabled=True, status=ModelStatus.DISABLED),
    ]
    app = FastAPI()
    app.include_router(execution.router, prefix="/v1")
    app.dependency_overrides[authenticate] = lambda: User(id="user-123", username="tester")

    @app.middleware("http")
    async def request_state(request: Request, call_next):
        request.state.wait_for_disconnect = AsyncMock()
        return await call_next(request)

    @app.exception_handler(ExtendedHTTPException)
    async def model_error(request: Request, exc: ExtendedHTTPException):
        return JSONResponse(status_code=exc.code, content={"error": {"message": exc.message, "details": exc.details}})

    with (
        patch.object(execution, "get_assistant_by_id_or_raise", return_value=assistant),
        patch.object(execution, "_get_assistant_by_slug_or_raise", return_value=assistant),
        patch.object(execution, "_ask_assistant", return_value=JSONResponse({"response": "ok"})) as ask,
        patch.object(execution, "_ask_virtual_assistant", return_value=JSONResponse({"response": "ok"})) as virtual,
        patch.object(execution.llm_service, "get_all_llm_model_info", return_value=models) as catalog,
        patch.object(Conversation, "find_by_id", return_value=None) as load_conversation,
        patch("codemie.core.ability.Ability.can", return_value=True) as can_access,
        TestClient(app) as client,
    ):
        yield SimpleNamespace(
            client=client,
            assistant=assistant,
            ask=ask,
            virtual=virtual,
            catalog=catalog,
            conversation=load_conversation,
            can_access=can_access,
        )


ASK_PATHS = ["/v1/assistants/assistant-123/model", "/v1/assistants/slug/existing/model"]
VIRTUAL_PATH = "/v1/assistants/virtual/model"


@pytest.mark.parametrize("path", ASK_PATHS + [VIRTUAL_PATH])
@pytest.mark.parametrize("model_name", ["old-model", "dep-old", " dep-old ", "disabled-model", "dep-disabled"])
@pytest.mark.parametrize("stream", [False, True])
def test_new_unselectable_model_returns_400_before_execution(selection_api, path, model_name, stream):
    field = "llm_model_type" if path == VIRTUAL_PATH else "llm_model"
    response = selection_api.client.post(path, json={"text": "Hello", field: model_name, "stream": stream})

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Model not selectable"
    assert "not selectable" in response.json()["error"]["details"]
    selection_api.ask.assert_not_called()
    selection_api.virtual.assert_not_called()
    assert selection_api.assistant.llm_model_type == "active-model"


@pytest.mark.parametrize("path", ASK_PATHS)
@pytest.mark.parametrize("model_name", [None, "old-model", "dep-old", " dep-old "])
def test_existing_assistant_may_keep_its_deprecated_model(selection_api, path, model_name):
    selection_api.assistant.llm_model_type = "old-model"
    payload = {"text": "Continue"}
    if model_name is not None:
        payload["llm_model"] = model_name

    response = selection_api.client.post(path, json=payload)

    assert response.status_code == 200
    execution_assistant, _, request = selection_api.ask.call_args.args[:3]
    # The router no longer pre-resolves the model; build_agent resolves it without mutation.
    assert execution_assistant.llm_model_type == "old-model"
    assert request.llm_model == model_name
    if model_name is None:
        selection_api.conversation.assert_not_called()


@pytest.mark.parametrize("path", ASK_PATHS)
@pytest.mark.parametrize("model_name", [None, "old-model", "dep-old"])
def test_existing_chat_may_keep_a_deprecated_override(selection_api, path, model_name):
    selection_api.conversation.return_value = Conversation(
        conversation_id="existing-chat",
        llm_model="dep-old",
        user_id="user-123",
    )
    payload = {"text": "Continue", "conversation_id": "existing-chat"}
    if model_name is not None:
        payload["llm_model"] = model_name

    response = selection_api.client.post(path, json=payload)

    assert response.status_code == 200
    selection_api.ask.assert_called_once()
    assistant, _, request = selection_api.ask.call_args.args[:3]
    # The pick is resolved, not written onto the request (resolve_execution_model mirrors build_agent).
    assert AssistantService.resolve_execution_model(assistant, request) == (model_name or "dep-old")


@pytest.mark.parametrize("path", ASK_PATHS)
def test_chat_selection_takes_precedence_over_assistant_for_retention(selection_api, path):
    selection_api.assistant.llm_model_type = "old-model"
    selection_api.conversation.return_value = Conversation(
        conversation_id="existing-chat",
        llm_model="active-model",
        user_id="user-123",
    )

    response = selection_api.client.post(
        path,
        json={
            "text": "Switch",
            "conversation_id": "existing-chat",
            "llm_model": "old-model",
        },
    )

    assert response.status_code == 400
    selection_api.ask.assert_not_called()


@pytest.mark.parametrize("path", ASK_PATHS)
def test_unauthorized_conversation_cannot_supply_previous_selection(selection_api, path):
    selection_api.conversation.return_value = Conversation(
        conversation_id="other-chat",
        llm_model="old-model",
        user_id="other-user",
    )
    selection_api.can_access.side_effect = lambda action, entity: not isinstance(entity, Conversation)

    response = selection_api.client.post(
        path,
        json={
            "text": "Continue",
            "conversation_id": "other-chat",
            "llm_model": "old-model",
        },
    )

    assert response.status_code == 403
    selection_api.catalog.assert_not_called()
    selection_api.ask.assert_not_called()


@pytest.mark.parametrize("path", ASK_PATHS)
def test_assistant_access_checked_before_selection_context(selection_api, path):
    selection_api.can_access.return_value = False

    response = selection_api.client.post(path, json={"text": "Hello", "llm_model": "old-model"})

    assert response.status_code == 401
    selection_api.conversation.assert_not_called()
    selection_api.catalog.assert_not_called()
    selection_api.ask.assert_not_called()


@pytest.mark.parametrize("path", ASK_PATHS + [VIRTUAL_PATH])
def test_enabled_model_still_reaches_execution(selection_api, path):
    field = "llm_model_type" if path == VIRTUAL_PATH else "llm_model"
    response = selection_api.client.post(path, json={"text": "Hello", field: "dep-active"})

    assert response.status_code == 200
    if path == VIRTUAL_PATH:
        assert selection_api.virtual.call_args.args[0].llm_model_type == "dep-active"
    else:
        # The router no longer pre-resolves the model; build_agent resolves it without mutation.
        request = selection_api.ask.call_args.args[2]
        assert request.llm_model == "dep-active"


def test_virtual_request_cannot_claim_an_existing_chat_selection(selection_api):
    response = selection_api.client.post(
        VIRTUAL_PATH,
        json={
            "text": "Hello",
            "llm_model_type": "old-model",
            "conversation_id": "existing-chat",
            "history": "Previously used this model",
        },
    )

    assert response.status_code == 400
    selection_api.conversation.assert_not_called()
    selection_api.virtual.assert_not_called()


@pytest.mark.parametrize("model_name", [None, "dep-active"])
def test_virtual_inline_selection_cannot_be_replaced_by_a_saved_chat(selection_api, model_name):
    selection_api.conversation.return_value = Conversation(
        conversation_id="existing-chat",
        llm_model="old-model",
        user_id="user-123",
    )
    payload = {"text": "Hello", "conversation_id": "existing-chat"}
    if model_name is not None:
        payload["llm_model_type"] = model_name
    with patch.object(type(execution.llm_service), "default_llm_model", property(lambda self: "active-model")):
        response = selection_api.client.post(VIRTUAL_PATH, json=payload)

    assert response.status_code == 200
    assistant, _, request = selection_api.virtual.call_args.args[:3]
    AssistantService._apply_conversation_runtime_overrides(assistant, request)
    assert request.llm_model == (model_name or "active-model")


@pytest.mark.parametrize("path", ASK_PATHS)
def test_historical_assistant_version_may_keep_its_existing_model(selection_api, path):
    versioned = selection_api.assistant.model_copy(update={"llm_model_type": "old-model", "version": 1})
    with patch(
        "codemie.rest_api.routers.assistant._ask.AssistantVersionService.apply_version_to_assistant",
        return_value=versioned,
    ):
        response = selection_api.client.post(path, json={"text": "Continue", "version": 1, "llm_model": "dep-old"})

    assert response.status_code == 200
    # The router no longer pre-resolves the model; build_agent resolves it without mutation.
    assert selection_api.ask.call_args.args[2].llm_model == "dep-old"


@pytest.mark.parametrize("path", ASK_PATHS)
def test_project_whitelist_does_not_hide_a_new_deprecated_selection(selection_api, path):
    with patch(
        "codemie.rest_api.routers.assistant._ask.resolve_assistant_model",
        return_value="active-model",
    ) as resolve_project_model:
        response = selection_api.client.post(path, json={"text": "Switch", "llm_model": "old-model"})

    assert response.status_code == 400
    resolve_project_model.assert_not_called()
    selection_api.ask.assert_not_called()


@pytest.mark.parametrize("path", ASK_PATHS)
def test_disabled_existing_selection_still_reaches_execution_fallback(selection_api, path):
    selection_api.conversation.return_value = Conversation(
        conversation_id="existing-chat",
        llm_model="disabled-model",
        user_id="user-123",
    )
    response = selection_api.client.post(
        path,
        json={
            "text": "Continue",
            "conversation_id": "existing-chat",
            "llm_model": "dep-disabled",
        },
    )

    assert response.status_code == 200
    selection_api.ask.assert_called_once()
