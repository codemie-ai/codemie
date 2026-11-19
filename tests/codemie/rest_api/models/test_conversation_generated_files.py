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

"""Files published during a turn ride on the assistant message's `file_names`, never on the user's."""

from unittest.mock import patch

from codemie.core.constants import ChatRole
from codemie.core.utils import build_unique_file_objects
from codemie.rest_api.models.conversation import ChatTurnData, Conversation, GeneratedMessage


def _turn(**overrides) -> ChatTurnData:
    defaults = {
        "user_query": "hi",
        "user_query_raw": "hi",
        "assistant_id": "a1",
        "assistant_response": "hello",
        "thoughts": [],
        "history_index": 0,
        "time_elapsed": 1.0,
        "input_tokens": 1,
        "output_tokens": 1,
        "file_names": ["uploaded-token"],
        "money_spent": 0.0,
    }
    defaults.update(overrides)
    return ChatTurnData(**defaults)


def test_generated_files_land_on_the_assistant_message_only():
    user_message, assistant_message = Conversation._build_chat_history_messages(
        _turn(generated_file_names=["artifact-token"])
    )

    assert user_message.file_names == ["uploaded-token"]
    assert assistant_message.file_names == ["artifact-token"]


def test_a_turn_without_generated_files_leaves_the_assistant_message_empty():
    _, assistant_message = Conversation._build_chat_history_messages(_turn())

    assert not assistant_message.file_names


def test_generated_files_of_earlier_turns_are_not_handed_to_the_model_as_uploads():
    history = [
        GeneratedMessage(role=ChatRole.USER, message="q", history_index=0, file_names=["uploaded-token"]),
        GeneratedMessage(role=ChatRole.ASSISTANT, message="a", history_index=0, file_names=["artifact-token"]),
    ]
    seen: list[str] = []

    with (
        patch("codemie.rest_api.models.conversation.Conversation.find_by_id") as find,
        patch("codemie.core.utils._process_file_names_to_objects", side_effect=lambda names, _: seen.extend(names)),
    ):
        find.return_value.history = history
        build_unique_file_objects(conversation_id="conv-1")

    assert seen == ["uploaded-token"]
