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

"""Headroom savings travel from the chat turn onto the persisted assistant message."""

from unittest.mock import patch

from codemie.core.headroom_savings import HeadroomSavings
from codemie.core.models import TokensUsage
from codemie.rest_api.models.conversation import ChatTurnData, Conversation, GeneratedMessage

SAVINGS = HeadroomSavings(
    tokens_original=1000, tokens_compacted=400, tokens_saved=600, mode="sidecar", strategy="balanced"
)


def _turn(headroom=None) -> ChatTurnData:
    return ChatTurnData(
        user_query="q",
        user_query_raw="q",
        assistant_id="a1",
        assistant_response="r",
        thoughts=[],
        history_index=0,
        time_elapsed=1.0,
        input_tokens=1,
        output_tokens=1,
        file_names=[],
        money_spent=0.0,
        headroom=headroom,
    )


def test_turn_savings_are_copied_to_the_assistant_message_only():
    with patch("codemie.service.llm_service.llm_service.llm_service"):
        user_message, assistant_message = Conversation._build_chat_history_messages(_turn(SAVINGS))

    assert assistant_message.headroom == SAVINGS
    assert user_message.headroom is None


def test_turn_without_savings_has_no_headroom_on_the_message():
    with patch("codemie.service.llm_service.llm_service.llm_service"):
        _, assistant_message = Conversation._build_chat_history_messages(_turn())

    assert assistant_message.headroom is None


def test_message_savings_survive_a_json_round_trip():
    message = GeneratedMessage(role="Assistant", message="x", headroom=SAVINGS)

    restored = GeneratedMessage.model_validate(message.model_dump(mode="json"))

    assert restored.headroom == SAVINGS


def test_tokens_usage_defaults_to_no_headroom():
    assert TokensUsage(input_tokens=1, output_tokens=1, money_spent=0.0).headroom is None
