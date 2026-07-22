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

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.interactive import InteractiveRequest, InteractiveResponse
from codemie.rest_api.models.conversation import GeneratedMessage
from codemie.service.conversation.interactive_intake import (
    materialize_interactive_message_text,
    materialize_interactive_request_text,
    validate_interactive_intake,
)


def _history_with_request(request_id="r1", answered=False):
    request = InteractiveRequest(request_id=request_id, surface=[{"type": "button", "id": "ok", "label": "OK"}])
    history = [GeneratedMessage(role="Assistant", message="", history_index=0, interactive_request=request)]
    if answered:
        history.append(
            GeneratedMessage(
                role="User",
                message="✓ OK",
                history_index=1,
                interactive_response=InteractiveResponse(
                    request_id=request_id, kind="action", payload={"action": "ok"}
                ),
            )
        )
    return history


def test_unknown_request_id_rejected():
    response = InteractiveResponse(request_id="nope", kind="action", payload={"action": "ok"})
    with pytest.raises(ExtendedHTTPException):
        validate_interactive_intake(_history_with_request(), response)


def test_duplicate_answer_rejected():
    response = InteractiveResponse(request_id="r1", kind="action", payload={"action": "ok"})
    with pytest.raises(ExtendedHTTPException):
        validate_interactive_intake(_history_with_request(answered=True), response)


def test_valid_answer_accepted():
    response = InteractiveResponse(request_id="r1", kind="action", payload={"action": "ok"})
    request = validate_interactive_intake(_history_with_request(), response)
    assert request.request_id == "r1"


def test_resubmit_allowed_when_replacing_answered_turn():
    # Re-answering a form mirrors editing the previous user request: the stored
    # response at the turn being replaced (history_index >= replacing index) is
    # being overwritten, so it must not count as "already answered".
    response = InteractiveResponse(request_id="r1", kind="action", payload={"action": "ok"})
    history = _history_with_request(answered=True)  # request @0, response @1
    request = validate_interactive_intake(history, response, replacing_history_index=1)
    assert request.request_id == "r1"


def test_resubmit_still_rejects_genuinely_earlier_answer():
    # A response at a turn BEFORE the one being replaced is a real prior answer.
    response = InteractiveResponse(request_id="r1", kind="action", payload={"action": "ok"})
    history = _history_with_request(answered=True)  # response @1
    with pytest.raises(ExtendedHTTPException):
        validate_interactive_intake(history, response, replacing_history_index=5)


def test_resubmit_low_history_index_cannot_bypass_already_answered():
    # Attack: the answer is stored at index 1, but the client sends a low replacing
    # index (0) hoping message_index >= 0 marks it "being replaced". Strict equality
    # (== not >=) must keep rejecting it as already answered.
    response = InteractiveResponse(request_id="r1", kind="action", payload={"action": "ok"})
    history = _history_with_request(answered=True)  # response @1
    with pytest.raises(ExtendedHTTPException):
        validate_interactive_intake(history, response, replacing_history_index=0)


def test_server_side_revalidation_rejects_invalid_form():
    request = InteractiveRequest(
        request_id="r2",
        surface=[
            {
                "type": "text_field",
                "id": "email",
                "label": "Email",
                "validation": {"required": True, "email": True},
            },
            {"type": "button", "id": "submit", "label": "Submit"},
        ],
    )
    history = [GeneratedMessage(role="Assistant", message="", interactive_request=request)]
    response = InteractiveResponse(request_id="r2", kind="form", payload={"values": {"email": "not-an-email"}})
    with pytest.raises(ExtendedHTTPException):
        validate_interactive_intake(history, response)


def test_materialized_text_contains_display_and_payload():
    response = InteractiveResponse(request_id="r1", kind="action", payload={"action": "approve"})
    text = materialize_interactive_message_text("✓ Approve", response)
    assert "✓ Approve" in text
    assert "r1" in text
    assert '"action": "approve"' in text


def test_to_chat_history_materializes_interactive_response():
    from codemie.rest_api.models.conversation import Conversation

    history = _history_with_request(answered=True)
    conversation = Conversation(id="c1", conversation_id="c1", user_id="u1", history=history, assistant_ids=["a1"])
    chat_messages = conversation.to_chat_history()
    user_texts = [m.message for m in chat_messages if m.role.value == "User"]
    assert any("Structured response to interactive request r1" in t for t in user_texts)
    assert any('"action": "ok"' in t for t in user_texts)


def test_materialize_request_replays_surface_for_resume():
    # CR-003: the assistant's own surface (labels/options) is materialized so a resumed
    # turn keeps the context of its own question, not just the user's structured answer.
    request = InteractiveRequest(
        request_id="rq1",
        surface=[{"type": "button", "id": "approve", "label": "Approve"}],
    )
    text = materialize_interactive_request_text("", request)
    assert "rq1" in text
    assert "approve" in text
    assert "Approve" in text


def test_materialize_request_keeps_preamble_text():
    request = InteractiveRequest(request_id="rq2", surface=[{"type": "button", "id": "ok", "label": "OK"}])
    text = materialize_interactive_request_text("Please choose:", request)
    assert text.startswith("Please choose:")
    assert "rq2" in text
