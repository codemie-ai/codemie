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


"""Run attributes on the IDE virtual inference route follow the chat contract without probing unreadable chats."""

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import VirtualIdeChatRequest
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.routers.ide import inference_virtual
from codemie.rest_api.security.user import User

FIND_CONVERSATION = "codemie.rest_api.routers.utils.Conversation.find_by_id"
IDE = "codemie.rest_api.routers.ide"
STORED = {"com.epam.gen_ai.service.work_item.id": "ABC-123"}


def _conversation() -> Conversation:
    conversation = Conversation(id="conv-1", conversation_id="conv-1", user_id="owner")
    conversation.history = [MagicMock()]
    conversation.attributes = STORED
    return conversation


def _raw_request() -> MagicMock:
    raw_request = MagicMock()
    raw_request.state.uuid = "request-1"
    raw_request.state.wait_for_disconnect = AsyncMock()
    return raw_request


def _request(attributes: dict[str, str] | None) -> VirtualIdeChatRequest:
    return VirtualIdeChatRequest(
        text="hi", conversation_id="conv-1", attributes=attributes, virtual_assistant_id="va-1", llm_model="gpt-4"
    )


@pytest.mark.asyncio
@patch(f"{IDE}.get_request_handler")
@patch(f"{IDE}.request_summary_manager.create_request_summary")
@patch(f"{IDE}.assistant_user_interaction_service.record_usage")
async def test_changed_attributes_are_rejected_before_anything_is_written(record_usage, create_summary, get_handler):
    with patch(FIND_CONVERSATION, return_value=_conversation()):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await inference_virtual(_raw_request(), MagicMock(), _request({"team": "core"}), User(id="owner"))

    assert exc_info.value.code == 422
    record_usage.assert_not_called()
    create_summary.assert_not_called()
    get_handler.assert_not_called()


@pytest.mark.asyncio
@patch(f"{IDE}.get_request_handler")
@patch(f"{IDE}.request_summary_manager.create_request_summary")
@patch(f"{IDE}.assistant_user_interaction_service.record_usage")
async def test_later_message_without_attributes_skips_the_check(_record_usage, _create_summary, get_handler):
    request = _request(None)

    with patch(FIND_CONVERSATION) as find_conversation:
        await inference_virtual(_raw_request(), MagicMock(), request, User(id="owner"))

    find_conversation.assert_not_called()
    assert get_handler.return_value.process_request.call_args.args[0].attributes is None


@pytest.mark.asyncio
async def test_attributes_check_runs_off_the_event_loop():
    loop_thread = threading.get_ident()
    lookup_threads = []

    def find_conversation(_conversation_id):
        lookup_threads.append(threading.get_ident())
        return _conversation()

    with patch(FIND_CONVERSATION, side_effect=find_conversation):
        with pytest.raises(ExtendedHTTPException):
            await inference_virtual(_raw_request(), MagicMock(), _request({"team": "core"}), User(id="owner"))

    assert lookup_threads
    assert loop_thread not in lookup_threads


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", [{"team": "core"}, STORED, None])
@patch(f"{IDE}.get_request_handler")
@patch(f"{IDE}.request_summary_manager.create_request_summary")
@patch(f"{IDE}.assistant_user_interaction_service.record_usage")
async def test_conversation_of_another_user_neither_rejects_nor_takes_attributes(
    _record_usage, _create_summary, get_handler, sent
):
    request = _request(sent)

    with patch(FIND_CONVERSATION, return_value=_conversation()):
        await inference_virtual(_raw_request(), MagicMock(), request, User(id="intruder"))

    get_handler.return_value.process_request.assert_called_once()
    assert request.attributes is None
