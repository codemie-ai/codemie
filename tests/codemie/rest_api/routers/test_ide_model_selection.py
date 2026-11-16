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
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from codemie.configs.llm_config import LLMModel, ModelStatus
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage, UpsertHistoryRequest
from codemie.rest_api.routers import ide
from codemie.rest_api.routers.conversation import crud, history
from codemie.rest_api.routers.assistant._model_selection import llm_service
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.conversation_service import ConversationService, UpsertChatHistoryParams


@pytest.fixture
def ide_api():
    user = User(id="user-123", username="tester", name="IDE User")
    stored = {}
    writes = []
    models = [
        LLMModel(base_name=name, deployment_name=f"dep-{name}", enabled=True, status=status)
        for name, status in (
            ("active", ModelStatus.ENABLED),
            ("other-active", ModelStatus.ENABLED),
            ("old", ModelStatus.DEPRECATED),
            ("other-old", ModelStatus.DEPRECATED),
            ("disabled", ModelStatus.DISABLED),
            ("image-active", ModelStatus.ENABLED),
            ("image-old", ModelStatus.DEPRECATED),
        )
    ]
    app = FastAPI()
    app.include_router(ide.router)
    app.include_router(crud.router, prefix="/v1")
    app.include_router(history.router, prefix="/v1")
    app.dependency_overrides[authenticate] = lambda: user

    @app.middleware("http")
    async def request_state(request: Request, call_next):
        request.state.wait_for_disconnect = AsyncMock()
        request.state.uuid = "request-123"
        return await call_next(request)

    @app.exception_handler(ExtendedHTTPException)
    async def selection_error(request: Request, exc: ExtendedHTTPException):
        return JSONResponse(status_code=exc.code, content={"message": exc.message})

    def persist(conversation, *args, **kwargs):
        stored[conversation.conversation_id] = conversation
        writes.append((conversation.llm_model, conversation.image_generation_model))

    def handler(assistant, user, request_uuid):
        def process(request, background_tasks, raw_request):
            # Exercise the actual history-persistence path, with only DB/external effects mocked.
            ConversationService.upsert_chat_history(
                UpsertChatHistoryParams(
                    request=request,
                    assistant=assistant,
                    user=user,
                    assistant_response="ok",
                    in_progress=bool(request.stream),
                )
            )
            return JSONResponse({"response": "ok"})

        return SimpleNamespace(process_request=process)

    with (
        patch.object(Conversation, "find_by_id", side_effect=lambda key: stored.get(key)),
        patch.object(Conversation, "get_by_id", side_effect=lambda key: stored[key]),
        patch.object(Conversation, "save", autospec=True, side_effect=persist),
        patch.object(Conversation, "update", autospec=True, side_effect=persist),
        patch.object(Conversation, "update_conversation_assistants"),
        patch.object(ConversationService, "_handle_turn_completion"),
        patch.object(ConversationService, "_upsert_conversation_metrics"),
        patch.object(ide.config, "CONVERSATION_HISTORY_UPSERT_NOOP_ENABLED", False),
        patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files"),
        patch.object(llm_service, "get_all_llm_model_info", return_value=models) as catalog,
        patch.object(llm_service, "get_allowed_router_options", return_value=[]),
        patch.object(
            type(llm_service), "default_llm_model", new_callable=PropertyMock, return_value="active"
        ) as default,
        patch("codemie.core.ability.Ability.can", return_value=True) as can_access,
        patch.object(ide.assistant_user_interaction_service, "record_usage"),
        patch.object(ide.request_summary_manager, "create_request_summary"),
        patch.object(ide, "get_request_handler", side_effect=handler) as runtime,
        TestClient(app) as client,
    ):
        yield SimpleNamespace(
            client=client,
            stored=stored,
            writes=writes,
            models=models,
            catalog=catalog,
            can_access=can_access,
            runtime=runtime,
            default=default,
        )


def send(api, *, omit_model=False, **overrides):
    payload = {
        "conversation_id": "chat-123",
        "virtual_assistant_id": "plugin-123",
        "llm_model": "active",
        "text": "Continue",
        "stream": False,
        **overrides,
    }
    if omit_model:
        payload.pop("llm_model")
    return api.client.post("/v1/ide/inference/virtual", json=payload)


def existing_chat(**overrides):
    return Conversation(
        **{
            "id": "chat-123",
            "conversation_id": "chat-123",
            "user_id": "user-123",
            "initial_assistant_id": ide._get_formatted_virtual_assistant_id("plugin-123"),
            "history": [GeneratedMessage(role="Assistant", message="Previous answer")],
            **overrides,
        }
    )


@pytest.mark.parametrize("model", ["old", "dep-old", "other-old", "disabled", "dep-disabled"])
@pytest.mark.parametrize("stream", [False, True])
def test_new_ide_conversation_rejects_unselectable_models(ide_api, model, stream):
    response = send(ide_api, llm_model=model, stream=stream)
    assert response.status_code == 400
    ide_api.runtime.assert_not_called()
    assert not ide_api.writes


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("continuation_model", ["old", "dep-old", " dep-old "])
def test_ide_turn_persists_model_then_can_continue_after_deprecation(ide_api, stream, continuation_model):
    old = next(model for model in ide_api.models if model.base_name == "old")
    old.status = ModelStatus.ENABLED
    assert send(ide_api, llm_model="old", stream=stream).status_code == 200
    assert ide_api.writes == [("old", None)]
    old.status = ModelStatus.DEPRECATED

    response = send(ide_api, llm_model=continuation_model, stream=stream)

    assert response.status_code == 200
    assert ide_api.writes[-1] == (continuation_model.strip(), None)
    assert ide_api.runtime.call_count == 2


def test_known_ide_selection_cannot_switch_to_another_deprecated_model(ide_api):
    ide_api.stored["chat-123"] = existing_chat(llm_model="old")
    assert send(ide_api, llm_model="other-old").status_code == 400
    assert not ide_api.writes
    ide_api.runtime.assert_not_called()


def test_switch_to_enabled_model_replaces_the_selection_retained_by_future_turns(ide_api):
    ide_api.stored["chat-123"] = existing_chat(llm_model="old")
    assert send(ide_api).status_code == 200
    assert ide_api.writes == [("active", None)]
    assert send(ide_api, llm_model="old").status_code == 400
    assert len(ide_api.writes) == 1


def test_unmarked_ide_conversation_must_select_an_enabled_model_once(ide_api):
    ide_api.stored["chat-123"] = existing_chat()
    assert send(ide_api, llm_model="old").status_code == 400
    assert not ide_api.writes
    ide_api.runtime.assert_not_called()
    assert send(ide_api).status_code == 200
    assert ide_api.writes == [("active", None)]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model", ["old", "dep-old", " dep-old "])
def test_marked_legacy_ide_chat_adopts_one_deprecated_model_and_persists_it(ide_api, stream, model):
    conversation = existing_chat(legacy_ide_model_selection_pending=True)
    ide_api.stored["chat-123"] = conversation

    assert send(ide_api, llm_model=model, stream=stream).status_code == 200
    assert ide_api.runtime.call_args.args[0].llm_model_type == model.strip()
    assert ide_api.writes == [(model.strip(), None)]
    assert conversation.legacy_ide_model_selection_pending is False

    assert send(ide_api, llm_model="other-old", stream=stream).status_code == 400
    assert len(ide_api.writes) == 1
    assert send(ide_api, omit_model=True, stream=stream).status_code == 200
    assert ide_api.writes[-1] == (model.strip(), None)


@pytest.mark.parametrize("model", ["disabled", "dep-disabled", "old"])
def test_legacy_allowance_does_not_authorize_disabled_models(ide_api, model):
    conversation = existing_chat(legacy_ide_model_selection_pending=True)
    ide_api.stored["chat-123"] = conversation
    if model == "old":
        next(item for item in ide_api.models if item.base_name == "old").enabled = False

    assert send(ide_api, llm_model=model).status_code == 400
    assert conversation.legacy_ide_model_selection_pending is True
    assert not ide_api.writes
    ide_api.runtime.assert_not_called()


@pytest.mark.parametrize(
    "overrides",
    [
        {"history": []},
        {"initial_assistant_id": "web-assistant"},
        {"initial_assistant_id": ide._get_formatted_virtual_assistant_id("another-plugin")},
        {"import_source": "codex"},
        {"llm_model": "active"},
    ],
)
def test_legacy_marker_still_requires_matching_existing_ide_work(ide_api, overrides):
    ide_api.stored["chat-123"] = existing_chat(legacy_ide_model_selection_pending=True, **overrides)

    assert send(ide_api, llm_model="old").status_code == 400
    assert not ide_api.writes
    ide_api.runtime.assert_not_called()


def test_client_cannot_supply_legacy_eligibility(ide_api):
    ide_api.stored["chat-123"] = existing_chat()

    assert send(ide_api, llm_model="old", legacy_ide_model_selection_pending=True).status_code == 400
    assert ide_api.stored["chat-123"].legacy_ide_model_selection_pending is False
    assert not ide_api.writes


@pytest.mark.parametrize("model", [None, "", "active"])
def test_explicit_model_update_consumes_legacy_allowance(ide_api, model):
    conversation = existing_chat(legacy_ide_model_selection_pending=True)
    ide_api.stored["chat-123"] = conversation

    response = ide_api.client.put("/v1/conversations/chat-123", json={"llm_model": model})

    assert response.status_code == 200
    assert "legacy_ide_model_selection_pending" not in response.json()
    assert conversation.legacy_ide_model_selection_pending is False
    assert send(ide_api, llm_model="old").status_code == 400


def test_enabled_ide_turn_then_clearing_model_does_not_restore_legacy_allowance(ide_api):
    conversation = existing_chat(legacy_ide_model_selection_pending=True)
    ide_api.stored["chat-123"] = conversation

    assert send(ide_api).status_code == 200
    assert conversation.legacy_ide_model_selection_pending is False
    assert ide_api.client.put("/v1/conversations/chat-123", json={"llm_model": None}).status_code == 200
    assert send(ide_api, llm_model="old").status_code == 400


@pytest.mark.parametrize("delete_path", ["history", "history/0"])
def test_clearing_history_then_importing_it_cannot_restore_legacy_allowance(ide_api, delete_path):
    conversation = existing_chat(
        legacy_ide_model_selection_pending=True,
        history=[GeneratedMessage(role="Assistant", message="Previous answer", history_index=0)],
    )
    ide_api.stored["chat-123"] = conversation
    with (
        patch("codemie.service.conversation_service.Session"),
        patch("codemie.core.workflow_models.workflow_execution.WorkflowExecution.delete_by_conversation_ids"),
    ):
        response = ide_api.client.delete(f"/v1/conversations/chat-123/{delete_path}")

    assert response.status_code == 200
    assert conversation.legacy_ide_model_selection_pending is False
    assert not conversation.history
    response = ide_api.client.put(
        "/v1/conversations/chat-123/history",
        json={
            "assistant_id": ide._get_formatted_virtual_assistant_id("plugin-123"),
            "history": [{"role": "Assistant", "message": "Imported answer"}],
        },
    )
    assert response.status_code == 200
    assert len(conversation.history) == 1
    assert conversation.history[0].message == "Imported answer"
    assert send(ide_api, llm_model="old").status_code == 400


def test_legacy_allowance_does_not_authorize_a_new_deprecated_image_model(ide_api):
    conversation = existing_chat(legacy_ide_model_selection_pending=True)
    ide_api.stored["chat-123"] = conversation

    assert send(ide_api, llm_model="old", image_generation_model="image-old").status_code == 400
    assert conversation.legacy_ide_model_selection_pending is True
    assert not ide_api.writes
    ide_api.runtime.assert_not_called()


def test_legacy_allowance_does_not_authorize_an_inaccessible_conversation(ide_api):
    ide_api.stored["chat-123"] = existing_chat(legacy_ide_model_selection_pending=True)
    ide_api.can_access.return_value = False

    assert send(ide_api, llm_model="old").status_code == 403
    ide_api.catalog.assert_not_called()
    ide_api.runtime.assert_not_called()
    assert not ide_api.writes


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("selection", [None, "", "   "])
def test_default_first_turn_records_selection_and_cannot_unlock_a_deprecated_model(ide_api, stream, selection):
    response = send(ide_api, omit_model=selection is None, llm_model=selection or "", stream=stream)

    assert response.status_code == 200
    assert ide_api.runtime.call_args.args[0].llm_model_type == "active"
    assert ide_api.writes == [("active", None)]
    assert ide_api.default.call_count == 1
    assert send(ide_api, llm_model="old", stream=stream).status_code == 400
    assert ide_api.writes == [("active", None)]


def test_omitted_model_keeps_recorded_default_after_deprecation_and_default_change(ide_api):
    assert send(ide_api, omit_model=True).status_code == 200
    next(model for model in ide_api.models if model.base_name == "active").status = ModelStatus.DEPRECATED
    ide_api.default.return_value = "other-active"

    assert send(ide_api, omit_model=True).status_code == 200
    assert ide_api.runtime.call_args.args[0].llm_model_type == "active"
    assert ide_api.writes == [("active", None), ("active", None)]
    assert ide_api.default.call_count == 1


def test_explicit_empty_model_selects_current_default_instead_of_retaining_previous_model(ide_api):
    ide_api.stored["chat-123"] = existing_chat(llm_model="old")
    assert send(ide_api, llm_model="").status_code == 200
    assert ide_api.runtime.call_args.args[0].llm_model_type == "active"
    assert ide_api.writes == [("active", None)]


@pytest.mark.parametrize("import_source", [None, "codex"])
def test_imported_history_does_not_authorize_a_deprecated_ide_selection(ide_api, import_source):
    conversation = ConversationService._create_conversation_with_history(
        "chat-123",
        User(id="user-123", username="tester"),
        UpsertHistoryRequest(
            assistant_id=ide._get_formatted_virtual_assistant_id("plugin-123"),
            history=[GeneratedMessage(role="Assistant", message="Imported answer")],
        ),
        import_source=import_source,
    )
    ide_api.stored["chat-123"] = conversation

    assert send(ide_api, llm_model="old").status_code == 400
    assert conversation.llm_model is None
    assert not ide_api.writes
    ide_api.runtime.assert_not_called()


def test_unselectable_default_is_checked_before_starting_new_ide_work(ide_api):
    ide_api.default.return_value = "old"
    assert send(ide_api, omit_model=True).status_code == 400
    ide_api.runtime.assert_not_called()
    assert not ide_api.writes


@pytest.mark.parametrize(
    "conversation",
    [
        existing_chat(history=[]),
        existing_chat(initial_assistant_id="web-assistant"),
    ],
)
def test_empty_or_non_ide_conversation_does_not_grandfather_a_new_model(ide_api, conversation):
    ide_api.stored["chat-123"] = conversation
    assert send(ide_api, llm_model="old").status_code == 400
    assert not ide_api.writes
    ide_api.runtime.assert_not_called()


def test_client_history_does_not_prove_an_existing_ide_conversation(ide_api):
    response = send(ide_api, llm_model="old", history="I previously used this model")
    assert response.status_code == 400
    ide_api.runtime.assert_not_called()
    assert not ide_api.writes


def test_inaccessible_conversation_cannot_supply_selection_or_legacy_history(ide_api):
    ide_api.stored["chat-123"] = existing_chat(user_id="another-user", llm_model="old")
    ide_api.can_access.return_value = False
    response = send(ide_api, llm_model="old")
    assert response.status_code == 403
    ide_api.catalog.assert_not_called()
    ide_api.runtime.assert_not_called()
    assert not ide_api.writes


@pytest.mark.parametrize("stream", [False, True])
def test_ide_turn_persists_image_selection_and_retains_it_after_deprecation(ide_api, stream):
    image = next(model for model in ide_api.models if model.base_name == "image-old")
    image.status = ModelStatus.ENABLED
    assert send(ide_api, image_generation_model="image-old", stream=stream).status_code == 200
    assert ide_api.writes == [("active", "image-old")]
    image.status = ModelStatus.DEPRECATED
    assert send(ide_api, image_generation_model="dep-image-old", stream=stream).status_code == 200
    assert ide_api.writes[-1] == ("active", "dep-image-old")


def test_existing_ide_chat_cannot_newly_select_a_deprecated_image_model(ide_api):
    ide_api.stored["chat-123"] = existing_chat(llm_model="active")
    assert send(ide_api, image_generation_model="image-old").status_code == 400
    assert not ide_api.writes
    ide_api.runtime.assert_not_called()


def test_initial_ide_persistence_does_not_erase_a_model_when_request_omits_it(ide_api):
    ide_api.stored["chat-123"] = existing_chat(llm_model="old")
    assert send(ide_api, omit_model=True).status_code == 200
    assert ide_api.writes == [("old", None)]


def test_web_per_request_overrides_do_not_become_persisted_ide_selections(ide_api):
    ide_api.stored["chat-123"] = existing_chat(llm_model="old", image_generation_model="image-old")
    ConversationService.upsert_chat_history(
        UpsertChatHistoryParams(
            request=AssistantChatRequest(
                conversation_id="chat-123", text="Hello", llm_model="active", image_generation_model="image-active"
            ),
            assistant=Assistant(
                id="web-assistant", name="Web", description="Web chat", project="demo", system_prompt="Answer"
            ),
            user=User(id="user-123", username="tester"),
            in_progress=True,
        )
    )
    assert ide_api.writes == [("old", "image-old")]
