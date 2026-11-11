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

"""Run attributes of a chat are fixed by the first turn; later turns keep them or are rejected with 422."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.routers.assistant._ask import _ask_assistant
from codemie.rest_api.routers.utils import validate_run_attributes_and_raise
from codemie.rest_api.security.user import User

FIND_CONVERSATION = "codemie.rest_api.routers.utils.Conversation.find_by_id"
STORED = {"com.epam.gen_ai.service.work_item.id": "ABC-123"}


def _user(user_id: str = "owner") -> MagicMock:
    return MagicMock(spec=User, id=user_id)


def _conversation(history: list, attributes: dict[str, str] | None = None) -> Conversation:
    conversation = Conversation(id="conv-1", conversation_id="conv-1", user_id="owner")
    conversation.history = history
    conversation.attributes = attributes
    return conversation


class TestValidateRunAttributesAndRaise:
    @pytest.mark.parametrize("conversation", [None, _conversation(history=[], attributes={"team": "old"})])
    def test_missing_or_empty_conversation_keeps_request_attributes(self, conversation):
        request = AssistantChatRequest(text="hi", conversation_id="conv-1", attributes=STORED)

        with patch(FIND_CONVERSATION, return_value=conversation):
            validate_run_attributes_and_raise(request, _user())

        assert request.attributes == STORED

    def test_request_without_attributes_does_not_load_the_conversation(self):
        request = AssistantChatRequest(text="hi", conversation_id="conv-1")

        with patch(FIND_CONVERSATION) as find_conversation:
            validate_run_attributes_and_raise(request, _user())

        find_conversation.assert_not_called()
        assert request.attributes is None

    def test_save_history_false_ignores_different_attributes_without_loading_the_conversation(self):
        request = AssistantChatRequest(
            text="hi", conversation_id="conv-1", attributes={"team": "core"}, save_history=False
        )

        with patch(FIND_CONVERSATION, return_value=_conversation(history=[MagicMock()], attributes=STORED)) as find:
            validate_run_attributes_and_raise(request, _user())

        find.assert_not_called()
        assert request.attributes == {"team": "core"}

    def test_conversation_with_history_accepts_identical_attributes(self):
        request = AssistantChatRequest(text="hi", conversation_id="conv-1", attributes=STORED)

        with patch(FIND_CONVERSATION, return_value=_conversation(history=[MagicMock()], attributes=STORED)):
            validate_run_attributes_and_raise(request, _user())

        assert request.attributes == STORED

    @pytest.mark.parametrize("history", [[], [MagicMock()]])
    def test_conversation_the_caller_cannot_read_drops_request_attributes(self, history):
        request = AssistantChatRequest(text="hi", conversation_id="conv-1", attributes={"team": "core"})

        with patch(FIND_CONVERSATION, return_value=_conversation(history=history, attributes=STORED)):
            validate_run_attributes_and_raise(request, _user("intruder"))

        assert request.attributes is None

    @pytest.mark.parametrize("stored", [STORED, None])
    def test_conversation_with_history_rejects_different_attributes(self, stored):
        request = AssistantChatRequest(text="hi", conversation_id="conv-1", attributes={"team": "core"})

        with patch(FIND_CONVERSATION, return_value=_conversation(history=[MagicMock()], attributes=stored)):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                validate_run_attributes_and_raise(request, _user())

        assert exc_info.value.code == 422
        assert exc_info.value.message == "Run attributes cannot be changed"


class _PreflightPassed(Exception):
    pass


@pytest.mark.parametrize("history", [[], [MagicMock()]])
@pytest.mark.parametrize("sent", [{"team": "core"}, None])
@patch("codemie.rest_api.routers.assistant._ask.GuardrailService.apply_guardrails_for_entity")
@patch("codemie.rest_api.routers.assistant._ask._validate_remote_entities_and_raise")
@patch("codemie.rest_api.routers.assistant._ask.check_user_can_access_assistant")
def test_conversation_of_another_user_neither_rejects_nor_takes_attributes(_access, _remote, guardrails, sent, history):
    guardrails.side_effect = _PreflightPassed
    assistant = MagicMock(spec=Assistant, id="assistant-1", project="proj", llm_model_type=None)
    request = AssistantChatRequest(text="hi", conversation_id="conv-1", attributes=sent)
    raw_request = MagicMock()
    raw_request.state.uuid = "request-1"

    with patch(FIND_CONVERSATION, return_value=_conversation(history=history, attributes=STORED)):
        with pytest.raises(_PreflightPassed):
            _ask_assistant(assistant, raw_request, request, _user("intruder"), MagicMock())

    assert request.attributes is None


@patch("codemie.rest_api.routers.assistant._ask._save_error")
@patch("codemie.rest_api.routers.assistant._ask.request_summary_manager.create_request_summary")
@patch("codemie.rest_api.routers.assistant._ask.get_request_handler")
@patch("codemie.rest_api.routers.assistant._ask._validate_remote_entities_and_raise")
@patch("codemie.rest_api.routers.assistant._ask.check_user_can_access_assistant")
def test_changed_attributes_are_rejected_before_anything_is_written(
    _access, _remote, get_handler, create_summary, save_error
):
    assistant = MagicMock(spec=Assistant, id="assistant-1", project="proj", llm_model_type=None)
    request = AssistantChatRequest(text="hi", conversation_id="conv-1", attributes={"team": "core"})
    raw_request = MagicMock()
    raw_request.state.uuid = "request-1"

    with patch(FIND_CONVERSATION, return_value=_conversation(history=[MagicMock()], attributes=STORED)):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            _ask_assistant(assistant, raw_request, request, _user(), MagicMock())

    assert exc_info.value.code == 422
    get_handler.assert_not_called()
    create_summary.assert_not_called()
    save_error.assert_not_called()
