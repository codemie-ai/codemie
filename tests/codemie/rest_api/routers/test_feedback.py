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

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage, UserMark
from codemie.rest_api.models.feedback import FeedbackEntry, MarkEnum
from codemie.rest_api.models.standard import PostResponse
from codemie.rest_api.routers import feedback as router_mod
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

ALICE = "alice-owner"
MALLORY = "mallory-other-user"
ADMIN = "root-admin"
AUDITOR = "audit-user"


@pytest.fixture(autouse=True)
def _pin_non_local_mode(monkeypatch):
    monkeypatch.setattr(config, "ENV", "development")
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", True)


def _new_conversation(user_id, marked=False):
    conversation_id = str(uuid.uuid4())
    message = GeneratedMessage(role="Assistant", message="answer")
    if marked:
        message.user_mark = UserMark(mark=MarkEnum.CORRECT)
    return Conversation(id=conversation_id, conversation_id=conversation_id, user_id=user_id, history=[message])


@pytest.fixture
def world(monkeypatch):
    alice_conversation = _new_conversation(ALICE)
    mallory_conversation = _new_conversation(MALLORY)
    conversations = {c.id: c for c in (alice_conversation, mallory_conversation)}
    feedback: dict[str, FeedbackEntry] = {}
    saved_ids: list[str] = []
    service_calls: list[str] = []

    def _entry(conversation_id, text):
        entry = FeedbackEntry(
            request=text,
            response="r",
            mark=MarkEnum.CORRECT,
            conversationId=uuid.UUID(conversation_id),
            history=[],
        )
        entry.id = str(uuid.uuid4())
        feedback[entry.id] = entry
        return entry

    alice_entry = _entry(alice_conversation.id, "alice private request")
    orphan_entry = _entry(str(uuid.uuid4()), "orphan request")

    def _owned_by(entry, user_id):
        conversation = conversations.get(str(entry.conversationId))
        return conversation is not None and conversation.user_id == user_id

    class FakeService:
        @staticmethod
        def add_feedback(request, user):
            service_calls.append("add")
            conversations[request.conversation_id].history[request.message_index].user_mark = UserMark(
                mark=request.mark, feedback_id=request.feedback_id
            )

        @staticmethod
        def remove_feedback(request, user):
            service_calls.append("remove")
            conversations[request.conversation_id].history[request.message_index].user_mark = None

    def fake_save(self):
        self.id = self.id or str(uuid.uuid4())
        feedback[self.id] = self
        saved_ids.append(self.id)
        return PostResponse(id=self.id)

    monkeypatch.setattr(Conversation, "find_by_id", classmethod(lambda cls, i: conversations.get(str(i))))
    monkeypatch.setattr(router_mod, "ConversationService", FakeService)
    monkeypatch.setattr(FeedbackEntry, "get_all", classmethod(lambda cls, *a, **k: list(feedback.values())))
    monkeypatch.setattr(FeedbackEntry, "find_by_id", classmethod(lambda cls, i: feedback.get(i)))
    monkeypatch.setattr(
        FeedbackEntry,
        "get_all_for_user",
        classmethod(lambda cls, user_id: [e for e in feedback.values() if _owned_by(e, user_id)]),
    )
    monkeypatch.setattr(
        FeedbackEntry,
        "find_by_id_for_user",
        classmethod(
            lambda cls, i, user_id: next((e for e in feedback.values() if e.id == i and _owned_by(e, user_id)), None)
        ),
    )
    monkeypatch.setattr(FeedbackEntry, "save", fake_save)
    monkeypatch.setattr(FeedbackEntry, "delete", lambda self: feedback.pop(self.id, None))

    app = FastAPI()
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    app.include_router(router_mod.router)

    def client_as(user_id, is_admin=False, is_auditor=False):
        app.dependency_overrides[authenticate] = lambda: User(id=user_id, is_admin=is_admin, is_auditor=is_auditor)
        return TestClient(app)

    return SimpleNamespace(
        client_as=client_as,
        alice_conversation=alice_conversation,
        mallory_conversation=mallory_conversation,
        conversations=conversations,
        alice_entry=alice_entry,
        orphan_entry=orphan_entry,
        feedback=feedback,
        saved_ids=saved_ids,
        service_calls=service_calls,
        add_entry=_entry,
    )


def test_user_list_returns_only_own_entries(world):
    mallory_response = world.client_as(MALLORY).get("/v1/feedback")
    alice_response = world.client_as(ALICE).get("/v1/feedback")

    assert mallory_response.status_code == 200
    assert mallory_response.json() == []
    assert alice_response.status_code == 200
    assert [e["id"] for e in alice_response.json()] == [world.alice_entry.id]


def test_admin_list_returns_every_entry(world):
    response = world.client_as(ADMIN, is_admin=True).get("/v1/feedback")

    assert response.status_code == 200
    assert {e["id"] for e in response.json()} == {world.alice_entry.id, world.orphan_entry.id}


def test_auditor_list_is_scoped_like_a_user(world):
    response = world.client_as(AUDITOR, is_auditor=True).get("/v1/feedback")

    assert response.status_code == 200
    assert response.json() == []


def test_show_foreign_entry_returns_404(world):
    response = world.client_as(MALLORY).get(f"/v1/feedback/{world.alice_entry.id}")

    assert response.status_code == 404


def test_show_missing_entry_returns_404(world):
    response = world.client_as(ALICE).get(f"/v1/feedback/{uuid.uuid4()}")

    assert response.status_code == 404


def test_show_own_entry_returns_200(world):
    response = world.client_as(ALICE).get(f"/v1/feedback/{world.alice_entry.id}")

    assert response.status_code == 200
    assert response.json()["id"] == world.alice_entry.id


def test_show_orphan_entry_admin_200_user_404(world):
    admin_response = world.client_as(ADMIN, is_admin=True).get(f"/v1/feedback/{world.orphan_entry.id}")
    user_response = world.client_as(ALICE).get(f"/v1/feedback/{world.orphan_entry.id}")

    assert admin_response.status_code == 200
    assert user_response.status_code == 404


def _post_body(conversation_id):
    return {
        "conversationId": conversation_id,
        "assistant_id": "assistant",
        "request": "question",
        "response": "answer",
        "messageIndex": 0,
        "author": "user",
        "mark": "wrong",
    }


def _delete_body(conversation_id, feedback_id):
    return {
        "conversationId": conversation_id,
        "feedbackId": feedback_id,
        "messageIndex": 0,
        "assistant_id": "assistant",
        "author": "user",
    }


def test_post_on_foreign_conversation_is_403_and_unchanged(world):
    entries_before = len(world.feedback)

    response = world.client_as(MALLORY).post("/v1/feedback", json=_post_body(world.alice_conversation.id))

    assert response.status_code == 403
    assert world.alice_conversation.history[0].user_mark is None
    assert len(world.feedback) == entries_before
    assert world.saved_ids == []


def test_delete_on_foreign_conversation_is_403_and_unchanged(world):
    world.alice_conversation.history[0].user_mark = UserMark(mark=MarkEnum.CORRECT)

    response = world.client_as(MALLORY).request(
        "DELETE", "/v1/feedback", json=_delete_body(world.alice_conversation.id, world.alice_entry.id)
    )

    assert response.status_code == 403
    assert world.alice_conversation.history[0].user_mark is not None
    assert world.alice_entry.id in world.feedback
    assert world.service_calls == []


def test_delete_own_conversation_with_foreign_feedback_id_is_rejected(world):
    world.mallory_conversation.history[0].user_mark = UserMark(mark=MarkEnum.CORRECT)
    world.alice_conversation.history[0].user_mark = UserMark(mark=MarkEnum.CORRECT)

    response = world.client_as(MALLORY).request(
        "DELETE", "/v1/feedback", json=_delete_body(world.mallory_conversation.id, world.alice_entry.id)
    )

    assert response.status_code == 404
    assert world.alice_entry.id in world.feedback
    assert world.alice_conversation.history[0].user_mark is not None
    assert world.mallory_conversation.history[0].user_mark is not None
    assert "remove" not in world.service_calls


def test_post_and_delete_missing_conversation_return_404(world):
    missing = str(uuid.uuid4())
    client = world.client_as(ALICE)

    post_response = client.post("/v1/feedback", json=_post_body(missing))
    delete_response = client.request("DELETE", "/v1/feedback", json=_delete_body(missing, world.alice_entry.id))

    assert post_response.status_code == 404
    assert delete_response.status_code == 404
    assert world.saved_ids == []
    assert world.alice_entry.id in world.feedback


def test_owner_post_then_delete_work_as_today(world):
    client = world.client_as(ALICE)

    post_response = client.post("/v1/feedback", json=_post_body(world.alice_conversation.id))

    assert post_response.status_code == 200
    feedback_id = post_response.json()["id"]
    assert feedback_id in world.feedback
    assert world.alice_conversation.history[0].user_mark.mark == MarkEnum.WRONG

    delete_response = client.request(
        "DELETE", "/v1/feedback", json=_delete_body(world.alice_conversation.id, feedback_id)
    )

    assert delete_response.status_code == 200
    assert delete_response.json() == {"message": "Specified feedback entry removed"}
    assert feedback_id not in world.feedback
    assert world.alice_conversation.history[0].user_mark is None


def test_admin_can_delete_feedback_on_foreign_conversation(world):
    world.alice_conversation.history[0].user_mark = UserMark(mark=MarkEnum.CORRECT)

    response = world.client_as(ADMIN, is_admin=True).request(
        "DELETE", "/v1/feedback", json=_delete_body(world.alice_conversation.id, world.alice_entry.id)
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Specified feedback entry removed"}
    assert world.alice_entry.id not in world.feedback
    assert world.alice_conversation.history[0].user_mark is None


def test_admin_delete_with_mismatched_conversation_and_feedback_is_rejected(world):
    world.alice_conversation.history[0].user_mark = UserMark(mark=MarkEnum.CORRECT)

    response = world.client_as(ADMIN, is_admin=True).request(
        "DELETE", "/v1/feedback", json=_delete_body(world.mallory_conversation.id, world.alice_entry.id)
    )

    assert response.status_code == 404
    assert world.alice_entry.id in world.feedback
    assert world.alice_conversation.history[0].user_mark is not None
    assert world.service_calls == []


def test_auditor_cannot_delete_feedback_on_foreign_conversation(world):
    response = world.client_as(AUDITOR, is_auditor=True).request(
        "DELETE", "/v1/feedback", json=_delete_body(world.alice_conversation.id, world.alice_entry.id)
    )

    assert response.status_code == 403
    assert world.alice_entry.id in world.feedback
    assert world.service_calls == []


def test_admin_cannot_write_on_foreign_conversation(world):
    response = world.client_as(ADMIN, is_admin=True).post("/v1/feedback", json=_post_body(world.alice_conversation.id))

    assert response.status_code == 403
    assert world.alice_conversation.history[0].user_mark is None
    assert world.saved_ids == []
